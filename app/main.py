"""FastAPI-App: lokale Oberfläche für opensteuerauszug (nur 127.0.0.1)."""
from __future__ import annotations

import datetime as dt
import os
import threading
from contextlib import asynccontextmanager
from xml.parsers import expat
from pathlib import Path, PurePosixPath

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import kursliste, paths, program_update, runner, selfupdate, settings as st, toolsetup, workflow
from .jobs import JobManager
from .platform_utils import open_folder as _open_folder

STATIC_DIR = paths.STATIC_DIR
PORT = paths.port()
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
ALLOWED_ROOTS = {"FlexQueryResponse", "FlexStatements"}



@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Datenordner anlegen und alte Daten aus dem Projektordner einmalig kopieren (auch bei uvicorn app.main:app)
    paths.startup(log=lambda m: print(m, flush=True))
    yield


app = FastAPI(title="Steuerauszug-Assistent", lifespan=lifespan)
jobs = JobManager()
# Injizierbar, damit Tests den Prozess nicht beenden.
exit_process = os._exit
RESTART_DELAY = 0.5
# Beenden über /api/app/quit: Im Startprogramm (launcher.py) wird quit_via_event gesetzt, dann beendet der
# Launcher die App sauber, sobald quit_event gesetzt ist. Sonst (uvicorn direkt) endet der Prozess mit Code 0.
quit_event = threading.Event()
quit_via_event = False
# Injizierbar, damit Tests keinen Ordner öffnen.
open_folder = _open_folder
FOLDER_TARGETS = ("data", "output")


def _settings() -> dict:
    return st.load_settings()


def _year(year: int | None) -> int:
    y = year if year is not None else dt.date.today().year - 1
    if not 2000 <= y <= 2100:
        raise HTTPException(400, "Ungültiges Steuerjahr.")
    return y


def _start(kind: str, title: str, func):
    job = jobs.start(kind, title, func)
    if job is None:
        raise HTTPException(409, "Es läuft bereits ein Job.")
    return {"job_id": job.id}


@app.get("/api/status")
def api_status():
    s = _settings()
    info = runner.check_tool(s["tool_repo"])
    info.update(toolsetup.describe(s["tool_repo"], s.get("tool_repo_source", "settings")))
    info["app"] = "steuerauszug-assistent"
    info["app_version"] = selfupdate.app_version()
    info["app_version_number"] = paths.version()
    info["program_mode"] = paths.is_frozen()
    info["bundled"] = runner.bundled_mode()
    info["data_dir"] = str(paths.data_dir())
    info["output_dir"] = str(st.OUTPUT_DIR)
    info["default_year"] = dt.date.today().year - 1
    running = jobs.running()
    info["running_job"] = running.id if running else None
    return info


@app.get("/api/health")
def api_health():
    """Leichte Prüfung, ob diese App auf dem Port läuft (ohne Tool-Prüfung)."""
    return {"ok": True, "app": "steuerauszug-assistent", "version": paths.version()}


@app.get("/api/app/update")
def api_app_update_status():
    if paths.is_frozen():
        return program_update.status()
    return {**selfupdate.status(), "mode": "git", "version_number": paths.version()}


@app.post("/api/app/update/check")
def api_app_update_check():
    if paths.is_frozen():
        try:
            return program_update.check()
        except program_update.NotSupported as exc:
            raise HTTPException(409, str(exc))
    return selfupdate.check()


@app.post("/api/app/update/apply")
def api_app_update_apply():
    if paths.is_frozen():
        try:
            return _start("app-update", "Programm aktualisieren", program_update.job_apply())
        except program_update.NotSupported as exc:
            raise HTTPException(409, str(exc))
    return _start("app-update", "App aktualisieren", selfupdate.job_apply())


@app.post("/api/app/quit")
def api_app_quit(body: dict = Body(default={})):
    """Beendet die App sauber. Läuft ein Job, nur mit {"force": true}."""
    if jobs.running() and not body.get("force"):
        raise HTTPException(409, "Es läuft ein Job. Bitte warten, bis er beendet ist.")
    quit_event.set()
    if not quit_via_event:
        timer = threading.Timer(RESTART_DELAY, lambda: exit_process(0))
        timer.daemon = True
        timer.start()
    return {"quitting": True}


@app.post("/api/open-folder")
def api_open_folder(body: dict = Body(default={})):
    """Öffnet den Datenordner oder den Ausgabeordner im Datei-Manager (nur diese festen Ziele)."""
    which = body.get("which")
    if which not in FOLDER_TARGETS:
        raise HTTPException(400, 'Ungültiges Ziel: erlaubt sind "data" und "output".')
    target = paths.data_dir() if which == "data" else st.OUTPUT_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
        open_folder(target)
    except OSError as exc:
        raise HTTPException(500, f"Ordner konnte nicht geöffnet werden: {exc}")
    return {"opened": str(target)}


