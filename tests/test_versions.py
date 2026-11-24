"""Versionswechsel im Startprogramm (app/versions.py): pending -> current, Rückfall, Aufräumen. Alles mit Fakes."""
import os
import sys
from pathlib import Path

import pytest

from app import paths, versions


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def make_version(data, version):
    exe = versions.exe_in(versions.version_dir(data, version))
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


def make_original(tmp_path, version="1.5.0"):
    exe = tmp_path / "orig" / "Steuerauszug-Assistent"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    return exe


class FakeProc:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = -15

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.exit_code


class Fake:
    """Sammelt Starts; probe antwortet nach ``healthy_after`` Aufrufen mit ``child_version``."""
    def __init__(self, child_version=None, healthy_after=2, proc=None, running=None):
        self.started = []
        self.child_version = child_version
        self.healthy_after = healthy_after
        self.calls = 0
        self.proc = proc or FakeProc()
        self.running = running
        self.t = 0.0

    def popen(self, cmd, env):
        self.started.append((list(cmd), env))
        return self.proc

    def probe(self, port, timeout=2):
        if not self.started:
            return self.running
        self.calls += 1
        return self.child_version if self.child_version and self.calls >= self.healthy_after else None

    def sleep(self, s):
        self.t += s

    def clock(self):
        return self.t


def run_delegate(data, fake, own_version, own_exe, env=None, wait=5, argv=("--no-browser",)):
    return versions.delegate(data, own_version, own_exe, list(argv), port=1, env=env or {}, popen=fake.popen,
                             probe=fake.probe, sleep=fake.sleep, clock=fake.clock, wait=wait, say=lambda m: None)


# ---------------------------------------------------------------- Versionen / Zustand

def test_version_compare_is_numeric():
    assert versions.is_newer("1.10.0", "1.9.0") and versions.is_newer("v1.10.0", "1.9.9")
    assert not versions.is_newer("1.5.0", "1.5.0") and not versions.is_newer("1.4.9", "1.5.0")
    assert not versions.is_newer("kaputt", "1.0.0") and not versions.is_newer("1.0.0", None)
    assert versions.norm_version("v1.5.0") == "1.5.0"
    for bad in ("../x", "1.5.0/../..", "", None, "1.5.0-beta", "1"):
        assert versions.norm_version(bad) is None


def test_read_state_tolerates_garbage(data):
    assert versions.read_state(data)["current"] is None
    versions.versions_root(data).mkdir()
    versions.state_file(data).write_text("{kaputt")
    assert versions.read_state(data) == {"current": None, "previous": None, "pending": None, "failed": None,
                                         "original": None}
    versions.state_file(data).write_text('{"current": "../../etc", "pending": "1.5.1", "failed": "x"}')
    s = versions.read_state(data)
    assert s["current"] is None and s["pending"] == "1.5.1" and s["failed"] is None


def test_version_dir_rejects_path_tricks(data):
    with pytest.raises(ValueError):
        versions.version_dir(data, "../../x")


def test_clean_env_drops_pyinstaller_vars():
    env = versions.clean_env({"_PYI_APPLICATION_HOME_DIR": "x", "_MEIPASS2": "y", "LD_LIBRARY_PATH": "/tmp/_MEI",
                              "LD_LIBRARY_PATH_ORIG": "/usr/lib", "KEEP": "1", "STEUERAUSZUG_DELEGATED": "1"},
                             drop=("STEUERAUSZUG_DELEGATED",))
    assert env == {"LD_LIBRARY_PATH": "/usr/lib", "KEEP": "1"}


# ---------------------------------------------------------------- pending -> current

def test_pending_success_promotes_and_hands_over(data, tmp_path):
    orig = make_original(tmp_path)
    exe = make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "pending": "1.5.1"})
    fake = Fake(child_version="1.5.1")
    assert run_delegate(data, fake, "1.5.0", orig) == "delegated"
    cmd, env = fake.started[0]
    assert cmd == [exe, "--no-browser"]
    assert env[versions.DELEGATED_ENV] == "1" and env[versions.TRIAL_ENV] == "1"
    s = versions.read_state(data)
    assert (s["current"], s["previous"], s["pending"], s["failed"]) == ("1.5.1", "1.5.0", None, None)
    assert s["original"] == {"version": "1.5.0", "exe": str(orig)}
    assert fake.proc.terminated is False


def test_pending_health_timeout_rolls_back_and_marks_failed(data, tmp_path):
    orig = make_original(tmp_path)
    make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "pending": "1.5.1"})
    fake = Fake(child_version=None)
    assert run_delegate(data, fake, "1.5.0", orig, wait=3) == "self"
    s = versions.read_state(data)
    assert s["pending"] is None and s["current"] is None
    assert s["failed"]["version"] == "1.5.1" and "Sekunden" in s["failed"]["reason"]
    assert fake.proc.terminated is True
    assert not versions.version_dir(data, "1.5.1").exists()      # aufgeräumt
    # nicht erneut automatisch versucht
    fake2 = Fake(child_version="1.5.1")
    assert run_delegate(data, fake2, "1.5.0", orig) == "self" and fake2.started == []


def test_pending_child_crash_is_detected_early(data, tmp_path):
    orig = make_original(tmp_path)
    make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "pending": "1.5.1"})
    fake = Fake(proc=FakeProc(exit_code=1))
    assert run_delegate(data, fake, "1.5.0", orig, wait=60) == "self"
    assert fake.t < 5                                              # kein Warten bis zum Timeout
    assert "Code 1" in versions.read_state(data)["failed"]["reason"]


