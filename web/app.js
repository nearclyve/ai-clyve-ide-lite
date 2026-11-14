/* Clyve IDE - frontend. Vanilla JS, no build step. */

const $ = (id) => document.getElementById(id);
const chatEl = $("chat");
const chatInner = $("chatInner");
const input = $("input");
let state = {};
let busy = false;
let controller = null;
let current = null; // { role, contentEl, toolEls: Map }

/* ---------------------------------------------------------------- helpers */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** tiny markdown: fences, inline code, bold, italic, links, lists */
function md(src) {
  const parts = String(src).split(/```/);
  let html = "";
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf("\n");
      const body = nl >= 0 ? part.slice(nl + 1) : part;
      const lang = nl >= 0 ? part.slice(0, nl).trim() : "";
      html += `<pre><code data-lang="${esc(lang)}">${esc(body.replace(/\n$/, ""))}</code></pre>`;
      return;
    }
    let t = esc(part);
    t = t.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    t = t.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    t = t.replace(/(^|\s)\*([^*\n]+)\*/g, "$1<i>$2</i>");
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
    const lines = t.split("\n");
    let out = "", inList = false;
    for (const line of lines) {
      const m = line.match(/^\s*[-*]\s+(.*)$/);
      if (m) {
        if (!inList) { out += "<ul>"; inList = true; }
        out += `<li>${m[1]}</li>`;
      } else {
        if (inList) { out += "</ul>"; inList = false; }
        out += line.trim() === "" ? "\n" : line + "\n";
      }
    }
    if (inList) out += "</ul>";
    html += out.replace(/\n{3,}/g, "\n\n").replace(/\n/g, "<br>");
  });
  return html.replace(/<br>(<\/?(?:ul|pre|li)>)/g, "$1")
             .replace(/(<\/(?:ul|pre)>)(?!<br>)/g, "$1");
}

function scrollDown(force) {
  const nearBottom = chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight < 160;
  if (force || nearBottom) chatEl.scrollTop = chatEl.scrollHeight;
}

/** keep the tail of a long path visible: C:\Users\me\…\project */
function shortenPath(p, max = 34) {
  if (!p) return "-";
  if (p.length <= max) return p;
  return "…" + p.slice(-(max - 1));
}

/** one-line summary of tool args instead of raw JSON */
function argSummary(name, args) {
  if (args == null) return "";
  if (typeof args === "string") return args;
  const a = args;
  switch (name) {
    case "read_file": return a.path || "";
    case "write_file": return `${a.path || ""} (${(a.content || "").length} chars)`;
    case "edit_file": return a.path || "";
    case "list_dir": return a.path || ".";
    case "search_files": return `${a.pattern || ""} ${a.glob ? "in " + a.glob : ""}`;
    case "run_shell": return a.cmd || "";
    case "sqlite_query": return `${a.db || ""} : ${(a.sql || "").slice(0, 80)}`;
    case "http_request": return `${(a.method || "GET").toUpperCase()} ${a.url || ""}`;
    case "open_path": return a.path || ".";
    case "open_url": return a.url || "";
    case "make_dir": case "delete_path": return a.path || "";
    default: return JSON.stringify(a);
  }
}

/* ---------------------------------------------------------------- boot */
async function boot() {
  try {
    const r = await fetch("/api/state").then((x) => x.json());
    state = r;
  } catch (e) {
    alert("server tidak merespons");
    return;
  }
  if (!state.has_key) {
    $("gate").classList.remove("hidden");
    $("gateBase").value = state.base_url || "https://ai.clyve.id/v1";
    $("gateKey").focus();
    return;
  }
  applyState();
  $("shell").classList.remove("hidden");
  await loadChats();
  input.focus();
}

function applyState() {
  $("gate").classList.add("hidden");
  $("shell").classList.remove("hidden");
  $("wsPath").textContent = shortenPath(state.workspace);
  $("wsPath").title = state.workspace || "";
  $("crumbWs").textContent = state.workspace || "-";
  $("keyMasked").textContent = state.key_masked || "-";
  $("fullAccess").checked = !!state.full_access;
  renderRoots();

  const sel = $("modelSel");
  sel.innerHTML = "";
  (state.models || []).forEach((m) => {
    const o = el("option", null, m);
    o.value = m;
    if (m === state.model) o.selected = true;
    sel.appendChild(o);
  });
  document.title = `Clyve IDE - ${state.model || ""}`;
}

/* ---------------------------------------------------------------- key gate */
$("gateBtn").onclick = async () => {
  const key = $("gateKey").value.trim();
  const base = $("gateBase").value.trim();
  if (!key) return;
  const btn = $("gateBtn");
  btn.disabled = true; btn.textContent = "Menghubungkan...";
  $("gateErr").classList.add("hidden");
  try {
    const r = await fetch("/api/key", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, base_url: base }),
    }).then((x) => x.json());
    if (!r.ok) throw new Error(r.error || "gagal");
    state = r;
    applyState();
    input.focus();
  } catch (e) {
    $("gateErr").textContent = e.message;
    $("gateErr").classList.remove("hidden");
  } finally {
    btn.disabled = false; btn.textContent = "Hubungkan";
  }
};
$("gateKey").addEventListener("keydown", (e) => { if (e.key === "Enter") $("gateBtn").click(); });

$("keyBtn").onclick = () => {
  $("gate").classList.remove("hidden");
  $("gateKey").value = "";
  $("gateBase").value = state.base_url || "https://ai.clyve.id/v1";
  $("gateKey").focus();
};

/* ---------------------------------------------------------------- sidebar */
$("modelSel").onchange = async (e) => {
  const model = e.target.value;
  await fetch("/api/model", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  state.model = model;
  setStatus("model: " + model);
};

$("exploreBtn").onclick = async () => {
  await fetch("/api/workspace", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: state.workspace }),
  });
  setStatus("workspace dibuka di Explorer - agent bisa open_path juga");
};

/* ---------------------------------------------------------------- access */
function renderRoots() {
  const box = $("roots");
  box.innerHTML = "";
  const ws = state.workspace;
  const extras = (state.extra_roots || []).filter((x) => x && x !== ws);
  if (!extras.length) {
    box.appendChild(el("div", "root-empty", "hanya workspace"));
    return;
  }
  extras.forEach((r) => {
    const row = el("div", "root");
    row.appendChild(el("span", null, shortenPath(r, 30)));
    row.title = r;
    const btn = el("button", null, "×");
    btn.title = "hapus folder ini dari izin";
    btn.onclick = () => saveAccess({ extra_roots: extras.filter((x) => x !== r) });
    row.appendChild(btn);
    box.appendChild(row);
  });
}

async function saveAccess(payload) {
  const r = await fetch("/api/access", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((x) => x.json());
  if (r.ok) {
    state = { ...state, ...r };
    renderRoots();
    setStatus("akses diperbarui");
  } else {
    setStatus(r.error || "gagal update akses");
  }
}

$("fullAccess").onchange = (e) => saveAccess({ full_access: e.target.checked });

$("addRoot").onclick = () => openPicker(state.workspace, "root");

/* ---------------------------------------------------------------- picker */
let pickState = { path: "", parent: null };
let pickMode = "workspace";

async function openPicker(path, mode = "workspace") {
  pickMode = mode;
  $("picker").classList.remove("hidden");
  $("pickHere").textContent = mode === "root" ? "Izinkan folder ini" : "Pakai folder ini";
  await loadPicker(path || "");
}
async function loadPicker(path) {
  const r = await fetch("/api/browse?path=" + encodeURIComponent(path)).then((x) => x.json());
  if (r.error) { setStatus(r.error); return; }
  pickState = r;
  $("pickPath").textContent = r.path || "(drive list)";
  const list = $("pickList");
  list.innerHTML = "";
  (r.dirs || []).forEach((d) => {
    const it = el("div", "pick-item");
    it.innerHTML = `<span class="ico">▸</span><span>${esc(d)}</span>`;
    it.onclick = () => loadPicker(d);
    list.appendChild(it);
  });
  if (!r.dirs || !r.dirs.length) list.appendChild(el("div", "pick-item", "(tidak ada subfolder)"));
}
$("pickBtn").onclick = () => openPicker(state.workspace);
$("wsBtn").onclick = () => openPicker(state.workspace);
$("pickClose").onclick = () => $("picker").classList.add("hidden");
$("picker").addEventListener("click", (e) => {
  if (e.target === $("picker")) $("picker").classList.add("hidden");
});
$("pickUp").onclick = () => { if (pickState.parent) loadPicker(pickState.parent); else loadPicker(""); };
$("pickHere").onclick = async () => {
  if (!pickState.path) return setStatus("pilih drive/folder dulu");

  if (pickMode === "root") {
    const already = [state.workspace, ...(state.extra_roots || [])];
    if (already.includes(pickState.path)) {
      setStatus("folder itu sudah diizinkan");
      $("picker").classList.add("hidden");
      return;
    }
    const roots = [...(state.extra_roots || []), pickState.path];
    await saveAccess({ extra_roots: roots });
    $("picker").classList.add("hidden");
    return;
  }

  const r = await fetch("/api/workspace", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: pickState.path }),
  }).then((x) => x.json());
  if (r.ok) {
    state.workspace = r.workspace;
    state.roots = [r.workspace, ...(state.extra_roots || [])];
    $("wsPath").textContent = shortenPath(r.workspace);
    $("wsPath").title = r.workspace;
    $("crumbWs").textContent = r.workspace;
    $("picker").classList.add("hidden");
    renderRoots();
    setStatus("workspace: " + r.workspace);
  } else {
    setStatus(r.error || "gagal ganti workspace");
  }
};

/* ---------------------------------------------------------------- chat ui */
const WELCOME_HTML = document.getElementById("welcome") ? document.getElementById("welcome").outerHTML : "";

function bindChips() {
  document.querySelectorAll(".chip").forEach((c) => {
    c.onclick = () => send(c.dataset.t);
  });
}

function ensureWelcome() {
  if (!document.getElementById("welcome") && WELCOME_HTML) {
    chatInner.insertAdjacentHTML("beforeend", WELCOME_HTML);
    bindChips();
  }
  return document.getElementById("welcome");
}

function showWelcome() {
  chatInner.innerHTML = "";
  const w = ensureWelcome();
  if (w) w.classList.remove("hidden");
  clearToolLog();
}

function hideWelcome() {
  const w = document.getElementById("welcome");
  if (w) w.classList.add("hidden");
}

function addMsg(role, text) {
  hideWelcome();
  const wrap = el("div", "msg " + role);
  const av = el("div", "avatar", role === "user" ? "You" : "◈");
  if (role === "bot") av.textContent = "◈";
  if (role === "user") av.textContent = "🙋";
  const body = el("div", "body");
  body.appendChild(el("div", "who", role === "user" ? "kamu" : "Clyve"));
  const content = el("div", "content");
  if (text) content.innerHTML = md(text);
  body.appendChild(content);
  wrap.appendChild(av); wrap.appendChild(body);
  chatInner.appendChild(wrap);
  scrollDown(true);
  return { wrap, body, content };
}

function addToolCard(bodyEl, name, args) {
  const card = el("div", "tool");
  const head = el("div", "tool-head");
  head.innerHTML = `<span class="spin"></span><span class="name">${esc(name)}</span>`;
  const argStr = argSummary(name, args);
  head.appendChild(el("span", "args", " " + argStr));
  const out = el("div", "tool-out", "");
  card.appendChild(head); card.appendChild(out);
  head.onclick = () => card.classList.toggle("open");
  // keep chronology: actions sit above the prose that summarises them
  const anchor = current && current.contentEl;
  if (anchor && anchor.parentNode === bodyEl) bodyEl.insertBefore(card, anchor);
  else bodyEl.appendChild(card);
  scrollDown();
  return { card, head, out, argStr, name };
}

function finishToolCard(ref, ok, result, ms, name) {
  ref.card.classList.add(ok ? "ok" : "bad");
  ref.head.querySelector(".spin")?.remove();
  const msEl = el("span", "ms", (ms != null ? ms + "ms" : ""));
  ref.head.appendChild(msEl);
  ref.out.textContent = result || "";
  if (!ok) ref.card.classList.add("open");
  const log = $("toolLog");
  if (log) {
    const ti = el("div", "ti " + (ok ? "ok" : "bad"), name + " " + ref.argStr);
    log.appendChild(ti);
    while (log.children.length > 12) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
  }
  scrollDown();
}

function setStatus(t, kind) {
  $("status").textContent = t;
  const box = $("status").parentElement;
  box.classList.remove("err", "busy");
  if (kind) box.classList.add(kind);
}
function setUsage(u) {
  const box = $("usage");
  if (!box || !u) return;
  box.textContent = `prompt  ${u.prompt_tokens ?? 0}\noutput  ${u.completion_tokens ?? 0}`;
}

function clearToolLog() {
  const log = $("toolLog");
  if (log) log.innerHTML = "";
}

/* ---------------------------------------------------------------- attachments */
let attachments = [];
const MAX_FILES = 10;

function fmtSize(b) {
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + " KB";
  return (b / 1024 / 1024).toFixed(1) + " MB";
}

function renderAttach() {
  const bar = $("attachBar");
  bar.innerHTML = "";
  if (!attachments.length) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  attachments.forEach((a, i) => {
    const chip = el("div", "att");
    const KIND_LABEL = { image: "IMG", pdf: "PDF", docx: "DOCX", xlsx: "XLSX", text: "TEXT" };
    chip.appendChild(el("span", "kind", KIND_LABEL[a.kind] || "FILE"));
    const nm = el("span", "nm", a.name);
    nm.title = a.path || a.name;
    chip.appendChild(nm);
    chip.appendChild(el("span", "sz", fmtSize(a.size || 0)));
    const x = el("button", null, "×");
    x.title = "hapus lampiran";
    x.onclick = () => { attachments.splice(i, 1); renderAttach(); syncSend(); };
    chip.appendChild(x);
    bar.appendChild(chip);
  });
  $("attachBtn").disabled = attachments.length >= MAX_FILES;
  $("attachBtn").title = attachments.length >= MAX_FILES
    ? `maksimal ${MAX_FILES} file per pesan` : "Lampirkan file";
}

function readDataURL(file) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = () => rej(new Error("gagal membaca file"));
    fr.readAsDataURL(file);
  });
}

async function addFiles(list) {
  const files = Array.from(list || []);
  for (const f of files) {
    if (attachments.length >= MAX_FILES) { setStatus(`maksimal ${MAX_FILES} file per pesan`); break; }
    setStatus("mengunggah " + f.name + "...", "busy");
    try {
      const data = await readDataURL(f);
      const r = await fetch("/api/upload", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: f.name, data }),
      }).then((x) => x.json());
      if (!r.ok) throw new Error(r.error || "upload gagal");
      attachments.push({ path: r.path, name: r.name, kind: r.kind, size: r.size });
      renderAttach();
      setStatus("siap");
    } catch (e) {
      setStatus(e.message, "err");
    }
  }
  syncSend();
}

$("attachBtn").onclick = () => $("fileInput").click();
$("fileInput").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };

input.addEventListener("paste", (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  const files = [];
  for (const it of items) if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
  if (files.length) { e.preventDefault(); addFiles(files); }
});

chatEl.addEventListener("dragover", (e) => { e.preventDefault(); chatEl.classList.add("drop"); });
chatEl.addEventListener("dragleave", () => chatEl.classList.remove("drop"));
chatEl.addEventListener("drop", (e) => {
  e.preventDefault(); chatEl.classList.remove("drop");
  if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
});

function syncSend() {
  if (!busy) $("send").disabled = !(input.value.trim() || attachments.length);
}

/* ---------------------------------------------------------------- riwayat */
function fmtWhen(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("id-ID", { day: "numeric", month: "short" });
}

async function loadChats() {
  try {
    const r = await fetch("/api/chats").then((x) => x.json());
    state.chat_id = r.current;
    renderChats(r.chats || []);
  } catch (e) { /* server offline */ }
}

function renderChats(chats) {
  const box = $("chatList");
  box.innerHTML = "";
  if (!chats.length) { box.appendChild(el("div", "chat-empty", "belum ada riwayat")); return; }
  chats.forEach((c) => {
    const row = el("div", "chat-item" + (c.id === state.chat_id ? " active" : ""));
    row.appendChild(el("span", "t", c.title || "(baru)"));
    row.title = c.title || "";
    row.appendChild(el("span", "d", fmtWhen(c.updated)));
    const del = el("button", "del", "×");
    del.title = "hapus chat";
    del.onclick = (e) => { e.stopPropagation(); deleteChat(c.id); };
    row.appendChild(del);
    row.onclick = () => openChat(c.id);
    box.appendChild(row);
  });
}

function renderTranscript(msgs) {
  chatInner.innerHTML = "";
  if (!msgs || !msgs.length) { showWelcome(); return; }
  const w = document.getElementById("welcome");
  if (w) w.classList.add("hidden");
  msgs.forEach((m) => addMsg(m.role === "user" ? "user" : "bot", m.content || ""));
  chatEl.scrollTop = chatEl.scrollHeight;
}

async function openChat(id) {
  if (busy) return;
  const r = await fetch("/api/chats/open", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  }).then((x) => x.json());
  if (!r.ok) { setStatus(r.error || "gagal buka chat", "err"); return; }
  state.chat_id = r.id;
  renderTranscript(r.messages || []);
  clearToolLog();
  setStatus("chat dibuka");
  loadChats();
}

async function newChat() {
  if (busy) return;
  const r = await fetch("/api/chats/new", { method: "POST" }).then((x) => x.json());
  state.chat_id = r.id;
  chatInner.innerHTML = "";
  showWelcome();
  clearToolLog();
  setUsage({ prompt_tokens: 0, completion_tokens: 0 });
  setStatus("siap");
  loadChats();
}

async function deleteChat(id) {
  const r = await fetch("/api/chats/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  }).then((x) => x.json());
  state.chat_id = r.current;
  renderChats(r.chats || []);
  if (id === (state.chat_id || "")) { /* deleted current */ }
  if (!document.querySelector(".chat-item.active")) { chatInner.innerHTML = ""; showWelcome(); }
}

$("newChat").onclick = newChat;

/* ---------------------------------------------------------------- send */
async function send(text) {
  if (busy) return;
  const msg = (text ?? input.value).trim();
  const att = attachments.slice();
  if (!msg && !att.length) return;
  if (!state.has_key) { $("gate").classList.remove("hidden"); return; }

  input.value = "";
  input.style.height = "auto";
  attachments = [];
  renderAttach();
  syncSend();
  busy = true;
  $("send").disabled = true;
  $("stopBtn").classList.remove("hidden");
  setStatus("berpikir...", "busy");
  clearToolLog();

  const shown = msg + (att.length ? "\n\n" + att.map((a) => "📎 " + a.name).join("\n") : "");
  addMsg("user", shown);
  const bot = addMsg("bot", "");
  current = { contentEl: bot.content, buf: "", toolIndex: 0 };
  bot.content.classList.add("cursor");
  let toolRefs = [];

  try {
    controller = new AbortController();
    const resp = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg || "Tolong baca lampiran ini.", attachments: att }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({ error: "HTTP " + resp.status }));
      throw new Error(j.error || "request gagal");
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let raw = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      raw += dec.decode(value, { stream: true });
      let sep;
      while ((sep = raw.indexOf("\n\n")) >= 0) {
        const block = raw.slice(0, sep);
        raw = raw.slice(sep + 2);
        const line = block.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }
        handleEvent(ev, bot, toolRefs);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      if (!current.buf) bot.content.textContent = "dihentikan oleh user";
      setStatus("dihentikan");
    } else {
      bot.content.innerHTML = md("**Error:** " + e.message);
      setStatus("error", "err");
    }
  } finally {
    bot.content.classList.remove("cursor");
    busy = false;
    controller = null;
    syncSend();
    $("stopBtn").classList.add("hidden");
    scrollDown();
    loadChats();
  }
}

function handleEvent(ev, bot, toolRefs) {
  if (ev.type === "delta") {
    current.buf += ev.text;
    bot.content.innerHTML = md(current.buf);
    scrollDown();
  } else if (ev.type === "tool_start") {
    setStatus(`${ev.name}...`);
    const ref = addToolCard(bot.body, ev.name, ev.args);
    toolRefs[ev.name + ":" + (current.toolIndex++)] = ref;
    ref._name = ev.name;
    pendingRef = ref;
  } else if (ev.type === "tool_end") {
    const ref = pendingRef;
    if (ref) finishToolCard(ref, ev.ok, ev.result, ev.ms, ev.name);
    pendingRef = null;
    setStatus("jalan...");
  } else if (ev.type === "done") {
    if (!current.buf && ev.content) {
      current.buf = ev.content;
      bot.content.innerHTML = md(current.buf);
    }
    if (!current.buf && !ev.content) bot.content.innerHTML = md("_(selesai tanpa teks)_");
    setUsage(ev.usage);
    setStatus("siap");
    $("stopBtn").classList.add("hidden");
  } else if (ev.type === "error") {
    bot.content.innerHTML = md("**Error:** " + ev.message);
    setStatus("error", "err");
    $("stopBtn").classList.add("hidden");
  }
}
let pendingRef = null;

$("send").onclick = () => send();
$("stopBtn").onclick = async () => {
  if (!busy) return;
  // tell the server to abort the provider stream, then drop the HTTP connection
  try { await fetch("/api/cancel", { method: "POST" }); } catch (e) { /* ignore */ }
  if (controller) { try { controller.abort(); } catch (e) { /* ignore */ } }
  busy = false;
  if (current && current.contentEl) current.contentEl.classList.remove("cursor");
  $("stopBtn").classList.add("hidden");
  syncSend();
  setStatus("dihentikan");
};

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
  syncSend();
});
$("send").disabled = true;

document.querySelectorAll(".chip").forEach((c) => {
  c.onclick = () => send(c.dataset.t);
});
boot();
