#!/usr/bin/env python3
"""End-to-End-Test des Programm-Updates mit echten Builds (kein pytest-Test; läuft unter Linux/macOS, nur Standardbibliothek).

  python tests/update_e2e.py [--python <Python mit PyInstaller und Tool>] [--work <Ordner>] [--keep]

Ablauf:
  1. baut Version A (app/VERSION, 1.5.0) und Version B (VERSION vorübergehend 1.5.1, danach zurückgesetzt) in getrennte
     Ordner; mit --work und vorhandenen Builds werden diese wiederverwendet
  2. startet einen lokalen HTTP-Server auf 127.0.0.1 als Fake-GitHub (releases/latest, ZIP, .sha256)
  3. Fall 1 (Update): A läuft, prüfen -> installieren -> neu starten -> 1.5.1 antwortet, previous = 1.5.0
  4. Fall 2 (Rollback): "Vorherige Version wiederherstellen" -> 1.5.0 antwortet, versions/1.5.1 wird aufgeräumt
  5. Fall 3 (kaputte Version C 1.5.2: gültige Struktur, Programmdatei startet nicht): Neustart -> es läuft wieder
     1.5.0, failed=1.5.2, pending leer
Es wird nie das echte GitHub angesprochen; Datenordner und Port sind eigene.
"""
import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import paths  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

NAME = paths.APP_NAME
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # nie über einen Proxy


def log(msg):
    print(msg, flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, path, body=None, timeout=60):
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def health_version(base):
    try:
        return call(base, "/api/health", timeout=3).get("version")
    except (OSError, ValueError, urllib.error.URLError):
        return None


def wait_until(cond, seconds, what):
    end = time.time() + seconds
    while time.time() < end:
        value = cond()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"Zeitüberschreitung ({seconds} s): {what}")


# ---------------------------------------------------------------- Fake-GitHub