@app.post("/api/app/restart")
def api_app_restart():
    if jobs.running():
        raise HTTPException(409, "Es läuft ein Job. Bitte warten, bis er beendet ist.")
    if not selfupdate.restart_supported():
        raise HTTPException(400, "Bitte das Fenster schließen und start.bat / start.command erneut starten.")
    selfupdate.schedule_restart(lambda code: exit_process(code), RESTART_DELAY)
    return {"restarting": True}


def _refuse_if_bundled() -> None:
    if runner.bundled_mode():
        raise HTTPException(409, "Das Tool opensteuerauszug ist in diesem Programm mitgeliefert und wird mit dem "
                                 "Programm aktualisiert. Installieren oder Aktualisieren ist hier nicht nötig.")


@app.post("/api/tool/install")
def api_tool_install(body: dict = Body(default={})):
    _refuse_if_bundled()
    s = _settings()
    if not body.get("force") and runner.check_tool(s["tool_repo"])["ok"]:
        raise HTTPException(409, "Das Tool ist bereits installiert und funktionsfähig.")
    return _start("tool-install", "Tool installieren", toolsetup.job_install(s))


@app.post("/api/tool/update")
def api_tool_update(body: dict = Body(default={})):
    """target: "tested" (Standard: auf die getestete Version) oder "latest" (main, ungetestet).
    force: erlaubt das Zurückstufen, wenn der installierte Stand neuer als die getestete Version ist."""
    _refuse_if_bundled()
    s = _settings()
    target = body.get("target") or "tested"
    if target not in ("tested", "latest"):
        raise HTTPException(400, 'Ungültiges Ziel: erlaubt sind "tested" und "latest".')
    force = bool(body.get("force"))
    if target == "latest":
        return _start("tool-update", "Neueste Version (ungetestet)", toolsetup.job_update(s, target="latest", force=force))
    return _start("tool-update", "Tool aktualisieren", toolsetup.job_update(s, force=force))


@app.get("/api/settings")
def api_get_settings():
    return st.public_view(_settings())


@app.put("/api/settings")
def api_put_settings(patch: dict = Body(...)):
    new = st.update_settings(_settings(), patch)
    st.save_settings(new)
    return st.public_view(new)


@app.get("/api/kursliste")
def api_kursliste():
    s = _settings()
    directory = (kursliste.resolve_dir(s["tool_repo"])
                 if runner.bundled_mode() or runner.tool_python(s["tool_repo"]).exists() else None)
    return {"directory": str(directory) if directory else None,
            "years": kursliste.list_status(directory)}


@app.post("/api/kursliste/{year}/download")
def api_kursliste_download(year: int):
    s = _settings()
    y = _year(year)
    return _start("kursliste", f"Kursliste {y}", workflow.job_kursliste(s, y))


@app.get("/api/files")
def api_files():
    return [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime,
             "account": runner.extract_account_id(p)} for p in workflow.upload_files()]


class _ForbiddenDtd(Exception):
    pass


def _check_flex_xml(data: bytes) -> str | None:
    """Gibt eine deutsche Fehlermeldung zurück oder None, wenn die Datei gültig ist."""
    roots: list[str] = []

    def forbid(*_args):
        raise _ForbiddenDtd()

    def start_element(name, _attrs):
        if not roots:
            roots.append(name)

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = forbid
    parser.EntityDeclHandler = forbid
    parser.StartElementHandler = start_element
    try:
        parser.Parse(data, True)
    except _ForbiddenDtd:
        return "DOCTYPE/ENTITY-Deklarationen sind nicht erlaubt."
    except expat.ExpatError as exc:
        return f"kein wohlgeformtes XML ({exc})."
    root = roots[0] if roots else ""
    if root not in ALLOWED_ROOTS:
        return f"unerwartetes Wurzelelement <{root}> (erwartet: FlexQueryResponse oder FlexStatements)."
    return None


