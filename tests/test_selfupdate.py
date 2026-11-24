"""Selbst-Update: gefakte Git-Ausgaben plus Integrationstest mit echtem git."""
import shutil
import subprocess
from pathlib import Path

import pytest

from app import selfupdate as su


class FakeGit:
    """Antwortet auf git-Befehle anhand der Argumente (ohne 'git')."""

    def __init__(self, tmp_path, **over):
        (tmp_path / ".git").mkdir(exist_ok=True)
        self.base = tmp_path
        self.calls: list[list[str]] = []
        self.pip_calls: list[list[str]] = []
        self.pip_code = 0
        self.fail: dict[str, tuple[int, str, str]] = {}
        self.data = {
            "branch": "main", "commit": "abc1234", "version": "v1.0-2-gabc1234", "dirty": "",
            "upstream": "origin/main", "counts": "0\t0", "tag": "v1.1", "log": "", "head": "a" * 40,
            "diff": "", "new_head": "b" * 40,
        }
        self.data.update(over)
        self.merged = False

    def run(self, cmd, cwd, timeout):
        assert cmd[0] == "git" and cwd == self.base
        args = cmd[1:]
        self.calls.append(args)
        key = " ".join(args)
        for sub, res in self.fail.items():
            if sub in key:
                return res
        d = self.data
        if args[0] == "symbolic-ref":
            return (0, d["branch"] + "\n", "") if d["branch"] else (1, "", "")
        if args[:2] == ["rev-parse", "--short"]:
            return 0, d["commit"] + "\n", ""
        if args[0] == "describe" and "--abbrev=0" in args:
            return (0, d["tag"] + "\n", "") if d["tag"] else (128, "", "fatal: no tags")
        if args[0] == "describe":
            return 0, d["version"] + "\n", ""
        if args[0] == "status":
            return 0, d["dirty"], ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return (0, d["upstream"] + "\n", "") if d["upstream"] else (128, "", "no upstream")
        if args[:2] == ["rev-parse", "--verify"]:
            return 1, "", ""
        if args[0] == "rev-list":
            return 0, d["counts"] + "\n", ""
        if args[0] == "log":
            return 0, d["log"], ""
        if args == ["rev-parse", "HEAD"]:
            return 0, (d["new_head"] if self.merged else d["head"]) + "\n", ""
        if args[0] == "merge":
            self.merged = True
            return 0, "", ""
        if args[0] == "diff":
            return 0, d["diff"], ""
        return 0, "", ""

    def pip(self, cmd, cwd, log):
        self.pip_calls.append(cmd)
        log("pip output")
        return self.pip_code

    def deps(self):
        return su.Deps(run=self.run, run_streaming=self.pip, which=lambda n: "/usr/bin/git",
                       platform="linux", python="PY", base_dir=self.base)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    su.reset_state()
    monkeypatch.delenv("STEUERAUSZUG_LAUNCHER", raising=False)
    yield
    su.reset_state()


def _has(fake, *prefix):
    return any(c[:len(prefix)] == list(prefix) for c in fake.calls)


# ---------------------------------------------------------------- status

def test_status_no_git_dir(tmp_path):
    d = su.Deps(base_dir=tmp_path)
    s = su.status(d)
    assert s["is_git"] is False and s["reason"] == "kein Git-Klon"


def test_status_git_missing(tmp_path):
    (tmp_path / ".git").mkdir()
    s = su.status(su.Deps(which=lambda n: None, base_dir=tmp_path))
    assert s["is_git"] is False and s["reason"] == "git nicht installiert"


def test_status_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("STEUERAUSZUG_LAUNCHER", "1")
    fg = FakeGit(tmp_path)
    s = su.status(fg.deps())
    assert s["is_git"] and s["branch"] == "main" and s["commit"] == "abc1234"
    assert s["version"] == "v1.0-2-gabc1234" and s["dirty"] is False
    assert s["upstream"] == "origin/main" and s["blocker"] is None
    assert s["restart_supported"] is True and s["restart_required"] is False
    assert ["status", "--porcelain", "--untracked-files=no"] in fg.calls


def test_status_dirty_detached_and_fallback_upstream(tmp_path):
    fg = FakeGit(tmp_path, dirty=" M app/main.py\n", branch=None, upstream=None)
    fg.fail["rev-parse --verify"] = (0, "", "")
    s = su.status(fg.deps())
    assert s["branch"] is None and s["dirty"] is True and s["dirty_files"] == ["app/main.py"]
    assert s["upstream"] == "origin/main"
    assert "detached" in s["blocker"]


