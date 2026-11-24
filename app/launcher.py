#!/usr/bin/env python3
"""Startprogramm des Steuerauszug-Assistenten (Einstiegspunkt der .exe/.app, auch per Python nutzbar).

  python -m app.launcher                 startet die App, öffnet den Browser und zeigt ein Statusfenster
  python -m app.launcher --no-browser    ohne den Browser zu öffnen
  python -m app.launcher --no-window     ohne Statusfenster (nur Konsole)

Umgebung: STEUERAUSZUG_DATA_DIR (Datenordner), STEUERAUSZUG_PORT, STEUERAUSZUG_NO_BROWSER=1,
STEUERAUSZUG_NO_WINDOW=1.

Das Startprogramm
  * legt den Datenordner an (und kopiert einmalig Daten aus dem alten Projektordner hinein),
  * öffnet nur den Browser, wenn die App schon läuft (keine doppelte Instanz),
  * meldet einen fremd belegten Port,
  * startet sonst den Server (uvicorn) im selben Prozess in einem Thread, nur auf 127.0.0.1,
  * beendet sich, wenn die App über "Beenden" im Fenster oder "App beenden" im Browser geschlossen wird.

Der Tool-Modus (--run-tool, --tool-kursliste-dir) wird ganz am Anfang behandelt, bevor irgendetwas
anderes importiert wird: das Programm ruft sich damit selbst als Subprozess für opensteuerauszug auf.
"""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False) and not __package__:
    # Als Skript gestartet (python app/launcher.py): Projektordner statt app/ in den Suchpfad
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from app import toolrun  # noqa: E402  (nur Standardbibliothek)

if toolrun.is_tool_invocation(sys.argv):
    sys.exit(toolrun.main())

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import webbrowser  # noqa: E402

from app import paths  # noqa: E402

TITLE = "Steuerauszug-Assistent"
APP_ID = "steuerauszug-assistent"
HEALTH_WAIT = 60
LOG_MAX_BYTES = 2 * 1024 * 1024
FROZEN = paths.is_frozen()


def say(text=""):
    try:
        print(text, flush=True)
    except (OSError, ValueError):
        pass


def fail(text):
    """Fehlermeldung: Konsole/Protokoll und, wo es keine Konsole gibt, zusätzlich als Fenster."""
    say("FEHLER: " + text)
    if not FROZEN:
        return
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)
        elif sys.platform == "darwin":
            esc = text.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display alert "{TITLE}" message "{esc}" as critical'],
                           timeout=300, check=False)
        else:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror(TITLE, text)
            root.destroy()
    except Exception:  # noqa: BLE001  (keine Anzeige möglich, die Meldung steht im Protokoll)
        pass


