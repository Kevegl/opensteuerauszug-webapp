#!/usr/bin/env python3
"""Kurztest des gebauten Programms (läuft in GitHub Actions auf Windows und macOS, geht auch unter Linux).

  python tests/smoke_app.py -- <Pfad zum Programm>    z. B. dist/Steuerauszug-Assistent/Steuerauszug-Assistent

Startet das Programm mit --no-browser --no-window, eigenem Datenordner und eigenem Port und prüft: Status
(Version, mitgeliefertes Tool), Tool-Modus (--run-tool ... --help, nicht erlaubte Module), zweiter Start erkennt die
laufende Instanz, Beenden über /api/app/quit. Kein pytest-Test; nur Standardbibliothek.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("SMOKE_PORT", "18765"))
BASE = f"http://127.0.0.1:{PORT}"

for _s in (sys.stdout, sys.stderr):   # Windows-Konsole in GitHub Actions: cp1252 -> UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def call(path, body=None, timeout=60):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def status_code(path, body):
    try:
        call(path, body)
        return 200
    except urllib.error.HTTPError as exc:
        return exc.code


def main():
    if "--" not in sys.argv or len(sys.argv) <= sys.argv.index("--") + 1:
        raise SystemExit("Aufruf: python tests/smoke_app.py -- <Pfad zum Programm>")
    cmd = sys.argv[sys.argv.index("--") + 1:]
    version = (ROOT / "app" / "VERSION").read_text(encoding="utf-8").strip()
    data = Path(tempfile.mkdtemp(prefix="sa-smoke-"))
    env = dict(os.environ, STEUERAUSZUG_DATA_DIR=str(data), STEUERAUSZUG_PORT=str(PORT),
               STEUERAUSZUG_NO_BROWSER="1", STEUERAUSZUG_NO_WINDOW="1")
    env.pop("STEUERAUSZUG_BUNDLED_TOOL", None)
    print("Starte:", " ".join(cmd), flush=True)
    proc = subprocess.Popen([*cmd, "--no-browser", "--no-window"], env=env, stdin=subprocess.DEVNULL)
    try:
        st = None
        for _ in range(240):
            try:
                st = call("/api/status", timeout=30)
                break
            except (OSError, ValueError):
                if proc.poll() is not None:
                    raise SystemExit(f"FEHLER: Programm vorzeitig beendet (Code {proc.returncode})")
                time.sleep(0.5)
        else:
            raise SystemExit("FEHLER: Programm antwortet nicht innerhalb von 120 s")
        assert st["app"] == "steuerauszug-assistent", st
        assert st["app_version_number"] == version, (st["app_version_number"], version)
        assert st["bundled"] is True and st["ok"] is True, {k: st.get(k) for k in ("bundled", "ok", "message")}
        assert Path(st["data_dir"]).resolve() == data.resolve(), st["data_dir"]
        assert st.get("bundled_text", "").startswith("mitgeliefert, Version "), st.get("bundled_text")
        print("✓ läuft:", version, "|", st["bundled_text"], flush=True)

        upd = call("/api/app/update")
        assert upd["mode"] == "program" and upd["is_git"] is False, upd
        assert status_code("/api/tool/install", {}) == 409
        assert status_code("/api/open-folder", {"which": "/etc"}) == 400
        print("✓ Programm-Modus: Tool-Installation gesperrt, Update-Karte, open-folder nur feste Ziele", flush=True)

        # Tool-Modus: das Programm ruft sich selbst für opensteuerauszug auf
        for module, needle in (("opensteuerauszug.steuerauszug", "Usage"), ("opensteuerauszug.kursliste", "download")):
            res = subprocess.run([cmd[0], "--run-tool", module, "--help"], env=env, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=180, stdin=subprocess.DEVNULL)
            assert res.returncode == 0, (module, res.returncode, res.stdout[-500:], res.stderr[-1500:])
            assert needle in res.stdout, (module, res.stdout[-500:])
        res = subprocess.run([cmd[0], "--run-tool", "os", "--help"], env=env, capture_output=True, text=True,
                             timeout=60, stdin=subprocess.DEVNULL)
        assert res.returncode == 2, res.returncode
        res = subprocess.run([cmd[0], "--tool-kursliste-dir"], env=env, capture_output=True, text=True, timeout=120,
                             stdin=subprocess.DEVNULL)
        assert res.returncode == 0 and res.stdout.strip(), (res.returncode, res.stderr[-500:])
        print("✓ Tool-Modus: --run-tool (steuerauszug, kursliste) mit Exit 0, fremdes Modul abgewiesen, Kursliste-Ordner:",
              res.stdout.strip(), flush=True)

        second = subprocess.run([*cmd, "--no-browser", "--no-window"], env=env, timeout=120, stdin=subprocess.DEVNULL)
        assert second.returncode == 0, f"zweiter Start: Code {second.returncode}"
        assert proc.poll() is None, "erste Instanz wurde beendet"
        print("✓ zweiter Start erkennt die laufende Instanz", flush=True)

        assert call("/api/app/quit", {})["quitting"] is True
        code = proc.wait(timeout=60)
        assert code == 0, f"Exit-Code nach /api/app/quit: {code}"
        try:
            call("/api/health", timeout=2)
            raise SystemExit("FEHLER: Server läuft nach /api/app/quit weiter")
        except (OSError, urllib.error.URLError):
            pass
        print("✓ /api/app/quit beendet das Programm sauber (Exit 0)", flush=True)
    except BaseException:
        log = data / "logs" / "app.log"
        if log.is_file():
            print("--- app.log (Ende) ---\n" + log.read_text(encoding="utf-8", errors="replace")[-3000:], flush=True)
        raise
    finally:
        if proc.poll() is None:
            try:
                call("/api/app/quit", {}, timeout=5)
                proc.wait(20)
            except Exception:  # noqa: BLE001
                proc.kill()
        shutil.rmtree(data, ignore_errors=True)
    print("SMOKE-TEST OK")


if __name__ == "__main__":
    main()