def test_status_dirty_blocker_mentions_stash(tmp_path):
    s = su.status(FakeGit(tmp_path, dirty=" M a.py\n").deps())
    assert "git stash" in s["blocker"] and "a.py" in s["blocker"]


# ---------------------------------------------------------------- check

def test_check_current(tmp_path):
    fg = FakeGit(tmp_path)
    r = su.check(fg.deps())
    assert r["ok"] and r["update_available"] is False and r["behind"] == 0
    assert ["fetch", "--tags", "--prune", "origin"] in fg.calls
    assert ["rev-list", "--left-right", "--count", "HEAD...origin/main"] in fg.calls


def test_check_behind_with_commits(tmp_path):
    log = "".join(f"h{i}\tÄnderung {i}\n" for i in range(3))
    fg = FakeGit(tmp_path, counts="0\t3", log=log)
    r = su.check(fg.deps())
    assert r["update_available"] is True and r["behind"] == 3 and r["latest_tag"] == "v1.1"
    assert r["commits"][0] == {"hash": "h0", "subject": "Änderung 0"}
    assert any(c[0] == "log" and "-n20" in c for c in fg.calls)
    assert su.status(fg.deps())["last_check"]["behind"] == 3


def test_check_no_tags(tmp_path):
    r = su.check(FakeGit(tmp_path, tag=None, counts="0\t1").deps())
    assert r["ok"] and r["latest_tag"] is None


def test_check_ahead(tmp_path):
    r = su.check(FakeGit(tmp_path, counts="2\t0").deps())
    assert r["ahead"] == 2 and r["update_available"] is False


def test_check_fetch_error_is_message_not_exception(tmp_path):
    fg = FakeGit(tmp_path)
    fg.fail["fetch"] = (128, "", "fatal: unable to access 'https://user:secret@github.com/x': Could not resolve host")
    r = su.check(fg.deps())
    assert r["ok"] is False and "fehlgeschlagen" in r["error"]
    assert "secret" not in r["error"]


def test_check_timeout_message(tmp_path):
    fg = FakeGit(tmp_path)
    fg.fail["fetch"] = (-1, "", "Zeitüberschreitung nach 30 s")
    assert "Internetverbindung" in su.check(fg.deps())["error"]


def test_check_without_git_clone(tmp_path):
    r = su.check(su.Deps(base_dir=tmp_path))
    assert r["ok"] is False and "git clone" in r["error"]


def test_check_never_raises(tmp_path):
    def boom(cmd, cwd, timeout):
        raise RuntimeError("kaputt")
    (tmp_path / ".git").mkdir()
    r = su.check(su.Deps(run=boom, which=lambda n: "/x/git", base_dir=tmp_path))
    assert r["ok"] is False and r["error"]


# ---------------------------------------------------------------- apply

def _apply(fg):
    logs: list[str] = []
    ok = su.run_apply(logs.append, fg.deps())
    return ok, logs


def test_apply_up_to_date(tmp_path):
    fg = FakeGit(tmp_path)
    ok, logs = _apply(fg)
    assert ok and "neuesten Stand" in logs[-1] and not _has(fg, "merge")
    assert su.status(fg.deps())["restart_required"] is False


def test_apply_blocked_detached(tmp_path):
    fg = FakeGit(tmp_path, branch=None, counts="0\t1")
    ok, logs = _apply(fg)
    assert not ok and "detached" in logs[-1] and not _has(fg, "fetch") and not _has(fg, "merge")


def test_apply_blocked_dirty(tmp_path):
    fg = FakeGit(tmp_path, dirty=" M app/main.py\n", counts="0\t1")
    ok, logs = _apply(fg)
    assert not ok and "app/main.py" in logs[-1] and "git stash" in logs[-1] and not _has(fg, "merge")


def test_apply_blocked_ahead(tmp_path):
    fg = FakeGit(tmp_path, counts="1\t2")
    ok, logs = _apply(fg)
    assert not ok and "divergiert" in logs[-1] and not _has(fg, "merge")


def test_apply_fetch_error(tmp_path):
    fg = FakeGit(tmp_path, counts="0\t1")
    fg.fail["fetch"] = (128, "", "fatal: could not resolve host")
    ok, logs = _apply(fg)
    assert not ok and "fehlgeschlagen" in logs[-1] and not _has(fg, "merge")


