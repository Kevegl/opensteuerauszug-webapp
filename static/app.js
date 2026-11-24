"use strict";
const $ = (s) => document.querySelector(s);
let queries = [];
let busy = false;
let appState = null;

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
  const SRC = { settings: "Einstellungen", env: "Umgebungsvariable", home: "Benutzerordner", app: "App-eigener Ordner", bundled: "mitgeliefert" };
  const KIND = { git: "Git", zip: "ZIP", unknown: "unbekannt" };
  const bundled = !!st.bundled;
  $("#tool-info").textContent = bundled
    ? (st.ok ? `Tool: ${st.bundled_text || "mitgeliefert"}` : `Tool: ${st.message}`)
    : `Ordner: ${st.tool_repo} (${SRC[st.tool_source] || st.tool_source})` +
    (st.ok ? ` | Installationsart: ${KIND[st.install_kind] || "-"} | Stand: ${st.version || "unbekannt"}` : " | nicht installiert oder nicht funktionsfähig");
  $("#btn-tool-install").hidden = st.ok || bundled;
  $("#btn-tool-update").hidden = !st.ok || bundled;
  $("#tool-repo-label").hidden = bundled;
  const upd = $("#btn-tool-update");
  upd.textContent = st.version_state === "newer" ? "Zurück zur getesteten Version"
    : st.version_state === "tested" ? "Tool prüfen" : "Tool aktualisieren";
  upd.dataset.state = st.version_state || "unknown";
  upd.dataset.tested = st.tested_commit || "";
  $("#btn-tool-latest").hidden = !st.ok || bundled;
  const vi = $("#tool-version");
  vi.hidden = !st.tested_commit || bundled;
  $("#data-info").textContent = "Datenordner: " + (st.data_dir || "-") +
    (st.app_version_number ? ` | Programmversion: ${st.app_version_number}` : "");
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
  loadStatus().catch(() => {}); loadKursliste().catch(() => {}); loadHistory(); loadFiles(); loadAppUpdate();
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

// ---- App-Update ----
function appMsg(text, cls) {
  const m = $("#app-msg");
  m.hidden = !text; m.textContent = text || ""; m.className = "hint " + (cls || "");
}

function renderApp(u) {
  appState = u;
  const info = $("#app-info");
  const program = u.mode === "program";
  $("#app-program").hidden = !program;
  $("#app-nogit").hidden = u.is_git || program;
  $("#btn-app-check").hidden = !u.is_git;
  if (program) {
    info.textContent = `Programmversion: ${u.version || "unbekannt"}`;
    if (u.releases_url) $("#app-releases").href = u.releases_url;
    $("#btn-app-apply").hidden = true; $("#btn-app-restart").hidden = true; $("#app-commits").hidden = true;
    appMsg("");
    return;
  }
  if (!u.is_git) {
    info.textContent = "Update nicht verfügbar: " + (u.reason || "unbekannt");
    $("#btn-app-apply").hidden = true; $("#btn-app-restart").hidden = true; $("#app-commits").hidden = true;
    appMsg("");
    return;
  }
  info.textContent = `Version: ${u.version || u.commit || "unbekannt"} | Branch: ${u.branch || "(losgelöst)"}` +
    (u.upstream ? ` | Upstream: ${u.upstream}` : "");
  const c = u.last_check;
  const avail = !!(c && c.ok && c.update_available && !u.restart_required);
  const list = $("#app-commits");
  list.hidden = !(avail && c.commits.length);
  list.innerHTML = avail ? c.commits.map((x) => `<li><code>${esc(x.hash)}</code>${esc(x.subject)}</li>`).join("") : "";
  const apply = $("#btn-app-apply");
  apply.hidden = !avail;
  apply.disabled = busy || !!u.blocker || (c && c.ahead > 0);
  const restart = $("#btn-app-restart");
  restart.hidden = !(u.restart_required && u.restart_supported);
  restart.disabled = busy;
  if (u.restart_required) {
    appMsg(u.restart_supported ? "Update installiert. Zum Aktivieren die App neu starten."
      : "Update installiert. Bitte das Fenster schließen und start.bat / start.command erneut starten.", "app-ok");
  } else if (u.blocker) {
    appMsg(u.blocker, "app-warn");
  } else if (c && !c.ok) {
    appMsg(c.error, "app-err");
  } else if (c && c.ahead > 0) {
    appMsg(`Der lokale Stand ist ${c.ahead} Commit(s) voraus; automatisches Update nicht möglich.`, "app-warn");
  } else if (c && c.update_available) {
    appMsg(`${c.behind} neue Änderung${c.behind === 1 ? "" : "en"}` + (c.latest_tag ? ` (Version ${c.latest_tag})` : ""), "app-warn");
  } else if (c) {
    appMsg("Aktuell.", "app-ok");
  } else {
    appMsg("");
  }
}