def test_pending_wrong_version_answering_does_not_count(data, tmp_path):
    orig = make_original(tmp_path)
    make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "pending": "1.5.1"})
    fake = Fake(child_version="1.5.0")                               # eine andere Instanz antwortet
    assert run_delegate(data, fake, "1.5.0", orig, wait=2) == "self"
    assert versions.read_state(data)["failed"]["version"] == "1.5.1"


def test_pending_missing_program_marks_failed(data, tmp_path):
    orig = make_original(tmp_path)
    versions.write_state(data, {**versions.read_state(data), "pending": "1.5.1"})
    fake = Fake()
    assert run_delegate(data, fake, "1.5.0", orig) == "self" and fake.started == []
    assert versions.read_state(data)["failed"]["version"] == "1.5.1"


# ---------------------------------------------------------------- current / manuell neuere Version

def test_current_newer_is_started_without_waiting(data, tmp_path):
    orig = make_original(tmp_path)
    exe = make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "current": "1.5.1", "previous": "1.5.0"})
    fake = Fake()
    assert run_delegate(data, fake, "1.5.0", orig) == "delegated"
    assert fake.started[0][0][0] == exe and fake.started[0][1][versions.DELEGATED_ENV] == "1"


def test_manually_newer_version_wins_over_current(data, tmp_path):
    orig = make_original(tmp_path)
    make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "current": "1.5.1", "pending": "1.5.2"})
    fake = Fake()
    assert run_delegate(data, fake, "1.6.0", orig) == "self" and fake.started == []
    s = versions.read_state(data)
    assert s["pending"] is None                                      # veraltet, wird verworfen
    assert s["original"]["version"] == "1.6.0"


def test_managed_program_returns_to_current_original(data, tmp_path):
    """Nach "Vorherige Version wiederherstellen": das Programm aus versions/ gibt an das Original ab."""
    orig = make_original(tmp_path)
    b = make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "current": "1.5.0",
                                "original": {"version": "1.5.0", "exe": str(orig)}})
    fake = Fake()
    assert run_delegate(data, fake, "1.5.1", b) == "delegated"
    assert fake.started[0][0][0] == orig


def test_delegated_child_and_running_instance_never_delegate(data, tmp_path):
    orig = make_original(tmp_path)
    make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "current": "1.5.1"})
    fake = Fake()
    assert run_delegate(data, fake, "1.5.0", orig, env={versions.DELEGATED_ENV: "1"}) == "self"
    assert fake.started == []
    fake = Fake(running="1.5.0")
    assert run_delegate(data, fake, "1.5.0", orig) == "self" and fake.started == []


def test_current_equal_to_own_runs_itself(data, tmp_path):
    b = make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "current": "1.5.1", "previous": "1.5.0"})
    fake = Fake()
    assert run_delegate(data, fake, "1.5.1", b) == "self" and fake.started == []


def test_popen_error_falls_back_to_self(data, tmp_path):
    orig = make_original(tmp_path)
    make_version(data, "1.5.1")
    versions.write_state(data, {**versions.read_state(data), "pending": "1.5.1"})
    fake = Fake()
    fake.popen = lambda cmd, env: (_ for _ in ()).throw(OSError("kaputt"))
    assert run_delegate(data, fake, "1.5.0", orig) == "self"
    assert versions.read_state(data)["failed"]["version"] == "1.5.1"


# ---------------------------------------------------------------- Aufräumen

def test_cleanup_keeps_current_previous_pending_only(data):
    for v in ("1.4.0", "1.5.0", "1.5.1", "1.5.2", "1.6.0"):
        make_version(data, v)
    (versions.versions_root(data) / "1.9.9.partial").mkdir()
    (versions.versions_root(data) / "notizen").mkdir()
    (versions.versions_root(data) / "datei.txt").write_text("x")
    versions.write_state(data, {**versions.read_state(data), "current": "1.5.2", "previous": "1.5.1",
                                "pending": "1.6.0"})
    removed = versions.cleanup(data)
    assert sorted(removed) == ["1.4.0", "1.5.0", "1.9.9.partial"]
    left = sorted(p.name for p in versions.versions_root(data).iterdir())
    assert left == ["1.5.1", "1.5.2", "1.6.0", "datei.txt", "notizen", "state.json"]


def test_cleanup_protects_running_program_and_symlinks(data, tmp_path):
    running = make_version(data, "1.4.0")
    outside = tmp_path / "wichtig"
    outside.mkdir()
    (outside / "daten.txt").write_text("bleibt")
    link = versions.versions_root(data) / "1.3.0"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("keine Symlinks")
    versions.write_state(data, {**versions.read_state(data), "current": "1.5.0"})
    versions.cleanup(data, protect=running)
    assert versions.version_dir(data, "1.4.0").exists()               # laufendes Programm bleibt
    assert not link.exists() and (outside / "daten.txt").read_text() == "bleibt"   # Ziel des Links unberührt


def test_cleanup_without_versions_dir(data):
    assert versions.cleanup(data) == []


def test_is_managed(data, tmp_path):
    assert versions.is_managed(data, make_version(data, "1.5.1")) is True
    assert versions.is_managed(data, make_original(tmp_path)) is False


def test_popen_detached_runs_child(tmp_path):
    out = tmp_path / "out.txt"
    proc = versions.popen_detached([sys.executable, "-c", f"open({str(out)!r}, 'w').write('ok')"],
                                   versions.clean_env())
    assert proc.wait(30) == 0 and out.read_text() == "ok"