class FakeGithub:
    def __init__(self):
        self.files = {}          # Dateiname -> Bytes
        self.release = None      # Version des "neuesten" Releases
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                if self.path == "/repos/Kevegl/opensteuerauszug-webapp/releases/latest" and outer.release:
                    base = f"http://127.0.0.1:{outer.port}"
                    zname = paths.asset_name(outer.release, "linux", paths.arch_tag())
                    body = json.dumps({
                        "tag_name": f"v{outer.release}", "html_url": f"{base}/rel", "draft": False, "prerelease": False,
                        "body": f"Testrelease {outer.release}",
                        "assets": [{"name": n, "size": len(outer.files[n]), "browser_download_url": f"{base}/dl/{n}"}
                                   for n in (zname, zname + ".sha256") if n in outer.files]}).encode()
                    return self.reply(200, body, "application/json")
                if self.path.startswith("/dl/") and self.path[4:] in outer.files:
                    return self.reply(200, outer.files[self.path[4:]], "application/octet-stream")
                self.reply(404, b"nicht gefunden", "text/plain")

            def reply(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add_zip(self, version, zip_bytes):
        name = paths.asset_name(version, "linux", paths.arch_tag())
        self.files[name] = zip_bytes
        self.files[name + ".sha256"] = f"{hashlib.sha256(zip_bytes).hexdigest()}  {name}\n".encode()


# ---------------------------------------------------------------- Builds

def build(python, out, version):
    version_file = ROOT / "app" / "VERSION"
    original = version_file.read_text(encoding="utf-8")
    try:
        version_file.write_text(version + "\n", encoding="utf-8")
        log(f"Baue Version {version} nach {out} ...")
        subprocess.run([python, str(ROOT / "packaging" / "build_app.py"), "--out", str(out)], check=True, cwd=str(ROOT))
    finally:
        version_file.write_text(original, encoding="utf-8")


def find_build(out, version):
    zip_path = out / "dist" / paths.asset_name(version, paths.system_tag(), paths.arch_tag())
    return zip_path if zip_path.is_file() and (zip_path.parent / (zip_path.name + ".sha256")).is_file() else None


def broken_zip(version):
    """Gültige Struktur, aber die Programmdatei startet nicht (Exit 3)."""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(paths.program_exe_relpath("linux").as_posix())
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        z.writestr(info, "#!/bin/sh\necho 'absichtlich kaputt' >&2\nexit 3\n")
    return buf.getvalue()


def extract(zip_path, dest):
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = dest / info.filename
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if (info.external_attr >> 16) & 0o111:
                target.chmod(0o755)


def kill_everything_under(folder):
    """Beendet alle Prozesse, deren Programmdatei unter folder liegt (die per Neustart gestarteten sind keine Kinder)."""
    me = os.getpid()
    for pid_dir in Path("/proc").glob("[0-9]*"):
        try:
            exe = os.readlink(pid_dir / "exe")
        except OSError:
            continue
        if int(pid_dir.name) != me and exe.startswith(str(folder)):
            try:
                os.kill(int(pid_dir.name), signal.SIGKILL)
            except OSError:
                pass


# ---------------------------------------------------------------- Test

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable, help="Python mit PyInstaller und dem Tool (Build-venv)")
    ap.add_argument("--work", type=Path, help="Arbeitsordner (vorhandene Builds werden wiederverwendet)")
    ap.add_argument("--keep", action="store_true", help="Arbeitsordner nicht löschen")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        raise SystemExit("update_e2e.py läuft nur unter Linux (Prozessverwaltung über /proc).")

    version_a = (ROOT / "app" / "VERSION").read_text(encoding="utf-8").strip()
    parts = version_a.split(".")
    version_b = ".".join(parts[:2] + [str(int(parts[2]) + 1)])
    version_c = ".".join(parts[:2] + [str(int(parts[2]) + 2)])
    work = args.work or Path(tempfile.mkdtemp(prefix="sa-e2e-"))
    work.mkdir(parents=True, exist_ok=True)
    results = []
    app_proc = None
    try:
        for version, sub in ((version_a, "build-a"), (version_b, "build-b")):
            if not find_build(work / sub, version):
                build(args.python, work / sub, version)
        zip_a, zip_b = find_build(work / "build-a", version_a), find_build(work / "build-b", version_b)
        assert zip_a and zip_b
        assert (ROOT / "app" / "VERSION").read_text(encoding="utf-8").strip() == version_a, "VERSION nicht zurückgesetzt"

        run_a = work / "programm-a"
        shutil.rmtree(run_a, ignore_errors=True)
        extract(zip_a, run_a)
        exe_a = run_a / paths.program_exe_relpath("linux")
        data = work / "daten"
        shutil.rmtree(data, ignore_errors=True)

        github = FakeGithub()
        github.add_zip(version_b, zip_b.read_bytes())
        github.release = version_b
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = dict(os.environ, STEUERAUSZUG_DATA_DIR=str(data), STEUERAUSZUG_PORT=str(port),
                   STEUERAUSZUG_UPDATE_API=f"http://127.0.0.1:{github.port}", NO_PROXY="127.0.0.1,localhost",
                   no_proxy="127.0.0.1,localhost")
        for var in ("STEUERAUSZUG_NO_BROWSER", "STEUERAUSZUG_NO_WINDOW", "STEUERAUSZUG_BUNDLED_TOOL",
                    "STEUERAUSZUG_DELEGATED", "STEUERAUSZUG_TRIAL"):
            env.pop(var, None)
        app_proc = subprocess.Popen([str(exe_a), "--no-browser", "--no-window"], env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def running_version():
            return health_version(base)

        def status_version():
            return call(base, "/api/status", timeout=30)["app_version_number"]

        wait_until(lambda: running_version() == version_a, 120, f"Version {version_a} startet")
        st = call(base, "/api/status", timeout=30)
        assert st["program_mode"] is True and st["app_version_number"] == version_a
        results.append(f"A läuft: {version_a}")

        # ---- Fall 1: Update
        chk = call(base, "/api/app/update/check", {})
        assert chk["ok"] and chk["latest"] == version_b and chk["update_available"] and chk["installable"], chk
        assert chk["notes"] == f"Testrelease {version_b}"
        upd = call(base, "/api/app/update")
        assert upd["last_check"]["update_available"] is True and upd["auto_check"] is True, upd
        job = call(base, "/api/app/update/apply", {})["job_id"]
        snap = wait_until(lambda: (lambda j: j if j["status"] != "running" else None)(call(base, f"/api/jobs/{job}")),
                          180, "Installation endet")
        assert snap["status"] == "done", snap["lines"][-5:]
        log("  Job-Log: " + " | ".join(snap["lines"][-4:]))
        upd = call(base, "/api/app/update")
        assert upd["restart_required"] and upd["pending"] == version_b, upd
        assert running_version() == version_a            # bis zum Neustart läuft noch A
        call(base, "/api/app/restart", {})
        wait_until(lambda: running_version() == version_b, 150, f"nach dem Neustart antwortet {version_b}")
        assert status_version() == version_b
        state = json.loads((data / "versions" / "state.json").read_text())
        assert (state["current"], state["previous"], state["pending"]) == (version_b, version_a, None), state
        upd = call(base, "/api/app/update")
        assert upd["can_rollback"] and upd["previous"] == version_a and upd["restart_required"] is False, upd
        assert upd["last_check"]["update_available"] is False
        results.append(f"Update: {version_a} -> {version_b} (current={state['current']}, previous={state['previous']})")

        # ---- Fall 2: Rollback
        rb = call(base, "/api/app/update/rollback", {})
        assert rb["restarting"] and rb["version"] == version_a, rb
        wait_until(lambda: running_version() == version_a, 150, f"nach dem Rollback antwortet {version_a}")
        assert status_version() == version_a
        state = json.loads((data / "versions" / "state.json").read_text())
        assert (state["current"], state["previous"], state["pending"]) == (version_a, None, None), state
        wait_until(lambda: not (data / "versions" / version_b).exists(), 30, f"versions/{version_b} wird aufgeräumt")
        assert call(base, "/api/app/update")["can_rollback"] is False
        results.append(f"Rollback: {version_b} -> {version_a}; versions/{version_b} aufgeräumt")

        # ---- Fall 3: kaputte Version -> automatisch zurück
        github.add_zip(version_c, broken_zip(version_c))
        github.release = version_c
        chk = call(base, "/api/app/update/check", {})
        assert chk["latest"] == version_c and chk["installable"], chk
        job = call(base, "/api/app/update/apply", {})["job_id"]
        snap = wait_until(lambda: (lambda j: j if j["status"] != "running" else None)(call(base, f"/api/jobs/{job}")),
                          120, "Installation von C endet")
        assert snap["status"] == "done", snap["lines"][-5:]
        assert call(base, "/api/app/update")["pending"] == version_c
        call(base, "/api/app/restart", {})
        t0 = time.time()
        wait_until(lambda: (lambda u: u if u and u.get("failed") else None)(
            call(base, "/api/app/update") if running_version() == version_a else None), 150,
            f"nach dem Neustart läuft wieder {version_a} mit failed={version_c}")
        upd = call(base, "/api/app/update")
        assert running_version() == version_a and status_version() == version_a
        assert upd["failed"]["version"] == version_c and upd["pending"] is None and upd["restart_required"] is False, upd
        state = json.loads((data / "versions" / "state.json").read_text())
        assert state["pending"] is None and state["failed"]["version"] == version_c and state["current"] == version_a, state
        assert not (data / "versions" / version_c).exists(), "kaputte Version wurde nicht aufgeräumt"
        results.append(f"Kaputte Version {version_c}: nach {time.time() - t0:.0f} s läuft wieder {version_a}; "
                       f"failed={version_c} ({state['failed']['reason']})")

        # ---- aufräumen: sauber beenden
        call(base, "/api/app/quit", {})
        wait_until(lambda: running_version() is None, 60, "Programm beendet sich")
        assert github.requests and all(r.startswith(("/repos/", "/dl/")) for r in github.requests)
    except BaseException:
        for logf in (work / "daten" / "logs" / "app.log",):
            if logf.is_file():
                log("--- app.log (Ende) ---\n" + logf.read_text(encoding="utf-8", errors="replace")[-3000:])
        raise
    finally:
        kill_everything_under(work)
        if app_proc is not None and app_proc.poll() is None:
            app_proc.kill()
        if not args.keep and not args.work:
            shutil.rmtree(work, ignore_errors=True)
    log("")
    for line in results:
        log("✓ " + line)
    log("UPDATE-E2E OK")


if __name__ == "__main__":
    main()