def test_apply_ff_without_requirements_change(tmp_path):
    fg = FakeGit(tmp_path, counts="0\t2", diff="app/main.py\nREADME.md\n")
    ok, logs = _apply(fg)
    assert ok
    assert ["merge", "--ff-only", "origin/main"] in fg.calls
    assert fg.pip_calls == []
    assert logs[-1] == "Update installiert: aaaaaaa → bbbbbbb. Bitte App neu starten."
    assert su.status(fg.deps())["restart_required"] is True
    assert not any(c[0] in ("clean",) or c[:2] == ["reset", "--hard"] for c in fg.calls)


def test_apply_installs_requirements_when_changed(tmp_path):
    fg = FakeGit(tmp_path, counts="0\t1", diff="requirements.txt\n")
    ok, logs = _apply(fg)
    assert ok and fg.pip_calls == [["PY", "-m", "pip", "install", "-r", "requirements.txt"]]
    assert "pip output" in logs


def test_apply_pip_failure_resets_keep(tmp_path):
    fg = FakeGit(tmp_path, counts="0\t1", diff="requirements.txt\n")
    fg.pip_code = 1
    ok, logs = _apply(fg)
    assert not ok
    assert ["reset", "--keep", "a" * 40] in fg.calls
    assert not any(c[:2] == ["reset", "--hard"] for c in fg.calls)
    assert su.status(fg.deps())["restart_required"] is False
    assert any("wiederhergestellt" in line for line in logs)


def test_apply_merge_failure(tmp_path):
    fg = FakeGit(tmp_path, counts="0\t1")
    fg.fail["merge"] = (128, "", "fatal: Not possible to fast-forward, aborting.")
    ok, logs = _apply(fg)
    assert not ok and "fast-forward" in logs[-1]


def test_job_apply_runs_in_job(tmp_path):
    from app.jobs import Job
    fg = FakeGit(tmp_path)
    job = Job("app-update", "t")
    assert su.job_apply(fg.deps())(job) is True
    assert job.lines


# ---------------------------------------------------------------- Restart

def test_schedule_restart_calls_exit_with_code():
    got = []
    t = su.schedule_restart(got.append, 0)
    t.join(2)
    assert got == [su.RESTART_EXIT_CODE] == [3]


# ---------------------------------------------------------------- Integration mit echtem git

pytestmark_git = pytest.mark.skipif(shutil.which("git") is None, reason="git fehlt")


def _git(cwd, *args):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "PATH": __import__("os").environ["PATH"], "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"}
    res = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


@pytestmark_git
def test_integration_real_git(tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    a, b = tmp_path / "a", tmp_path / "b"
    _git(tmp_path, "clone", str(bare), str(a))
    _git(a, "checkout", "-b", "main")
    (a / "requirements.txt").write_text("fastapi\n")
    (a / "f.txt").write_text("1\n")
    _git(a, "add", ".")
    _git(a, "commit", "-m", "erster")
    _git(a, "tag", "v1.0")
    _git(a, "push", "-u", "origin", "main", "--tags")
    _git(tmp_path, "clone", str(bare), str(b))
    _git(b, "checkout", "main")

    deps = su.Deps(base_dir=b, python="PY-nicht-benutzt")
    s = su.status(deps)
    assert s["is_git"] and s["branch"] == "main" and s["dirty"] is False and s["upstream"] == "origin/main"
    assert s["version"] == "v1.0"
    assert su.check(deps)["update_available"] is False

    (a / "f.txt").write_text("2\n")
    _git(a, "commit", "-am", "zweiter Commit")
    _git(a, "tag", "v1.1")
    _git(a, "push", "origin", "main", "--tags")

    r = su.check(deps)
    assert r["ok"] and r["update_available"] and r["behind"] == 1 and r["ahead"] == 0
    assert r["latest_tag"] == "v1.1" and r["commits"][0]["subject"] == "zweiter Commit"

    # geänderte verfolgte Datei blockiert, untracked nicht
    (b / "neu.txt").write_text("x")
    assert su.status(deps)["dirty"] is False
    (b / "f.txt").write_text("lokal\n")
    logs: list[str] = []
    assert su.run_apply(logs.append, deps) is False and "git stash" in logs[-1]
    _git(b, "checkout", "--", "f.txt")

    logs = []
    assert su.run_apply(logs.append, deps) is True, logs
    assert (b / "f.txt").read_text() == "2\n"
    assert (b / "neu.txt").exists()
    assert "Bitte App neu starten" in logs[-1]
    assert su.status(deps)["restart_required"] is True
    assert su.status(deps)["version"] == "v1.1"
    assert su.check(deps)["update_available"] is False
