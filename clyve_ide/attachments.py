"""Attachment handling: store uploads, extract text, prepare vision parts.

Supported without any dependency:
    images (png/jpg/webp/gif)  -> sent to the model as image_url parts
    text/code/json/csv/md     -> inlined as text
    .docx / .xlsx             -> unzipped + XML parsed
    .pdf                      -> pypdf if installed, else a small built-in extractor

Anything else is reported back to the agent, which can then use run_shell.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import zipfile
from pathlib import Path

MAX_FILES = 5
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_DOC_BYTES = 25 * 1024 * 1024
TEXT_PER_FILE = 30_000
TEXT_TOTAL = 90_000

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".ini", ".cfg", ".conf", ".env",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".xml",
            ".yml", ".yaml", ".sql", ".sh", ".ps1", ".bat", ".php", ".go", ".rs",
            ".java", ".kt", ".c", ".h", ".cpp", ".cs", ".rb", ".toml", ".lock"}

MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}


def classify(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext == ".xlsx":
        return "xlsx"
    if ext in TEXT_EXT:
        return "text"
    return "binary"


def safe_name(name: str) -> str:
    base = Path(name).name.replace("\\", "_").replace("/", "_")
    base = re.sub(r"[^\w.\- ()\[\]]+", "_", base).strip() or "file"
    return base[:120]


def decode_data_url(data_url: str) -> bytes:
    if "," not in data_url:
        raise ValueError("payload bukan data URL")
    head, b64 = data_url.split(",", 1)
    if "base64" not in head:
        raise ValueError("hanya data URL base64 yang didukung")
    return base64.b64decode(b64, validate=False)


def uploads_dir() -> Path:
    """Folder lampiran di config app, BUKAN di workspace.

    Dulu file disimpan di <workspace>/.clyve/uploads sehingga muncul sebagai
    perubahan git (dan diff biner sampah di panel Changes). Simpan di config
    user supaya workspace tetap bersih.
    """
    try:
        from .config import config_dir
        base = config_dir() / "uploads"
    except Exception:  # noqa: BLE001
        base = Path.home() / ".clyve" / "uploads"
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_upload(workspace: Path, name: str, data: bytes) -> dict:
    kind = classify(name)
    if kind == "image" and len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"gambar terlalu besar ({len(data)//1024} KB). Maks {MAX_IMAGE_BYTES//1024//1024} MB.")
    if len(data) > MAX_DOC_BYTES:
        raise ValueError(f"file terlalu besar ({len(data)//1024} KB). Maks {MAX_DOC_BYTES//1024//1024} MB.")
    folder = uploads_dir()
    import time
    target = folder / f"{int(time.time())}-{safe_name(name)}"
    n = 1
    while target.exists():
        target = folder / f"{int(time.time())}-{n}-{safe_name(name)}"
        n += 1
    target.write_bytes(data)
    return {"path": str(target), "name": name, "kind": kind, "size": len(data)}


# --------------------------------------------------------------------- text
def _clip(text: str, limit: int = TEXT_PER_FILE) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (dipotong, total {len(text)} karakter)"


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t").replace("<w:br/>", "\n")
    text = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def xlsx_text(path: Path, max_rows: int = 400) -> str:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            for si in re.findall(r"<si>(.*?)</si>", sx, re.S):
                shared.append("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
        sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for sheet in sheets[:3]:
            sx = z.read(sheet).decode("utf-8", "replace")
            out.append(f"--- {sheet} ---")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sx, re.S)[:max_rows]:
                cells = []
                for c in re.findall(r"<c[^>]*?(?:/>|>.*?</c>)", row, re.S):
                    v = re.search(r"<v>(.*?)</v>", c, re.S)
                    if not v:
                        isx = re.search(r"<is>.*?<t[^>]*>(.*?)</t>", c, re.S)
                        cells.append(isx.group(1) if isx else "")
                        continue
                    val = v.group(1)
                    if 't="s"' in c:
                        try:
                            val = shared[int(val)]
                        except Exception:  # noqa: BLE001
                            pass
                    cells.append(val)
                out.append(" | ".join(cells))
        return "\n".join(out)


def pdf_text_naive(path: Path) -> str:
    """Best-effort extraction: decompress content streams and read text operators."""
    import zlib
    raw = path.read_bytes()
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except Exception:  # noqa: BLE001
            pass  # uncompressed content stream: use it as-is
        line = []
        for tok in re.finditer(
                rb"\((?P<s>(?:\\.|[^\\()])*)\)\s*Tj|\[(?P<arr>.*?)\]\s*TJ|(?P<nl>T\*|TD|Td)", data, re.S):
            if tok.group("nl"):
                if line:
                    out.append("".join(line)); line = []
                continue
            if tok.group("s") is not None:
                s = tok.group("s")
            else:
                s = b"".join(re.findall(rb"\((?:\\.|[^\\()])*\)", tok.group("arr") or b""))
                s = s.replace(b")(", b"")
                s = s.replace(b"(", b"").replace(b")", b"")
            s = re.sub(rb"\\([nrt])", b" ", s)
            s = re.sub(rb"\\[()\\]", b"", s)
            line.append(s.decode("latin-1", "replace"))
        if line:
            out.append("".join(line))
    return "\n".join(x.strip() for x in out if x.strip())


def pdf_text(path: Path) -> tuple[str, str]:
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages[:80]]
        txt = "\n\n".join(pages).strip()
        return txt, "pypdf"
    except Exception:  # noqa: BLE001
        pass
    try:
        txt = pdf_text_naive(path).strip()
        return txt, "builtin"
    except Exception as e:  # noqa: BLE001
        return "", f"gagal: {e}"


# ------------------------------------------------------------------ payload
def build_context(attachments: list[dict]) -> tuple[str, list[str]]:
    """Return (text_block, image_paths). Only attachments that exist are used."""
    docs, images = [], []
    budget = TEXT_TOTAL
    for a in attachments or []:
        p = Path(a.get("path") or "")
        if not p.is_file():
            docs.append(f"[{a.get('name')}] file tidak ditemukan: {p}")
            continue
        kind = a.get("kind") or classify(p.name)
        if kind == "image":
            images.append(str(p))
            docs.append(f"[{a.get('name')}] gambar dilampirkan ({p.stat().st_size//1024} KB).")
            continue
        try:
            if kind == "text":
                text = _clip(p.read_text("utf-8", errors="replace"))
            elif kind == "pdf":
                text, how = pdf_text(p)
                if not text:
                    docs.append(f"[{a.get('name')}] PDF tidak bisa diekstrak teksnya ({how}). "
                                "Pakai run_shell + pypdf, atau kirim screenshot halamannya.")
                    continue
            elif kind == "docx":
                text = _clip(docx_text(p))
            elif kind == "xlsx":
                text = _clip(xlsx_text(p))
            else:
                docs.append(f"[{a.get('name')}] tipe file tidak bisa dibaca langsung. "
                            "Gunakan run_shell kalau perlu diproses.")
                continue
        except Exception as e:  # noqa: BLE001
            docs.append(f"[{a.get('name')}] gagal dibaca: {type(e).__name__}: {e}")
            continue
        if budget <= 0:
            docs.append(f"[{a.get('name')}] dilewati: kuota teks lampiran habis.")
            continue
        text = text[:budget]
        budget -= len(text)
        docs.append(f"===== FILE: {a.get('name')} =====\n{text}\n===== END {a.get('name')} =====")
    if docs:
        docs.insert(0, "(Lampiran sudah dibaca dan isinya disertakan di bawah. "
                       "Tidak perlu mencari file aslinya di disk.)")
    return "\n\n".join(docs), images


def image_part(path: str) -> dict:
    p = Path(path)
    mime = MIME.get(p.suffix.lower(), "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def summarize(attachments: list[dict]) -> str:
    return json.dumps([{"name": a.get("name"), "kind": a.get("kind"),
                        "size": a.get("size")} for a in attachments or []], ensure_ascii=False)
