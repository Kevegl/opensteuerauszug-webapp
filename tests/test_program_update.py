"""Programm-Update: Prüfen, Herunterladen (Größenlimit, Prüfsumme), sicheres Entpacken, Rollback, Neustart.
Kein Netzwerk: Antworten und Downloads kommen aus einem Fake für ``program_update._open``."""
import hashlib
import io
import json
import os
import stat
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import launcher, main, paths, program_update as pu, versions
from app.jobs import JobManager

LINUX_EXE = "Steuerauszug-Assistent/Steuerauszug-Assistent"


def build_zip(entries=None, version="1.5.1", exe=LINUX_EXE):
    """ZIP wie von build_app.py (Linux-Layout). entries ersetzt den Inhalt komplett."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if entries is None:
            info = zipfile.ZipInfo(exe)
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            z.writestr(info, b"#!/bin/sh\necho hi\n")
            z.writestr("Steuerauszug-Assistent/_internal/app/VERSION", version)
        else:
            for name, content in entries:
                z.writestr(name, content)
    return buf.getvalue()


def release(version="1.5.1", system="linux", arch="x64", with_sha=True, body="Neu: <b>fett</b>", size=None,
            prerelease=False, draft=False, base="http://fake"):
    name = paths.asset_name(version, system, arch)
    assets = [{"name": name, "browser_download_url": f"{base}/dl/{name}", "size": size or 1000}]
    if with_sha:
        assets.append({"name": name + ".sha256", "browser_download_url": f"{base}/dl/{name}.sha256", "size": 90})
    return {"tag_name": f"v{version}", "html_url": f"{base}/rel/v{version}", "body": body, "assets": assets,
            "prerelease": prerelease, "draft": draft}


class FakeResp:
    def __init__(self, data, length=True, length_value=None):
        self._io = io.BytesIO(data)
        self.headers = {"Content-Length": str(length_value if length_value is not None else len(data))} if length else {}

    def read(self, n=-1):
        return self._io.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    def __init__(self):
        self.routes = {}
        self.requested = []

    def __call__(self, url, timeout=15, accept=None):
        self.requested.append(url)
        if url not in self.routes:
            raise OSError("keine Verbindung")
        value = self.routes[url]
        if isinstance(value, Exception):
            raise value
        return value() if callable(value) else FakeResp(value)


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(pu, "_open", fake)
    monkeypatch.setenv("STEUERAUSZUG_UPDATE_API", "http://fake")
    monkeypatch.setattr(paths, "system_tag", lambda platform_name=None: "linux")
    monkeypatch.setattr(paths, "arch_tag", lambda machine=None: "x64")
    fake.set_release = lambda rel: fake.routes.__setitem__(
        "http://fake/repos/Kevegl/opensteuerauszug-webapp/releases/latest", json.dumps(rel).encode())
    return fake


def serve(net, version="1.5.1", zip_bytes=None, sha=None, **kw):
    zip_bytes = zip_bytes if zip_bytes is not None else build_zip(version=version)
    name = paths.asset_name(version, "linux", "x64")
    net.set_release(release(version, **kw))
    net.routes[f"http://fake/dl/{name}"] = zip_bytes
    digest = sha if sha is not None else hashlib.sha256(zip_bytes).hexdigest()
    net.routes[f"http://fake/dl/{name}.sha256"] = f"{digest}  {name}\n".encode()
    return zip_bytes


class Job:
    def __init__(self):
        self.lines, self.results = [], []

    def log(self, line):
        self.lines.append(line)


# ---------------------------------------------------------------- Prüfen

def test_names_shared_with_build_script():
    assert paths.asset_name("1.5.0", "windows", "x64") == "Steuerauszug-Assistent-1.5.0-windows-x64.zip"
    assert paths.asset_name("1.5.0", "macos", "arm64") == "Steuerauszug-Assistent-1.5.0-macos-arm64.zip"
    assert (paths.system_tag("win32"), paths.system_tag("darwin"), paths.system_tag("linux")) == ("windows", "macos", "linux")
    assert (paths.arch_tag("AMD64"), paths.arch_tag("x86_64"), paths.arch_tag("aarch64"), paths.arch_tag("arm64")) == \
        ("x64", "x64", "arm64", "arm64")
    build_src = (paths.BASE_DIR / "packaging" / "build_app.py").read_text(encoding="utf-8")
    assert "paths.asset_name(" in build_src and "paths.program_exe_relpath(" in build_src   # nicht dupliziert
    assert paths.program_exe_relpath("windows").as_posix() == "Steuerauszug-Assistent/Steuerauszug-Assistent.exe"
    assert paths.program_exe_relpath("macos").as_posix() == \
        "Steuerauszug-Assistent.app/Contents/MacOS/Steuerauszug-Assistent"
    assert paths.program_exe_relpath("linux").as_posix() == "Steuerauszug-Assistent/Steuerauszug-Assistent"


def test_default_api_url_and_overrides(monkeypatch):
    monkeypatch.delenv("STEUERAUSZUG_UPDATE_API", raising=False)
    monkeypatch.delenv("STEUERAUSZUG_UPDATE_REPO", raising=False)
    assert pu.latest_url() == "https://api.github.com/repos/Kevegl/opensteuerauszug-webapp/releases/latest"
    monkeypatch.setenv("STEUERAUSZUG_UPDATE_API", "http://127.0.0.1:1234/")
    monkeypatch.setenv("STEUERAUSZUG_UPDATE_REPO", "x/y")
    assert pu.latest_url() == "http://127.0.0.1:1234/repos/x/y/releases/latest"


def test_url_allowed_only_https_or_local_test_server(monkeypatch):
    monkeypatch.delenv("STEUERAUSZUG_UPDATE_API", raising=False)
    assert pu._url_allowed("https://github.com/x") and not pu._url_allowed("http://127.0.0.1/x")
    assert not pu._url_allowed("file:///etc/passwd") and not pu._url_allowed("ftp://x/y")
    monkeypatch.setenv("STEUERAUSZUG_UPDATE_API", "http://127.0.0.1:5")
    assert pu._url_allowed("http://127.0.0.1:5/x") and not pu._url_allowed("http://evil.example/x")


def test_check_finds_newer_release_with_asset(net):
    serve(net, "1.10.0", body="Notizen <script>alert(1)</script>", size=4321)
    r = pu.check()
    assert r["ok"] and r["latest"] == "1.10.0" and r["current"] == "1.5.0" and r["update_available"] is True
    assert r["installable"] and r["asset_size"] == 4321 and r["html_url"].endswith("/v1.10.0")
    assert r["asset_url"].endswith("Steuerauszug-Assistent-1.10.0-linux-x64.zip") and r["sha_url"].endswith(".sha256")
    assert "<script>" in r["notes"]                       # unverändert als Text; die UI setzt textContent
    saved = json.loads((paths.data_dir() / "updates" / "status.json").read_text())
    assert saved["latest"] == "1.10.0" and saved["checked_ts"] > 0


def test_check_numeric_comparison_and_same_version(net):
    serve(net, "1.5.0")
    assert pu.check()["update_available"] is False
    net.set_release(release("1.4.9"))
    assert pu.check()["update_available"] is False
    net.set_release(release("1.9.0"))
    assert pu.check()["update_available"] is True


def test_check_ignores_prereleases_and_drafts(net):
    for kw in ({"prerelease": True}, {"draft": True}):
        net.set_release(release("9.9.9", **kw))
        r = pu.check()
        assert r["ok"] and r["update_available"] is False and r["latest"] is None


@pytest.mark.parametrize("system,arch", [("windows", "x64"), ("macos", "arm64"), ("linux", "x64"), ("linux", "arm64")])
def test_asset_selection_per_system(system, arch):
    rel = release("1.6.0", system, arch)
    rel["assets"] += [{"name": "Steuerauszug-Assistent-1.6.0-other-x64.zip", "browser_download_url": "https://x/other"}]
    r = pu.parse_release(rel, "1.5.0", system, arch)
    assert r["asset_name"] == f"Steuerauszug-Assistent-1.6.0-{system}-{arch}.zip" and r["installable"]
    other = "macos" if system != "macos" else "windows"
    r2 = pu.parse_release(rel, "1.5.0", other, "x64")
    assert r2["update_available"] and not r2["installable"] and "kein Programmpaket" in r2["install_problem"]


def test_missing_checksum_asset_blocks_installation(net):
    serve(net, "1.6.0", with_sha=False)
    r = pu.check()
    assert r["update_available"] and not r["installable"] and "Prüfsummen" in r["install_problem"]


def test_check_errors_are_german_and_not_raised(net):
    r = pu.check()                                          # keine Route -> OSError
    assert r["ok"] is False and r["error"] and "Verbindung" in r["error"] and r["update_available"] is False
    net.routes["http://fake/repos/Kevegl/opensteuerauszug-webapp/releases/latest"] = b"<html>"
    assert "Unerwartet" in pu.check()["error"] or pu.check()["error"]
    net.set_release({"tag_name": "nightly"})
    assert "Versionsnummer" in pu.check()["error"]


def test_auto_check_once_a_day_and_switchable(net, monkeypatch):
    serve(net, "1.6.0")
    t = pu.auto_check_if_due()
    t.join(10)
    assert (paths.data_dir() / "updates" / "status.json").exists()
    n = len(net.requested)
    assert pu.auto_check_if_due() is None and len(net.requested) == n          # heute schon geprüft
    st_file = paths.data_dir() / "updates" / "status.json"
    data = json.loads(st_file.read_text())
    data["checked_ts"] = time.time() - 25 * 3600
    st_file.write_text(json.dumps(data))
    assert pu.auto_check_due() is True
    from app import settings as st
    st.save_settings({**st.load_settings(), "auto_update_check": False})
    assert pu.auto_check_if_due() is None and len(net.requested) == n          # abgeschaltet
    assert st.update_settings(st.load_settings(), {"auto_update_check": True})["auto_update_check"] is True
    assert st.load_settings()["auto_update_check"] is False
    assert st.public_view(st.load_settings())["auto_update_check"] is False


def test_status_reports_update_after_check(net):
    serve(net, "1.6.0")
    pu.check()
    s = pu.status()
    assert s["mode"] == "program" and s["last_check"]["update_available"] is True and s["auto_check"] is True
    assert s["restart_required"] is False and s["can_rollback"] is False and s["failed"] is None


# ---------------------------------------------------------------- Installieren

def apply(net_unused=None):
    job = Job()
    ok = pu.job_apply()(job)
    return ok, job


def test_apply_installs_pending_version(net):
    serve(net, "1.5.1")
    ok, job = apply()
    assert ok, job.lines
    data = paths.data_dir()
    exe = versions.exe_in(versions.version_dir(data, "1.5.1"))
    assert exe.is_file() and exe.stat().st_mode & 0o111
    assert versions.read_state(data)["pending"] == "1.5.1"
    assert not (data / "updates" / "download.tmp").exists()
    assert not list((data / "versions").glob("*.partial"))
    assert any("Prüfsumme stimmt" in l for l in job.lines) and any("Neustart" in l for l in job.lines)
    s = pu.status()
    assert s["restart_required"] and s["pending"] == "1.5.1" and s["last_check"]["update_available"] is False
    # zweiter Aufruf: schon installiert
    ok2, job2 = apply()
    assert ok2 and any("bereits installiert" in l for l in job2.lines)


def test_apply_without_newer_version(net):
    serve(net, "1.5.0")
    ok, job = apply()
    assert not ok and not (paths.data_dir() / "versions").exists()


def test_wrong_checksum_aborts_without_installing(net):
    serve(net, "1.5.1", sha="0" * 64)
    ok, job = apply()
    assert not ok and any("Prüfsumme stimmt nicht" in l for l in job.lines)
    data = paths.data_dir()
    assert not versions.version_dir(data, "1.5.1").exists()
    assert versions.read_state(data)["pending"] is None
    assert not (data / "updates" / "download.tmp").exists()


def test_invalid_checksum_file_aborts(net):
    serve(net, "1.5.1")
    name = paths.asset_name("1.5.1", "linux", "x64")
    net.routes[f"http://fake/dl/{name}.sha256"] = b"nix"
    ok, job = apply()
    assert not ok and any("SHA-256" in l for l in job.lines)
    assert versions.read_state(paths.data_dir())["pending"] is None


def test_download_size_limit_content_length_and_streaming(net, monkeypatch):
    monkeypatch.setattr(pu, "MAX_DOWNLOAD", 500)
    big = build_zip(entries=[("Steuerauszug-Assistent/Steuerauszug-Assistent", os.urandom(5000))])
    serve(net, "1.5.1", zip_bytes=big, size=100)
    ok, job = apply()                                       # Content-Length ist ehrlich -> sofort abgelehnt
    assert not ok and any("zu groß" in l for l in job.lines)
    name = paths.asset_name("1.5.1", "linux", "x64")
    net.routes[f"http://fake/dl/{name}"] = lambda: FakeResp(big, length=False)    # ohne Content-Length
    ok, job = apply()
    assert not ok and any("Limit" in l for l in job.lines)
    net.routes[f"http://fake/dl/{name}"] = lambda: FakeResp(big, length_value=10)   # lügt über die Länge
    ok, job = apply()
    assert not ok
    assert not (paths.data_dir() / "updates" / "download.tmp").exists()
    assert not versions.version_dir(paths.data_dir(), "1.5.1").exists()


def test_announced_size_over_limit_is_refused_before_download(net, monkeypatch):
    monkeypatch.setattr(pu, "MAX_DOWNLOAD", 500)
    serve(net, "1.5.1", size=10_000)
    r = pu.check()
    assert r["update_available"] and not r["installable"] and "Limit" in r["install_problem"]
    name = paths.asset_name("1.5.1", "linux", "x64")
    ok, job = apply()
    assert not ok and f"http://fake/dl/{name}" not in net.requested


@pytest.mark.parametrize("bad", ["../evil.txt", "/etc/evil", "C:/evil.txt", "C:\\evil.txt", "a/../../evil.txt",
                                 "..\\evil.txt", "\\\\server\\share\\x", "a/b:stream"])
def test_zip_slip_is_rejected(net, tmp_path, bad):
    evil = build_zip(entries=[(LINUX_EXE, b"x"), (bad, b"boese")])
    serve(net, "1.5.1", zip_bytes=evil)
    ok, job = apply()
    assert not ok and any("Unsicher" in l or "Ungültig" in l for l in job.lines), job.lines
    data = paths.data_dir()
    assert not versions.version_dir(data, "1.5.1").exists() and not list((data / "versions").glob("*.partial"))
    assert not (tmp_path / "evil.txt").exists() and not (paths.data_dir().parent / "evil.txt").exists()


def test_symlink_member_rejected_outside_macos(net, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(LINUX_EXE, b"x")
        link = zipfile.ZipInfo("Steuerauszug-Assistent/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "/etc/passwd")
    serve(net, "1.5.1", zip_bytes=buf.getvalue())
    ok, job = apply()
    assert not ok and any("Symlink" in l for l in job.lines)
    assert not versions.version_dir(paths.data_dir(), "1.5.1").exists()


def test_macos_extraction_uses_ditto_and_checks_links(tmp_path):
    z = tmp_path / "x.zip"
    z.write_bytes(build_zip(entries=[("Steuerauszug-Assistent.app/Contents/MacOS/Steuerauszug-Assistent", b"x")]))
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        dest = tmp_path / "out"
        (dest / "Steuerauszug-Assistent.app" / "Contents" / "MacOS").mkdir(parents=True)
        (dest / "Steuerauszug-Assistent.app" / "Contents" / "MacOS" / "Steuerauszug-Assistent").write_text("x")
        (dest / "Steuerauszug-Assistent.app" / "Contents" / "Frameworks").symlink_to("MacOS")
        return type("R", (), {"returncode": 0, "stderr": ""})()

    assert pu.safe_extract(z, tmp_path / "out", "macos", run=fake_run) == 1
    assert calls[0][:3] == ["ditto", "-x", "-k"]
    (tmp_path / "out" / "Steuerauszug-Assistent.app" / "Contents" / "Evil").symlink_to("/etc")
    with pytest.raises(pu.UpdateError, match="Symlink"):
        pu._check_tree_links(tmp_path / "out")


def test_package_without_program_file_is_rejected(net):
    serve(net, "1.5.1", zip_bytes=build_zip(entries=[("Steuerauszug-Assistent/LIESMICH.txt", b"x")]))
    ok, job = apply()
    assert not ok and any("Programmdatei" in l for l in job.lines)
    assert not versions.version_dir(paths.data_dir(), "1.5.1").exists()


def test_broken_zip_and_unpacked_limit(net, monkeypatch):
    serve(net, "1.5.1", zip_bytes=b"das ist kein zip")
    ok, job = apply()
    assert not ok and any("kein gültiges ZIP" in l for l in job.lines)
    monkeypatch.setattr(pu, "MAX_UNPACKED", 5)
    serve(net, "1.5.1")
    ok, job = apply()
    assert not ok and any("zu groß" in l for l in job.lines)


def test_reinstall_after_failed_attempt_replaces_directory_and_clears_failed(net):
    data = paths.data_dir()
    versions.mark_failed(data, "1.5.1", "alt")
    serve(net, "1.5.1")
    ok, job = apply()
    assert ok
    assert versions.read_state(data)["failed"] is None and versions.read_state(data)["pending"] == "1.5.1"


def test_install_keeps_at_most_current_previous_pending(net):
    data = paths.data_dir()
    for v in ("1.3.0", "1.4.0", "1.4.5"):
        exe = versions.exe_in(versions.version_dir(data, v))
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
    versions.write_state(data, {**versions.read_state(data), "current": "1.4.5", "previous": "1.4.0"})
    serve(net, "1.5.1")
    ok, _ = apply()
    assert ok
    names = sorted(p.name for p in (data / "versions").iterdir() if p.is_dir())
    assert names == ["1.4.0", "1.4.5", "1.5.1"]


# ---------------------------------------------------------------- Endpunkte

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "jobs", JobManager())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "RESTART_DELAY", 0)
    exits = []
    monkeypatch.setattr(main, "exit_process", exits.append)
    monkeypatch.setattr(main, "restart_requested", False)
    main.quit_event.clear()
    c = TestClient(main.app)
    c.exits = exits
    yield c
    main.quit_event.clear()


def wait_for(cond, seconds=5):
    end = time.time() + seconds
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_restart_endpoint_in_program_mode_signals_launcher_and_injected_exit(client):
    res = client.post("/api/app/restart")
    assert res.status_code == 200 and res.json() == {"restarting": True}
    assert main.restart_requested is True and main.quit_event.is_set()
    assert wait_for(lambda: client.exits == [3])            # ohne Launcher: Prozess mit Neustart-Code beenden


def test_restart_endpoint_with_launcher_only_sets_event(client, monkeypatch):
    monkeypatch.setattr(main, "quit_via_event", True)
    assert client.post("/api/app/restart").status_code == 200
    time.sleep(0.2)
    assert client.exits == [] and main.restart_requested and main.quit_event.is_set()


def test_restart_refused_while_job_runs(client, monkeypatch):
    import threading
    gate = threading.Event()
    assert main.jobs.start("x", "x", lambda job: gate.wait(5) or True)
    try:
        assert client.post("/api/app/restart").status_code == 409
        assert client.post("/api/app/update/rollback").status_code == 409
        assert main.restart_requested is False
    finally:
        gate.set()


def prepare_rollback_state(data, current="1.5.1"):
    orig = data.parent / "orig-program"
    orig.write_text("x")
    versions.write_state(data, {"current": current, "previous": "1.5.0", "pending": None, "failed": None,
                                "original": {"version": "1.5.0", "exe": str(orig)}})


def test_rollback_endpoint_sets_state_and_restarts(client, monkeypatch):
    data = paths.data_dir()
    monkeypatch.setattr(paths, "version", lambda: "1.5.1")
    prepare_rollback_state(data)
    status = client.get("/api/app/update").json()
    assert status["can_rollback"] is True and status["previous"] == "1.5.0"
    res = client.post("/api/app/update/rollback")
    assert res.status_code == 200 and res.json() == {"restarting": True, "version": "1.5.0"}
    s = versions.read_state(data)
    assert (s["current"], s["previous"], s["pending"]) == ("1.5.0", None, None)
    assert main.restart_requested and wait_for(lambda: client.exits == [3])
    assert client.get("/api/app/update").json()["can_rollback"] is False


def test_rollback_without_previous_is_refused(client):
    res = client.post("/api/app/update/rollback")
    assert res.status_code == 409 and "vorherige Version" in res.json()["detail"]
    assert main.restart_requested is False


def test_rollback_not_available_in_python_variant(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert TestClient(main.app).post("/api/app/update/rollback").status_code == 400


def test_status_shows_failed_update(client):
    data = paths.data_dir()
    versions.mark_failed(data, "1.5.1", "Die neue Version wurde beendet (Code 1)")
    s = client.get("/api/app/update").json()
    assert s["failed"]["version"] == "1.5.1" and "Code 1" in s["failed"]["reason"]
    versions.mark_failed(data, "1.4.0", "x")                # ältere als die laufende: nicht mehr relevant
    assert client.get("/api/app/update").json()["failed"] is None


def test_apply_endpoint_runs_as_job_with_log(client, net):
    serve(net, "1.5.1")
    jid = client.post("/api/app/update/apply").json()["job_id"]
    assert wait_for(lambda: client.get(f"/api/jobs/{jid}").json()["status"] != "running", 15)
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "done" and any("Prüfsumme" in l for l in job["lines"])
    assert client.get("/api/app/update").json()["restart_required"] is True


def test_python_variant_update_endpoints_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    body = TestClient(main.app).get("/api/app/update").json()
    assert body["mode"] == "git"


# ---------------------------------------------------------------- Launcher: Neustart

def test_launcher_restart_command_and_env(monkeypatch):
    monkeypatch.setenv(versions.DELEGATED_ENV, "1")
    monkeypatch.setenv(versions.TRIAL_ENV, "1")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/x")
    got = []
    l = launcher.Launcher(1, True, True, argv=["--no-window"])
    l.spawn = lambda cmd, env: got.append((cmd, env))
    assert l.restart() == 0
    cmd, env = got[0]
    assert cmd == [sys.executable, "--no-window", "--no-browser"]
    assert versions.DELEGATED_ENV not in env and versions.TRIAL_ENV not in env and "_PYI_APPLICATION_HOME_DIR" not in env
    l2 = launcher.Launcher(1, argv=["--no-browser"])
    assert l2.restart_command() == [sys.executable, "--no-browser"]


def test_launcher_run_restarts_after_restart_endpoint(monkeypatch):
    import socket
    import threading
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher, "webbrowser", type("W", (), {"open": staticmethod(lambda url: None)}))
    monkeypatch.setattr(main, "restart_requested", False)
    main.quit_event.clear()
    l = launcher.Launcher(port, open_browser=False, use_window=False, argv=["--no-window"])
    spawned = []
    l.spawn = lambda cmd, env: spawned.append(cmd)
    result = []
    t = threading.Thread(target=lambda: result.append(l.run()), daemon=True)
    t.start()
    try:
        assert wait_for(lambda: l.health(timeout=1), 30)
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/app/restart", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        assert urllib.request.urlopen(req, timeout=5).status == 200
        t.join(30)
        assert not t.is_alive() and result == [0]
        assert spawned == [[sys.executable, "--no-window", "--no-browser"]]
        assert l.health(timeout=0.5) is False              # Server vor dem Neustart sauber beendet
    finally:
        l.shutdown()
        main.quit_event.clear()
        main.quit_via_event = False
        main.restart_requested = False