async function loadAppUpdate() {
  try { renderApp(await api("/api/app/update")); }
  catch (e) { $("#app-info").textContent = "App-Version: " + e.message; }
}

async function checkAppUpdate() {
  const btn = $("#btn-app-check");
  btn.disabled = true; appMsg("Suche ...");
  try {
    const r = await api("/api/app/update/check", json("POST", {}));
    const u = await api("/api/app/update");
    u.last_check = r;
    renderApp(u);
  } catch (e) { appMsg("FEHLER: " + e.message, "app-err"); }
  btn.disabled = false;
}

async function restartApp() {
  if (!confirm("Die App jetzt neu starten?")) return;
  setBusy(true);
  appMsg("Neustart läuft ...", "app-warn");
  try { await api("/api/app/restart", json("POST", {})); }
  catch (e) { appMsg("FEHLER: " + e.message, "app-err"); setBusy(false); return; }
  const deadline = Date.now() + 60000;
  await new Promise((r) => setTimeout(r, 1500));
  while (Date.now() < deadline) {
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      if (r.ok) { location.reload(); return; }
    } catch (_) { /* Server noch nicht erreichbar */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
  appMsg("Die App ist nach 60 s nicht wieder erreichbar. Bitte start.bat / start.command prüfen.", "app-err");
  setBusy(false);
}

$("#btn-app-check").onclick = checkAppUpdate;
$("#btn-app-apply").onclick = async () => {
  if (!confirm("Update jetzt installieren? Nur Fast-Forward, eigene Daten (Einstellungen, Uploads, Ergebnisse) bleiben unberührt.")) return;
  await runJob("/api/app/update/apply", {});
};
$("#btn-app-restart").onclick = restartApp;

// ---- Ordner öffnen / App beenden ----
async function openFolder(which) {
  try { await api("/api/open-folder", json("POST", { which })); }
  catch (e) { log("FEHLER: " + e.message); }
}
$("#btn-open-data").onclick = () => openFolder("data");
$("#btn-open-output").onclick = () => openFolder("output");
$("#btn-quit").onclick = async () => {
  if (!confirm("Den Steuerauszug-Assistenten jetzt beenden? Ihre Daten bleiben gespeichert.")) return;
  const msg = $("#quit-msg");
  let force = false;
  try { await api("/api/app/quit", json("POST", {})); }
  catch (e) {
    if (!/Job/.test(e.message) || !confirm(e.message + "\nTrotzdem beenden?")) { log("FEHLER: " + e.message); return; }
    force = true;
    try { await api("/api/app/quit", json("POST", { force })); } catch (e2) { log("FEHLER: " + e2.message); return; }
  }
  msg.hidden = false;
  msg.textContent = "Der Steuerauszug-Assistent wurde beendet. Sie können dieses Fenster schließen. Zum erneuten Starten das Programm öffnen.";
  document.querySelectorAll("button").forEach((b) => { b.disabled = true; });
};

(async () => {
  await loadStatus().catch((e) => log(e.message));
  loadSettings(); loadKursliste().catch(() => {}); loadFiles(); loadHistory(); loadAppUpdate();
})();