def redirect_output(data_dir):
    """Ohne Konsole (Windows-Programm, macOS-App): Ausgabe nach <Datenordner>/logs/app.log."""
    no_console = sys.stdout is None or sys.stderr is None
    if not no_console and not (FROZEN and not sys.stdout.isatty()):
        return None
    try:
        logs = Path(data_dir) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / "app.log"
        if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
            log.replace(logs / "app.log.1")
        handle = open(log, "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    except OSError:
        handle = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        log = None
    sys.stdout = sys.stderr = handle
    print(f"--- Start {time.strftime('%Y-%m-%d %H:%M:%S')} (Version {paths.version()}) ---", flush=True)
    return log


class Launcher:
    def __init__(self, port, open_browser=True, use_window=True):
        self.port = port
        self.url = f"http://127.0.0.1:{port}/"
        self.open_browser = open_browser
        self.use_window = use_window
        self.data = paths.data_dir()
        self.server = None
        self.thread = None
        self.appmain = None
        self.status = "starting"      # starting | running | stopping
        self.stopping = False
        self.error = None

    # ---- Prüfen, ob die App (schon) läuft
    def health(self, timeout=2):
        for path in ("api/health", "api/status"):      # ältere Versionen haben nur /api/status
            try:
                with urllib.request.urlopen(self.url + path, timeout=timeout) as r:  # noqa: S310
                    body = json.loads(r.read(1_000_000).decode("utf-8", "replace"))
                return r.status == 200 and body.get("app") == APP_ID
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                return False
            except (OSError, ValueError):
                return False
        return False

    def port_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name == "posix":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", self.port))
                return True
            except OSError:
                return False

    # ---- Server im selben Prozess
    def start_server(self):
        import uvicorn
        from app import main as appmain
        appmain.quit_via_event = True
        self.appmain = appmain
        config = uvicorn.Config(appmain.app, host="127.0.0.1", port=self.port, log_level="info",
                                access_log=False, use_colors=False, timeout_graceful_shutdown=3)
        self.server = uvicorn.Server(config)

        def run():
            try:
                self.server.run()
            except BaseException:  # noqa: BLE001  (SystemExit bei belegtem Port, sonst Absturz)
                self.error = traceback.format_exc()
                say(self.error)

        self.thread = threading.Thread(target=run, name="uvicorn", daemon=True)
        self.thread.start()

    def wait_healthy(self):
        deadline = time.time() + HEALTH_WAIT
        while time.time() < deadline:
            if not self.thread.is_alive():
                return False
            if self.health(timeout=2):
                return True
            time.sleep(0.4)
        return False

    def should_stop(self):
        return (self.stopping or (self.appmain is not None and self.appmain.quit_event.is_set())
                or (self.thread is not None and not self.thread.is_alive()))

    def request_stop(self):
        self.stopping = True
        self.status = "stopping"

    def shutdown(self):
        self.status = "stopping"
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(15)

    # ---- Aktionen für das Statusfenster
    def open_in_browser(self):
        webbrowser.open(self.url)

    def open_data_folder(self):
        from app.platform_utils import open_folder
        try:
            paths.ensure_data_dir(self.data)
            open_folder(self.data)
        except OSError as exc:
            say(f"Datenordner konnte nicht geöffnet werden: {exc}")

    # ---- Ablauf
    def run(self):
        if self.health(timeout=1):
            say(f"{TITLE} läuft bereits: {self.url}")
            if self.open_browser:
                webbrowser.open(self.url)
            return 0
        if not self.port_free():
            fail(f"Port {self.port} ist von einem anderen Programm belegt. Bitte dieses Programm beenden "
                 f"oder einen anderen Port wählen (Umgebungsvariable STEUERAUSZUG_PORT, z. B. 8766).")
            return 1
        self.start_server()
        if not self.wait_healthy():
            self.shutdown()
            fail("Der Steuerauszug-Assistent startet nicht. Details stehen im Protokoll: "
                 + str(self.data / "logs" / "app.log"))
            return 1
        self.status = "running"
        say("")
        say(f"  {TITLE} läuft auf diesem Computer")
        say(f"  Adresse: {self.url}")
        say(f"  Daten:   {self.data}")
        say("  Beenden: Knopf \"App beenden\" im Browser, \"Beenden\" im Statusfenster oder Strg+C.")
        say("")
        if self.open_browser:
            webbrowser.open(self.url)
        try:
            window = self.make_window() if self.use_window else None
            if window is not None:
                window.run()
            else:
                while not self.should_stop():
                    time.sleep(0.3)
        except KeyboardInterrupt:
            pass
        self.shutdown()
        say(f"{TITLE} wurde beendet.")
        return 0

    def make_window(self):
        """Statusfenster, oder None ohne tkinter bzw. ohne Bildschirm (dann nur Konsole)."""
        try:
            from app.status_window import StatusWindow
            return StatusWindow(self)
        except Exception as exc:  # noqa: BLE001  (ImportError ohne tkinter, TclError ohne Display)
            say(f"Kein Statusfenster ({exc.__class__.__name__}); die App läuft ohne Fenster weiter.")
            return None


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="launcher", description=TITLE)
    ap.add_argument("--no-browser", action="store_true", help="Browser nicht öffnen")
    ap.add_argument("--no-window", action="store_true", help="kein Statusfenster, nur Konsole")
    return ap.parse_args(argv)


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "ja")


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        data = paths.ensure_data_dir()
    except OSError as exc:
        fail(f"Der Datenordner {paths.data_dir()} kann nicht angelegt werden: {exc}")
        return 1
    redirect_output(data)
    paths.migrate_legacy(data=data, log=say)
    launcher = Launcher(
        paths.port(),
        open_browser=not (args.no_browser or env_flag("STEUERAUSZUG_NO_BROWSER")),
        use_window=not (args.no_window or env_flag("STEUERAUSZUG_NO_WINDOW")),
    )
    return launcher.run()


if __name__ == "__main__":
    sys.exit(main())
