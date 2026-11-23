"use strict";
const $ = (s) => document.querySelector(s);
let queries = [];
let busy = false;

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const fmtSize = (n) => n == null ? "-" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB";
const fmtDate = (s) => s ? s.replace("T", " ") : "-";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const year = () => Number($("#year").value);
const STATE = { "none": "nicht vorhanden", "xml": "XML", "xml+sqlite": "XML + SQLite" };

function log(line) { const p = $("#log"); p.textContent += line + "\n"; p.scrollTop = p.scrollHeight; }

async function loadStatus() {
  const st = await api("/api/status");
  const b = $("#status");
  b.textContent = st.ok ? "Tool bereit" : "Tool: " + st.message;
  b.className = "badge " + (st.ok ? "ok" : "err");
  const SRC = { settings: "Einstellungen", env: "Umgebungsvariable", home: "Benutzerordner", app: "App-eigener Ordner" };
  const KIND = { git: "Git", zip: "ZIP", unknown: "unbekannt" };
  $("#tool-info").textContent = `Ordner: ${st.tool_repo} (${SRC[st.tool_source] || st.tool_source})` +
    (st.ok ? ` | Installationsart: ${KIND[st.install_kind] || "-"} | Stand: ${st.version || "unbekannt"}` : " | nicht installiert oder nicht funktionsfähig");
  $("#btn-tool-install").hidden = st.ok;
  $("#btn-tool-update").hidden = !st.ok;
  const upd = $("#btn-tool-update");
  upd.textContent = st.version_state === "newer" ? "Zurück zur getesteten Version"
    : st.version_state === "tested" ? "Tool prüfen" : "Tool aktualisieren";
  upd.dataset.state = st.version_state || "unknown";
  upd.dataset.tested = st.tested_commit || "";
  $("#btn-tool-latest").hidden = !st.ok;
  const vi = $("#tool-version");
  vi.hidden = !st.tested_commit;
  vi.innerHTML = st.tested_commit
    ? `Installierter Commit: <b>${esc(st.installed_commit || "unbekannt")}</b> | Getesteter Commit: <b>${esc(st.tested_commit)}</b>` +
      (st.tested_on ? ` (getestet am ${esc(st.tested_on)})` : "") +
      ` | Zustand: <b class="ver-${esc(st.version_state)}">${esc(st.version_state_text)}</b>`
    : "";
  if (!$("#year").options.length) {
    const cur = new Date().getFullYear();
    for (let y = cur; y >= 2020; y--) $("#year").add(new Option(y, y));
    $("#year").value = st.default_year;
  }
  return st;
}

async function loadKursliste() {
  const d = await api("/api/kursliste");
  $("#kl-dir").textContent = d.directory ? "Ablage: " + d.directory : "Ablage unbekannt (Tool nicht bereit).";
  $("#kl-table tbody").innerHTML = d.years.slice().reverse().map((y) => `<tr>
    <td>${y.year}</td><td class="s-${y.state}">${STATE[y.state]}</td>
    <td>${fmtSize(y.xml_size)}${y.sqlite_size ? " / " + fmtSize(y.sqlite_size) : ""}</td><td>${fmtDate(y.date)}</td>
    <td><button class="small kl-dl" data-y="${y.year}">${y.state === "none" ? "Herunterladen" : "Aktualisieren"}</button></td></tr>`).join("");
  document.querySelectorAll(".kl-dl").forEach((b) => b.onclick = () => runJob(`/api/kursliste/${b.dataset.y}/download`, {}));
}

async function loadFiles() {
  const files = await api("/api/files");
  $("#files-empty").hidden = files.length > 0;
  $("#files-table tbody").innerHTML = files.map((f) => `<tr>
    <td><input type="checkbox" class="sel" value="${esc(f.name)}" checked></td><td>${esc(f.name)}</td>
    <td>${esc(f.account || "-")}</td><td>${fmtSize(f.size)}</td>
    <td><button class="small del" data-n="${esc(f.name)}">Löschen</button></td></tr>`).join("");
  document.querySelectorAll(".del").forEach((b) => b.onclick = async () => {
    await api("/api/files/" + encodeURIComponent(b.dataset.n), { method: "DELETE" }); loadFiles();
  });
}

async function loadSettings() {
  const s = await api("/api/settings");
  $("#token-masked").textContent = s.flex_token_set ? s.flex_token_masked : "nicht gesetzt";
  $("#flex-token").value = "";
  $("#tool-repo").value = s.tool_repo;
  queries = s.flex_queries;
  renderQueries();
}

