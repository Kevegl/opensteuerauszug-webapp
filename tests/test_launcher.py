"""Startprogramm: Port-/Health-Prüfung und kompletter Lauf im selben Prozess (ohne Browser und Fenster)."""
import socket
import threading
import time
import urllib.request

import pytest

from app import launcher, main


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_args_and_env_flags(monkeypatch):
    a = launcher.parse_args(["--no-browser", "--no-window"])
    assert a.no_browser and a.no_window
    monkeypatch.setenv("STEUERAUSZUG_NO_BROWSER", "1")
    assert launcher.env_flag("STEUERAUSZUG_NO_BROWSER") and not launcher.env_flag("STEUERAUSZUG_NO_WINDOW")


def test_port_free_and_foreign_listener_is_not_our_app():
    port = _free_port()
    l = launcher.Launcher(port, open_browser=False, use_window=False)
    assert l.port_free() is True and l.health(timeout=0.5) is False
    srv = socket.socket()
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    try:
        assert l.port_free() is False
        assert l.health(timeout=0.5) is False   # nimmt keine Verbindung an / kein HTTP
    finally:
        srv.close()


def test_foreign_port_returns_error(monkeypatch):
    port = _free_port()
    msgs = []
    monkeypatch.setattr(launcher, "fail", msgs.append)
    srv = socket.socket()
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    try:
        assert launcher.Launcher(port, False, False).run() == 1
    finally:
        srv.close()
    assert msgs and str(port) in msgs[0]


def test_full_run_and_quit_endpoint(monkeypatch):
    port = _free_port()
    monkeypatch.setattr(launcher, "webbrowser", type("W", (), {"open": staticmethod(lambda url: None)}))
    main.quit_event.clear()
    l = launcher.Launcher(port, open_browser=False, use_window=False)
    result = []
    t = threading.Thread(target=lambda: result.append(l.run()), daemon=True)
    t.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not l.health(timeout=1):
            time.sleep(0.2)
        assert l.health(timeout=2) is True
        # Zweite Instanz erkennt die laufende App und beendet sich sofort
        assert launcher.Launcher(port, open_browser=False, use_window=False).run() == 0
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/app/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        assert urllib.request.urlopen(req, timeout=5).status == 200
        t.join(20)
        assert not t.is_alive() and result == [0]
        assert l.health(timeout=0.5) is False
    finally:
        l.shutdown()
        main.quit_event.clear()
        main.quit_via_event = False
