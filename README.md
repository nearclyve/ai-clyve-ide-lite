# AI Clyve IDE Lite

Aplikasi coding agent desktop yang jalan lokal di komputer kamu, terhubung ke
[Clyve AI Gateway](https://ai.clyve.id). Bisa baca/tulis file di folder mana pun,
jalankan terminal, baca PDF/gambar, dan ngoding bareng model Clyve.

- **Desktop native (Qt/PySide6)** — bukan tab browser.
- **Akses folder luas** — pilih workspace, atau aktifkan akses seluruh disk.
- **Terminal sungguhan** — `run_shell` (cmd.exe di Windows, bash di Linux/macOS).
- **Chat list** — sidebar riwayat chat, `+` chat baru, `x` hapus.
- **Baca file, gambar, PDF, docx/xlsx** — lampiran dibaca otomatis.
- **Upload maksimal 5 file per pesan** (gambar <= 8 MB, dokumen <= 25 MB).
- **BYOK** — user isi API key dari akun ai.clyve.id, disimpan lokal.
- **Endpoint terkunci** ke `https://ai.clyve.id/v1`, model default `ai-clyve-1`.

## Install (Windows)

1. Unduh `AIClyveIDELite-Setup-<versi>.exe` dari tab **Releases** repo ini.
2. Jalankan installer. Pemasangan **per-user** (tanpa admin/UAC), muncul di
   **Apps & Features**, plus shortcut **Start Menu** dan Desktop.
3. Buka dari Start Menu. Tidak ada window terminal yang ikut terbuka
   (build memakai PyInstaller `--windowed`).

Update tidak perlu download manual:

- App cek rilis GitHub saat dibuka. Kalau ada versi lebih baru, muncul dialog
  berisi catatan rilis -> **Update sekarang** -> installer diunduh, app ditutup,
  update dipasang senyap, lalu app dibuka lagi.
- Cek manual lewat tombol **Cek update** di topbar.
- Nomor versi terpasang tampil di sidebar (`v1.1.0`).
- Update tidak menghapus settings: API key + riwayat chat ada di
  `%APPDATA%\clyve-ide`, di luar folder instalasi.

## Rilis versi baru (maintainer)

Versi hanya didefinisikan di `clyve_ide/version.py`. Alur rilis:

```bash
# 1. naikkan __version__ di clyve_ide/version.py
# 2. commit
git commit -am "chore(release): v1.2.0"
# 3. push tag -> CI build installer + portable zip, lalu publish GitHub Release
git tag v1.2.0 && git push origin main --tags
```

CI (`.github/workflows/build-windows-exe.yml`) menghasilkan:

- `AIClyveIDELite-Setup-<versi>.exe` — installer Inno Setup (yang dipakai updater)
- `AIClyveIDELite-<versi>-portable-win64.zip` — versi portable tanpa install

Updater mengambil **release terbaru non-draft** dari `api.github.com`, jadi
setelah tag di-push tidak ada langkah manual lain.

## Test

```bash
python -m unittest discover -s tests -v
```

Bagian Qt otomatis di-skip kalau PySide6 tidak terpasang. CI menjalankan test
yang sama sebelum build Windows.

## Build manual

Windows: `build_exe.ps1` (butuh Python 3.10+; installer butuh Inno Setup 6:
`winget install JRSoftware.InnoSetup`). Hasil: `dist/AIClyveIDELite/` (app) dan
`dist/AIClyveIDELite-Setup-<versi>.exe` (installer). Development: `python app.py`.

## Fitur

- **Desktop native (Qt)** — jendela aplikasi asli, widget Qt, bukan tab browser dan bukan
  webview. Semua render di sisi app; tidak buka port HTTP kecuali lo pakai `--ui web`.
- **Mode kerja** — `Auto` (langsung jalan), `Review` (tiap tulis/hapus/shell minta
  persetujuan, lengkap dengan diff berwarna), `Plan` (read-only, agent cuma menyusun rencana).
- **Checkpoint / rollback** — setiap perubahan file disnapshot otomatis; satu klik untuk
  mengembalikan isi file ke kondisi sebelum agent menyentuhnya.
- **Riwayat chat** — daftar percakapan di sidebar (tersimpan di `chats/*.json`), klik untuk buka,
  `×` untuk hapus, `+` untuk percakapan baru. Riwayat ikut tersimpan saat app ditutup.
- **Stop yang benar-benar berhenti** — tombol Stop memutus request ke gateway (bukan cuma
  berhenti menampilkan), chat langsung bisa dipakai lagi tanpa reload.
- **Chat UI** — stream jawaban token per token; langkah agent dikelompokkan dalam blok
  `Working · N` (satu baris per tool: ikon, argumen, durasi, output mentah bisa dibuka).
- **Antrian pesan** — kirim pesan saat agent masih membalas, pesan masuk antrian
  (chip di atas input, klik `×` untuk batal) dan terkirim otomatis begitu giliran selesai.
- **Panel Changes** — status git + diff per file dengan nomor baris, commit message langsung
  dari app (`git add -A` + `git commit`). Tombol di topbar atau `Ctrl+Shift+G`.
- **Terminal interaktif** — panel bawah dengan shell hidup (cwd ikut workspace), `Ctrl+J`.
- **API key milik user sendiri (BYOK)** — dibuka pertama kali, wajib isi key dari akun `ai.clyve.id`.
  Key disimpan lokal di `%APPDATA%\clyve-ide\settings.json`, tidak pernah dikirim ke mana pun selain gateway Clyve.
- **Workspace picker** — pilih folder kerja lewat dialog; semua akses file dibatasi di folder itu.
- **Permission always allow** — tidak ada prompt konfirmasi. Agent langsung eksekusi.
- **13 tool**: file read/write/edit, list, search regex, shell, SQLite, HTTP, open folder, open URL, system info.
- **Zero dependency** — hanya stdlib Python. Build `.exe` pun ringan.

## Cara jalan (Windows)

Prasyarat: **Python 3.10+** ([python.org](https://www.python.org/downloads/) — centang *Add python.exe to PATH*).

### Cara 1 — paling cepat

```powershell
git clone https://github.com/nearilts/ai-clyve-ide.git
cd ai-clyve-ide
python -m pip install PySide6 pypdf
python app.py
```

Jendela desktop Qt terbuka (bukan browser, bukan webview). Kalau PySide6 belum ada,
app otomatis jatuh ke UI web: `python app.py --ui web`.

Masukkan API key Clyve, pilih folder workspace, mulai chat.

### Cara 2 — pakai run.bat

Double-click `run.bat`. Script itu bikin `.venv` sekali, lalu jalankan app.

### Cara 3 — build .exe sendiri

```powershell
.\build_exe.ps1
```

Hasil: `dist\AIClyveIDE.exe`. Double-click → server lokal start + browser terbuka.

> Build `.exe` harus di Windows; PyInstaller tidak bisa cross-compile dari Linux/macOS.

## Cara jalan (Linux/macOS)

```bash
python3 app.py            # atau: python3 app.py --port 8777
```

Sama persis. Tool `open_path` pakai `xdg-open`/`open`, `run_shell` pakai `bash`.

## Flag CLI

| Flag | Fungsi |
|---|---|
| `--ui qt\|web` | `qt` = desktop native (default), `web` = UI browser/webview |
| `--port N` | Port untuk UI web (default: acak) |
| `--mode auto\|webview\|app\|browser` | UI web: jenis jendela |
| `--browser` | UI web: paksa browser biasa |
| `--no-window` | UI web: server saja (debug) |
| `--workspace PATH` | Set workspace awal |
| `--key KEY` | Set API key (sekalian disimpan) |
| `--model ID` | Set model default |

## Mode kerja

| Mode | Perilaku |
|---|---|
| **Auto** | Agent langsung eksekusi semua tool, tanpa konfirmasi |
| **Review** | Tiap `write_file`/`edit_file`/`delete_path`/`run_shell` memunculkan dialog berisi diff (hijau/merah). Tolak → agent diberi tahu dan tidak menerapkan |
| **Plan** | Tool yang mengubah sistem dimatikan. Agent hanya membaca dan menyusun rencana langkah demi langkah |

## Checkpoint / rollback

Sebelum setiap perubahan file, app menyimpan salinan isi lamanya di
`<config>/checkpoints/<chat-id>/`. Tombol **Checkpoint / rollback** di sidebar menampilkan
daftar snapshot; pilih satu → file dikembalikan ke kondisi saat itu. File yang tadinya
belum ada akan dihapus saat rollback. Snapshot disimpan maksimal 120 per chat.

## Suara (STT) — termurah

Tombol **Mic** di samping input merekam suara, lalu transkripnya masuk ke kotak pesan.
Default provider **OpenRouter** dengan model `openai/gpt-4o-mini-transcribe:floor`
(suffix `:floor` = ambil provider termurah).

Harga terukur dari tes gue: **$0.00002875 per 2 detik ≈ $0.0009/menit**, sekitar
7x lebih murah dari `openai/whisper-1` ($0.006/menit).

| Model | Perkiraan harga | Catatan |
|---|---|---|
| `openai/gpt-4o-mini-transcribe:floor` | ~$0.0009/menit | default, termurah |
| `openai/gpt-4o-transcribe` | lebih mahal | akurasi lebih tinggi |
| `openai/whisper-1` | $0.006/menit | paling matang |

Setup: sidebar → **Pengaturan suara (STT)** → tempel API key OpenRouter (`sk-or-v1-...`).
Base URL dan model bisa diganti ke endpoint OpenAI-compatible lain (Groq, gateway sendiri).

Catatan teknis:
- Rekaman 16 kHz mono WAV, dikirim ke `POST /audio/transcriptions` (multipart).
- Kalau mikrofon tidak ada / QtMultimedia tidak tersedia, tombol Mic melaporkan error
  dan app tetap jalan.
- `clyve-stt` di gateway Clyve sekarang balas 429 (`insufficient_quota` di sisi OpenAI),
  jadi jangan pakai model itu sampai kredit gateway diisi.

## Distribusi terkunci (brand lock)

Untuk versi yang dibagikan ke user lain, taruh `brand.json` di root repo (atau di sebelah `.exe`
kalau build manual). Contoh ada di `brand.example.json`:

```json
{
  "app_name": "AI Clyve IDE Lite",
  "base_url": "https://ai.clyve.id/v1",
  "lock_base_url": true,
  "lock_model": false,
  "models": []
}
```

Efeknya:

- Nama aplikasi & judul jendela jadi `app_name`.
- `base_url` dikunci — layar setup hanya menampilkan "Server: ..." sebagai teks, tidak bisa diubah.
  Env `CLYVE_BASE_URL` dan `settings.json` user tidak bisa menimpanya.
- `models: []` → semua model gateway ditampilkan. Isi array (mis. `["deepseek-v4.1-flash"]`)
  untuk membatasi pilihan model.
- User hanya perlu tempel API key masing-masing.

`brand.json` masuk `.gitignore`; `brand.example.json` yang di-commit. Build script dan CI
otomatis menyalin example kalau `brand.json` belum ada, lalu memasukkannya ke dalam `.exe`.

## Lampiran: gambar, PDF, dokumen

Tombol 📎 (atau drag & drop ke area chat, atau Ctrl+V screenshot) melampirkan file ke pesan.
Isi file dibaca otomatis oleh app sebelum dikirim ke model:

| Tipe | Cara dibaca |
|---|---|
| `png/jpg/webp/gif/bmp` | dikirim sebagai gambar ke model (vision) |
| `pdf` | `pypdf` kalau terinstall, kalau tidak pakai extractor bawaan |
| `docx`, `xlsx` | di-unzip dan XML-nya diparse (teks + isi sheet) |
| `txt/md/json/csv/py/js/...` | dibaca sebagai teks |
| lainnya | dilaporkan ke agent, dia bisa proses lewat `run_shell` |

**Batasan**

- Maksimal **10 file per pesan** (UI dan server sama-sama menolak lebih).
- Gambar maks **8 MB** per file (base64 membengkak ~33%, jadi jangan kirim PNG 20 MB).
- Dokumen maks **25 MB** per file.
- Teks per file dipotong **30.000 karakter**, total lampiran **90.000 karakter** per pesan.
- Model wajib support vision untuk gambar. `deepseek-v4.1-flash`, `claude-*`, `gpt-*` bisa.
  Kalau model menolak gambar, app otomatis mengulang tanpa gambar dan memberi catatan.
- PDF hasil scan (gambar) tidak bisa diekstrak teksnya — kirim screenshot halamannya,
  atau minta agent jalankan OCR lewat `run_shell`.

## Izin akses folder

Defaultnya agent hanya boleh menyentuh workspace. Banyak kasus butuh folder lain
(misal `C:\laragon\etc\ssl`, folder project lain, `C:\xampp`):

- **Akses folder** di sidebar → `+ Folder` → pilih folder → langsung diizinkan
  (persisten, tersimpan di `settings.json`). Klik `×` untuk mencabut.
- **Akses seluruh disk** → toggle di sidebar → semua path di mesin ini boleh dibaca/ditulis.
  Pakai kalau memang mau bebas; risiko ikut naik.

Catatan penting: `run_shell` selalu punya akses ke seluruh OS (itu memang shell).
Sandbox di atas berlaku untuk tool file (`read_file`, `write_file`, `edit_file`,
`list_dir`, `search_files`, `delete_path`, `sqlite_query`). Jadi jangan anggap sandbox
sebagai pengganti kehati-hatian.

## Tool yang tersedia

| Tool | Fungsi |
|---|---|
| `read_file` | Baca file, output bernomor baris, ada offset/limit |
| `write_file` | Buat/timpa file (folder parent otomatis) |
| `edit_file` | Replace string persis — lebih hemat token dari tulis ulang |
| `make_dir` | Buat folder rekursif |
| `delete_path` | Hapus file/folder (tidak boleh workspace root) |
| `list_dir` | Isi folder + ukuran |
| `search_files` | Grep regex isi file, skip `.git`/`node_modules`/`.venv` |
| `run_shell` | Shell command (cmd.exe di Windows), bisa pip/npm/git |
| `sqlite_query` | Query SQLite; read-only kecuali `allow_write=true` |
| `http_request` | HTTP apa pun: method, url, header, body |
| `open_path` | Buka file/folder di Explorer/Finder |
| `open_url` | Buka URL di browser default |
| `system_info` | OS, Python, workspace, shell |

## Model

Default `ai-clyve-1` (bisa diganti lewat pemilih model). Di gateway Clyve yang sudah dites menghasilkan
tool call dengan benar: `deepseek-v4.1-flash`, `claude-sonnet-5-5`, `claude-opus-5-5`.
Hindari `gemini-3.8-flash` untuk agent loop (di tes non-stream tidak mengembalikan `tool_calls`).
Daftar model diambil live dari `/v1/models` setelah key dimasukkan.

## Keamanan — baca ini

Agent ini punya akses shell penuh. Itu memang tujuannya, tapi konsekuensinya nyata:

- Semua path file di-resolve dan **ditolak kalau keluar dari workspace**.
- Hard blocklist untuk pola katastrofik: `rm -rf /`, `format X:`, `del /s`, `diskpart`, `mkfs`, fork bomb.
  Ini guardrail, bukan sandbox.
- API key disimpan di config lokal user, bukan di repo, bukan di binary.
- Jangan jalankan agent di folder sistem atau folder berisi data yang tidak ada backup-nya.
- Kalau mau kontrol lebih ketat: jalankan di WSL2/Docker dengan folder project di-mount saja.

## Struktur

```
app.py              # launcher: pilih UI Qt (default) atau web
clyve_ide/
  config.py         # simpan API key / model / workspace
  tools.py          # 13 tool + skema JSON
  agent.py          # loop streaming, mode auto/review/plan, eksekusi tool
  checkpoints.py    # snapshot file + rollback
  attachments.py    # simpan upload, ekstrak teks (pdf/docx/xlsx), part vision
  qt_app.py         # window utama + chat + dialog (PySide6)
  ui/
    theme.py        # palet + QSS tunggal
    activity.py     # blok "Working · N" (baris tool update in-place)
    changes.py      # panel Changes: status git + diff + commit
    terminal.py     # terminal interaktif (QProcess)
  gitutil.py        # status/diff/commit via subprocess git
  updater.py        # cek rilis GitHub + unduh installer
  version.py        # satu sumber versi (dipakai app, updater, CI)
  server.py         # HTTP server + REST + SSE (UI web)
  desktop.py        # jendela web: pywebview -> --app -> browser
tests/              # unittest inti (version, updater, gitutil, antrian chat)
installer/          # script Inno Setup
scripts/            # generator version resource Windows
web/
  index.html        # UI web (opsional)
  styles.css
  app.js
build_exe.ps1       # PyInstaller onefile
run.bat             # launcher Windows
```

## Troubleshooting

- **`python` cuma buka Microsoft Store / Python tidak dikenali** (umum kalau pakai Laragon):
  Laragon punya Python sendiri, tapi tidak masuk PATH. Cek dulu:
  ```powershell
  dir C:\laragon\bin\python
  ```
  Lalu jalankan app langsung dengan Python Laragon:
  ```powershell
  C:\laragon\bin\python\python-3.11\python.exe -m venv .venv
  .\.venv\Scripts\python.exe app.py
  ```
  `run.bat` dan `build_exe.ps1` versi terbaru sudah otomatis mencari Python Laragon
  (`C:\laragon\bin\python\python-*\python.exe`) sebelum menyerah.
- **Matikan alias Store** (opsional, sekali saja): Settings → Apps → Advanced app settings →
  App execution aliases → matikan `python.exe` dan `python3.exe`.
- **Muncul sebagai tab browser, bukan jendela app**: `pip install pywebview` lalu jalankan lagi.
  Kalau masih begitu, cek `python app.py --mode app` (pakai Edge/Chrome `--app`).
- **Jendela putih kosong di Windows**: WebView2 Runtime belum ada. Install dari
  https://developer.microsoft.com/microsoft-edge/webview2/ (biasanya sudah ada di Win10/11 baru),
  atau pakai `--mode app`.
- **Browser tidak terbuka**: buka manual URL yang tercetak di terminal.
- **Key ditolak**: pastikan key dari `ai.clyve.id` dan base URL `https://ai.clyve.id/v1`.
- **Port bentrok**: `python app.py --port 8777`.
- **Defender flag .exe**: false positive PyInstaller onefile yang unsigned. Untuk distribusi publik pakai Nuitka + code signing.
- **Model balas kosong tanpa tool call**: ganti model ke `deepseek-v4.1-flash` atau `claude-sonnet-5-5`.

## Lisensi

MIT
