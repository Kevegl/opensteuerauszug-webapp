from pathlib import Path

from app import platform_utils as pu, runner, settings as st


def test_venv_python_windows(monkeypatch):
    monkeypatch.setattr(pu, "is_windows", lambda: True)
    assert pu.venv_python(Path("x") / ".venv") == Path("x") / ".venv" / "Scripts" / "python.exe"
    assert runner.tool_python("repo") == Path("repo") / ".venv" / "Scripts" / "python.exe"
    assert "Scripts" in pu.venv_python_hint()


def test_venv_python_posix(monkeypatch):
    monkeypatch.setattr(pu, "is_windows", lambda: False)
    assert pu.venv_python(Path("x") / ".venv") == Path("x") / ".venv" / "bin" / "python"
    assert runner.tool_python("repo") == Path("repo") / ".venv" / "bin" / "python"
    assert pu.venv_python_hint() == ".venv/bin/python"


def test_home_detection_uses_platform_python(monkeypatch, tmp_path):
    home = tmp_path / "opensteuerauszug"
    monkeypatch.setattr(st, "home_tool_dir", lambda: home)
    monkeypatch.setattr(st, "APP_TOOL_DIR", tmp_path / "app")
    monkeypatch.delenv("OPENSTEUER_TOOL_REPO", raising=False)
    for os_name, rel in (("nt", "Scripts/python.exe"), ("posix", "bin/python")):
        monkeypatch.setattr(pu, "is_windows", lambda: os_name == "nt")
        py = home / ".venv" / rel
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"")
        assert st.resolve_tool_repo("") == (str(home), "home")
        py.unlink()
        assert st.resolve_tool_repo("") == (str(tmp_path / "app"), "app")


def test_git_usable_macos_stub():
    calls = []

    def run(cmd):
        calls.append(cmd)
        return 1

    assert pu.git_usable(lambda n: "/usr/bin/git", run, "darwin") is False
    assert calls == [["xcode-select", "-p"]]
    assert pu.git_usable(lambda n: "/usr/bin/git", lambda c: 0, "darwin") is True
    # Homebrew-git ist nie der Stub, xcode-select wird nicht gefragt
    assert pu.git_usable(lambda n: "/opt/homebrew/bin/git", run, "darwin") is True
    assert len(calls) == 1
    assert pu.git_usable(lambda n: "/usr/bin/git", run, "linux") is True
    assert pu.git_usable(lambda n: None, run, "darwin") is False


def test_toolsetup_has_git_uses_platform():
    from app import toolsetup as ts
    deps = ts.Deps(which=lambda n: "/usr/bin/git", run_capture=lambda cmd, cwd, timeout=60: (1, ""), platform="darwin")
    assert ts.has_git(deps) is False
    deps = ts.Deps(which=lambda n: "/usr/bin/git", run_capture=lambda cmd, cwd, timeout=60: (0, "/Library/Developer"), platform="darwin")
    assert ts.has_git(deps) is True