function renderQueries() {
  $("#q-table tbody").innerHTML = queries.map((q, i) => `<tr>
    <td><input type="text" data-i="${i}" data-k="name" value="${esc(q.name)}"></td>
    <td><input type="text" data-i="${i}" data-k="query_id" value="${esc(q.query_id)}"></td>
    <td><button class="small q-del" data-i="${i}">x</button></td></tr>`).join("");
  document.querySelectorAll("#q-table input").forEach((el) => el.oninput = () => { queries[el.dataset.i][el.dataset.k] = el.value; });
  document.querySelectorAll(".q-del").forEach((b) => b.onclick = () => { queries.splice(b.dataset.i, 1); renderQueries(); });
}

async function saveSettings(extra = {}) {
  await api("/api/settings", json("PUT", { tool_repo: $("#tool-repo").value, flex_token: $("#flex-token").value, flex_queries: queries, ...extra }));
  await loadSettings();
}

async function loadHistory() {
  const h = await api("/api/history");
  $("#history").innerHTML = h.length ? h.map((y) => `<div class="hist-year">${y.year}</div><div class="hist-files">` +
    y.files.map((f) => `<a href="/api/output/${y.year}/${encodeURIComponent(f.name)}">${esc(f.name)}</a>`).join("") + "</div>").join("")
    : '<p class="hint">Noch keine Ergebnisse.</p>';
}

function setBusy(v) { busy = v; document.querySelectorAll("button").forEach((b) => b.disabled = v); }

async function runJob(path, body) {
  if (busy) return;
  setBusy(true);
  $("#log").textContent = "";
  try {
    const { job_id } = await api(path, json("POST", body));
    let since = 0, status = "running";
    while (status === "running") {
      const j = await api(`/api/jobs/${job_id}?since=${since}`);
      $("#job-title").textContent = "- " + j.title;
      j.lines.forEach(log); since = j.next; status = j.status;
      if (status === "running") await new Promise((r) => setTimeout(r, 1000));
      else (j.results || []).filter((x) => x.kind === "warning").forEach((x) => log("WARNUNG: " + x.message));
    }
  } catch (e) { log("FEHLER: " + e.message); }
  setBusy(false);
  loadStatus().catch(() => {}); loadKursliste().catch(() => {}); loadHistory(); loadFiles();
}

async function upload(fileList) {
  const fd = new FormData();
  [...fileList].forEach((f) => fd.append("files", f));
  try { await api("/api/upload", { method: "POST", body: fd }); } catch (e) { log("FEHLER: " + e.message); }
  loadFiles();
}

$("#btn-all").onclick = () => runJob("/api/workflow/all", { year: year() });
$("#btn-tool-install").onclick = () => runJob("/api/tool/install", {});
$("#btn-tool-update").onclick = (e) => {
  const d = e.currentTarget.dataset;
  if (d.state === "newer") {
    if (!confirm(`Das Tool wird auf die getestete Version ${d.tested} zurückgesetzt, eigene Dateien bleiben erhalten.`)) return;
    runJob("/api/tool/update", { target: "tested", force: true });
    return;
  }
  runJob("/api/tool/update", { target: "tested" });
};
$("#btn-tool-latest").onclick = () => {
  if (!confirm("Neueste Version von main installieren (ungetestet)?\n\n" +
    "Das kann die App unbrauchbar machen. Bei Problemen führt \"Zurück zur getesteten Version\" zurück auf die getestete Version.")) return;
  runJob("/api/tool/update", { target: "latest" });
};
$("#btn-flex").onclick = () => runJob("/api/flex/fetch", { year: year() });
$("#btn-generate").onclick = () => {
  const files = [...document.querySelectorAll(".sel:checked")].map((c) => c.value);
  if (!files.length) { log("Keine Datei ausgewählt."); return; }
  runJob("/api/generate", { year: year(), files });
};
$("#btn-add-q").onclick = () => { queries.push({ name: "", query_id: "" }); renderQueries(); };
$("#btn-save-settings").onclick = () => saveSettings().catch((e) => log("FEHLER: " + e.message));
$("#btn-clear-token").onclick = () => saveSettings({ clear_token: true, flex_token: "" });
$("#file-input").onchange = (e) => upload(e.target.files);
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => upload(e.dataTransfer.files));

(async () => {
  await loadStatus().catch((e) => log(e.message));
  loadSettings(); loadKursliste().catch(() => {}); loadFiles(); loadHistory();
})();