@app.post("/api/upload")
async def api_upload(files: list[UploadFile] = File(...)):
    validated: list[tuple[str, bytes]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for f in files:
        label = f.filename or "upload.xml"
        name = runner.safe_name(PurePosixPath(label.replace("\\", "/")).name)
        if not name.lower().endswith(".xml"):
            errors.append(f"{label}: nur XML-Dateien (.xml) erlaubt.")
            continue
        if name.casefold() in seen:
            errors.append(f"{label}: doppelter Dateiname im Upload ({name}).")
            continue
        seen.add(name.casefold())
        data = bytearray()
        too_big = False
        while chunk := await f.read(CHUNK_SIZE):
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_BYTES:
                too_big = True
                break
        if too_big:
            errors.append(f"{label}: Datei zu gross (maximal {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
            continue
        problem = _check_flex_xml(bytes(data))
        if problem:
            errors.append(f"{label}: {problem}")
            continue
        validated.append((name, bytes(data)))
    if errors:
        raise HTTPException(400, " ".join(errors))
    st.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_paths: list[Path] = []
    try:
        for name, data in validated:
            tmp = st.UPLOAD_DIR / f".{name}.tmp"
            tmp_paths.append(tmp)
            tmp.write_bytes(data)
    except OSError as exc:
        for tmp in tmp_paths:
            tmp.unlink(missing_ok=True)
        raise HTTPException(500, f"Speichern fehlgeschlagen, es wurde nichts gespeichert ({exc}).")

    # Replace-Phase mit Rollback: vorhandene Ziele werden zuerst gesichert.
    done: list[tuple[Path, Path | None]] = []  # (Ziel, Sicherung oder None, falls neu)
    bak_paths: list[Path] = []
    try:
        for (name, _), tmp in zip(validated, tmp_paths):
            target = st.UPLOAD_DIR / name
            bak: Path | None = None
            if target.exists():
                bak = st.UPLOAD_DIR / f".{name}.bak"
                bak_paths.append(bak)
                os.replace(target, bak)
            try:
                os.replace(tmp, target)
            except OSError:
                if bak is not None:
                    done.append((target, bak))  # Ziel fehlt jetzt, Sicherung zurueckspielen
                raise
            done.append((target, bak))
    except OSError as exc:
        failed: list[str] = []
        for target, bak in reversed(done):
            try:
                if bak is not None:
                    os.replace(bak, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                failed.append(target.name)
        for tmp in tmp_paths:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        for bak in bak_paths:
            if bak.name[1:-4] in failed:
                continue  # Sicherung behalten, sie ist die einzige Kopie
            try:
                bak.unlink(missing_ok=True)
            except OSError:
                pass
        if failed:
            raise HTTPException(
                500,
                f"Speichern fehlgeschlagen ({exc}) und Wiederherstellung unvollstaendig. "
                f"Betroffene Dateien: {', '.join(failed)}. Sicherungen liegen als .<name>.bak im Upload-Ordner.",
            )
        raise HTTPException(500, f"Speichern fehlgeschlagen, es wurde nichts gespeichert ({exc}).")
    for bak in bak_paths:
        bak.unlink(missing_ok=True)
    return {"saved": [n for n, _ in validated]}


@app.delete("/api/files/{name}")
def api_delete_file(name: str):
    if Path(name).name != name:
        raise HTTPException(400, "Ungültiger Name.")
    p = st.UPLOAD_DIR / name
    if not p.is_file():
        raise HTTPException(404, "Datei nicht gefunden.")
    p.unlink()
    return {"deleted": name}


@app.post("/api/flex/fetch")
def api_flex_fetch(body: dict = Body(default={})):
    s = _settings()
    y = _year(body.get("year"))
    if not s["flex_token"] or not s["flex_queries"]:
        raise HTTPException(400, "Flex-Token und mindestens eine Query-ID müssen gespeichert sein.")
    return _start("flex", f"Flex-Abruf {y}", workflow.job_flex(s, y))


@app.post("/api/generate")
def api_generate(body: dict = Body(default={})):
    s = _settings()
    y = _year(body.get("year"))
    files = workflow.resolve_selected(body.get("files"))
    if not files:
        raise HTTPException(400, "Keine XML-Dateien vorhanden oder ausgewählt.")
    return _start("generate", f"Steuerauszug {y}", workflow.job_generate(s, y, files))


@app.post("/api/workflow/all")
def api_all(body: dict = Body(default={})):
    s = _settings()
    y = _year(body.get("year"))
    if not s["flex_token"] or not s["flex_queries"]:
        raise HTTPException(400, "Flex-Token und mindestens eine Query-ID müssen gespeichert sein.")
    return _start("all", f"Alles automatisch {y}", workflow.job_all(s, y))


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, since: int = 0):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job nicht gefunden.")
    return job.snapshot(since)


@app.get("/api/history")
def api_history():
    return workflow.history()


@app.get("/api/output/{year}/{name}")
def api_output(year: int, name: str):
    if Path(name).name != name:
        raise HTTPException(400, "Ungültiger Name.")
    p = st.OUTPUT_DIR / str(year) / name
    if not p.is_file():
        raise HTTPException(404, "Datei nicht gefunden.")
    return FileResponse(p, filename=name)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
