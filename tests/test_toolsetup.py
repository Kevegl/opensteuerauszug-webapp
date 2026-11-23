"""Tests für app/toolsetup.py und die Tool-Endpunkte. Offline: Fakes für Subprozesse, Netzwerk und which."""
import json
import os
import shutil
import stat
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, runner, settings as st, toolsetup as ts
from app.jobs import JobManager

OLD_LINE = '    "ibflex @ git+https://github.com/vroonhof/ibflex.git",'
NEW_LINE = '    "ibflex2 @ git+https://github.com/vroonhof/ibflex.git",'


def pyproject(dep_line=OLD_LINE, extra=""):
    return ('[project]\nname = "opensteuerauszug"\nversion = "0.1.0"\ndependencies = [\n'
            f'{dep_line}\n    "requests>=2.33.0",\n]\n{extra}')


def wr(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def make_zip(path: Path, entries):
    """entries: Liste (name, payload). payload bytes/str oder ("symlink", ziel)."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in entries:
            if isinstance(payload, tuple):
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, payload[1])
            else:
                zf.writestr(name, payload)
    return path


# --------------------------------------------------------------------------- resolve_tool_repo

@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENSTEUER_TOOL_REPO", raising=False)
    home_tool = tmp_path / "home" / "opensteuerauszug"
    app_tool = tmp_path / "app_tool"
    monkeypatch.setattr(st, "home_tool_dir", lambda: home_tool)
    monkeypatch.setattr(st, "APP_TOOL_DIR", app_tool)
    return home_tool, app_tool


def _make_home_venv(home_tool: Path) -> None:
    py = home_tool / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")


def test_resolve_settings_wins_over_env_and_home(clean_env, monkeypatch, tmp_path):
    home_tool, _ = clean_env
    _make_home_venv(home_tool)
    monkeypatch.setenv("OPENSTEUER_TOOL_REPO", str(tmp_path / "env"))
    explicit = str(tmp_path / "explizit")
    assert st.resolve_tool_repo(f"  {explicit}  ") == (explicit, "settings")


def test_resolve_env_wins_over_home(clean_env, monkeypatch, tmp_path):
    home_tool, _ = clean_env
    _make_home_venv(home_tool)
    monkeypatch.setenv("OPENSTEUER_TOOL_REPO", str(tmp_path / "env"))
    assert st.resolve_tool_repo("") == (str(tmp_path / "env"), "env")


def test_resolve_blank_env_is_ignored(clean_env, monkeypatch):
    home_tool, _ = clean_env
    _make_home_venv(home_tool)
    monkeypatch.setenv("OPENSTEUER_TOOL_REPO", "   ")
    assert st.resolve_tool_repo(None) == (str(home_tool), "home")


def test_resolve_home_only_with_venv_python(clean_env):
    home_tool, app_tool = clean_env
    home_tool.mkdir(parents=True)  # Ordner ohne .venv\Scripts\python.exe
    assert st.resolve_tool_repo("") == (str(app_tool), "app")
    _make_home_venv(home_tool)
    assert st.resolve_tool_repo("") == (str(home_tool), "home")


def test_resolve_app_folder_fallback(clean_env):
    _, app_tool = clean_env
    assert st.resolve_tool_repo(None) == (str(app_tool), "app")


def test_auto_path_is_not_persisted(clean_env, monkeypatch, tmp_path):
    _, app_tool = clean_env
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"tool_repo": "", "flex_token": "", "flex_queries": []}), encoding="utf-8")
    loaded = st.load_settings(sf)
    assert loaded["tool_repo_source"] == "app"
    st.save_settings(loaded, sf)
    assert json.loads(sf.read_text(encoding="utf-8"))["tool_repo"] == ""


def test_auto_env_path_is_not_persisted(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSTEUER_TOOL_REPO", str(tmp_path / "env"))
    sf = tmp_path / "settings.json"
    loaded = st.load_settings(sf)  # Datei fehlt, Standardwerte
    assert loaded["tool_repo_source"] == "env"
    st.save_settings(loaded, sf)
    assert json.loads(sf.read_text(encoding="utf-8"))["tool_repo"] == ""


def test_explicit_path_is_persisted(clean_env, tmp_path):
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"tool_repo": str(tmp_path / "mein")}), encoding="utf-8")
    loaded = st.load_settings(sf)
    assert loaded["tool_repo_source"] == "settings"
    st.save_settings(loaded, sf)
    assert json.loads(sf.read_text(encoding="utf-8"))["tool_repo"] == str(tmp_path / "mein")


def test_update_settings_does_not_pin_unchanged_auto_path(clean_env, tmp_path):
    _, app_tool = clean_env
    sf = tmp_path / "settings.json"
    current = st.load_settings(sf)  # automatisch: app
    new = st.update_settings(current, {"tool_repo": str(app_tool), "flex_token": "", "flex_queries": []})
    assert new["tool_repo_source"] == "app"
    st.save_settings(new, sf)
    assert json.loads(sf.read_text(encoding="utf-8"))["tool_repo"] == ""


def test_update_settings_explicit_change_is_pinned(clean_env, tmp_path):
    sf = tmp_path / "settings.json"
    current = st.load_settings(sf)
    new = st.update_settings(current, {"tool_repo": str(tmp_path / "neu")})
    assert new["tool_repo_source"] == "settings"
    st.save_settings(new, sf)
    assert json.loads(sf.read_text(encoding="utf-8"))["tool_repo"] == str(tmp_path / "neu")


# --------------------------------------------------------------------------- ibflex-Fix

def test_ibflex_fix_replaces_line_and_is_idempotent(tmp_path):
    p = tmp_path / "pyproject.toml"
    wr(p, pyproject(OLD_LINE))
    logs = []
    assert ts.apply_ibflex_fix(p, logs.append) is True
    fixed = p.read_text(encoding="utf-8")
    assert NEW_LINE in fixed and OLD_LINE not in fixed
    # Nur die ibflex-Zeile unterscheidet sich, alle anderen Zeilen bleiben gleich.
    old_lines = pyproject(OLD_LINE).splitlines()
    new_lines = fixed.splitlines()
    assert len(old_lines) == len(new_lines)
    diff = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert diff == [old_lines.index(OLD_LINE)]
    # Zweiter Aufruf ändert nichts.
    before = p.read_bytes()
    assert ts.apply_ibflex_fix(p, logs.append) is False
    assert p.read_bytes() == before


def test_ibflex_fix_keeps_crlf_and_other_bytes(tmp_path):
    p = tmp_path / "pyproject.toml"
    original = pyproject(OLD_LINE).replace("\n", "\r\n").encode("utf-8")
    p.write_bytes(original)
    assert ts.apply_ibflex_fix(p, lambda s: None) is True
    assert p.read_bytes() == original.replace(OLD_LINE.encode(), NEW_LINE.encode())


def test_ibflex_fix_leaves_upstream_fixed_file_unchanged(tmp_path):
    p = tmp_path / "pyproject.toml"
    wr(p, pyproject(NEW_LINE))
    before = p.read_bytes()
    logs = []
    assert ts.apply_ibflex_fix(p, logs.append) is False
    assert p.read_bytes() == before
    assert any("nicht nötig" in line for line in logs)


def test_ibflex_fix_missing_line_changes_nothing(tmp_path):
    p = tmp_path / "pyproject.toml"
    wr(p, '[project]\ndependencies = ["requests"]\n')
    before = p.read_bytes()
    assert ts.apply_ibflex_fix(p, lambda s: None) is False
    assert p.read_bytes() == before


def test_ibflex_fix_text_only_touches_ibflex_line():
    text = pyproject(OLD_LINE)
    new, status = ts.apply_ibflex_fix_text(text)
    assert status == "fixed"
    assert ts.revert_ibflex_fix_text(new) == text
    assert ts.apply_ibflex_fix_text(new) == (new, "already")


# --------------------------------------------------------------------------- classify_pyproject

def test_classify_unchanged_ignores_line_endings():
    assert ts.classify_pyproject(pyproject(NEW_LINE), pyproject(NEW_LINE).replace("\n", "\r\n")) == "unchanged"


def test_classify_fix_only():
    assert ts.classify_pyproject(pyproject(NEW_LINE), pyproject(OLD_LINE)) == "fix_only"


def test_classify_other_changes():
    assert ts.classify_pyproject(pyproject(OLD_LINE, extra="# eigene Notiz\n"), pyproject(OLD_LINE)) == "other"
    assert ts.classify_pyproject(pyproject(NEW_LINE, extra="# eigene Notiz\n"), pyproject(OLD_LINE)) == "other"
    assert ts.classify_pyproject(pyproject(NEW_LINE), pyproject(NEW_LINE, extra="x = 1\n")) == "other"


# --------------------------------------------------------------------------- safe_extract_zip

def test_extract_strips_single_top_folder(tmp_path):
    z = make_zip(tmp_path / "a.zip", [("proj/pyproject.toml", "p"), ("proj/src/a.py", "a")])
    dest = tmp_path / "out"
    assert ts.safe_extract_zip(z, dest) == 2
    assert (dest / "pyproject.toml").read_text() == "p"
    assert (dest / "src" / "a.py").read_text() == "a"


def test_extract_keeps_paths_with_several_tops(tmp_path):
    z = make_zip(tmp_path / "a.zip", [("a/x", "1"), ("b/y", "2")])
    dest = tmp_path / "out"
    ts.safe_extract_zip(z, dest)
    assert (dest / "a" / "x").read_text() == "1"
    assert (dest / "b" / "y").read_text() == "2"


@pytest.mark.parametrize("entries", [
    [("proj/a", "1"), ("proj/../../evil.txt", "x")],   # ../ mitten im Pfad
    [("proj/a", "1"), ("../evil.txt", "x")],            # ../ am Anfang, mehrere Top-Ordner
    [("proj/a", "1"), ("/abs.txt", "x")],               # absoluter Pfad, mehrere Top-Ordner
    [("proj/a", "1"), ("C:/evil.txt", "x")],            # Laufwerksbuchstabe, mehrere Top-Ordner
    [("proj/a", "1"), ("C:\\evil.txt", "x")],           # Laufwerk mit Backslash
])
def test_extract_rejects_unsafe_paths(tmp_path, entries):
    z = make_zip(tmp_path / "a.zip", entries)
    dest = tmp_path / "out"
    with pytest.raises(ts.SetupError):
        ts.safe_extract_zip(z, dest)
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("entries", [
    [("proj/link", ("symlink", "/etc/passwd"))],
    [("proj/a", "1"), ("proj/link", ("symlink", "../../x"))],
])
def test_extract_rejects_symlinks(tmp_path, entries):
    z = make_zip(tmp_path / "a.zip", entries)
    with pytest.raises(ts.SetupError):
        ts.safe_extract_zip(z, tmp_path / "out")


def test_extract_rejects_empty_archive(tmp_path):
    z = tmp_path / "leer.zip"
    with zipfile.ZipFile(z, "w"):
        pass
    with pytest.raises(ts.SetupError):
        ts.safe_extract_zip(z, tmp_path / "out")


def test_single_top_dotdot_is_rejected(tmp_path):
    z = make_zip(tmp_path / "a.zip", [("../evil.txt", "x")])
    with pytest.raises(ts.SetupError):
        ts.safe_extract_zip(z, tmp_path / "out")


def test_single_top_absolute_is_rejected(tmp_path):
    z = make_zip(tmp_path / "a.zip", [("/abs.txt", "x")])
    with pytest.raises(ts.SetupError):
        ts.safe_extract_zip(z, tmp_path / "out")


def test_single_top_drive_letter_is_rejected(tmp_path):
    z = make_zip(tmp_path / "a.zip", [("C:/evil.txt", "x")])
    with pytest.raises(ts.SetupError):
        ts.safe_extract_zip(z, tmp_path / "out")


# --------------------------------------------------------------------------- replace_zip_source

def test_replace_keeps_config_identifiers_venv_and_foreign_files(tmp_path):
    repo = tmp_path / "repo"
    wr(repo / "config.toml", "USER-CONFIG")
    wr(repo / "data" / "security_identifiers.csv", "USER-IDS")
    wr(repo / "data" / "eigene.csv", "alt")
    wr(repo / "pyproject.toml", "alt")
    wr(repo / "notes.txt", "meine Notizen")
    wr(repo / "output" / "2025" / "x.pdf", "pdf")
    wr(repo / "src" / "opensteuerauszug" / "alt.py", "alt")
    ts.write_marker(repo, files=["src/opensteuerauszug/alt.py"])
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts" / "python.exe").write_bytes(b"venv")

    src = tmp_path / "neu"
    wr(src / "pyproject.toml", "neu")
    wr(src / "config.toml", "DEFAULT")
    wr(src / "data" / "security_identifiers.csv", "NEW-IDS")
    wr(src / "data" / "neu.csv", "neu")
    wr(src / "src" / "opensteuerauszug" / "__init__.py", "neu")
    (src / ".venv" / "Scripts").mkdir(parents=True)
    (src / ".venv" / "Scripts" / "python.exe").write_bytes(b"NEWVENV")

    logs = []
    ts.replace_zip_source(src, repo, logs.append)

    assert (repo / "config.toml").read_text() == "USER-CONFIG"
    assert (repo / "data" / "security_identifiers.csv").read_text() == "USER-IDS"
    assert (repo / "data" / "neu.csv").read_text() == "neu"
    assert (repo / "data" / "eigene.csv").read_text() == "alt"  # fremde Datei im data-Ordner bleibt
    assert (repo / ".venv" / "Scripts" / "python.exe").read_bytes() == b"venv"
    assert (repo / "notes.txt").read_text() == "meine Notizen"
    assert (repo / "output" / "2025" / "x.pdf").read_text() == "pdf"
    assert (repo / "pyproject.toml").read_text() == "neu"
    assert (repo / "src" / "opensteuerauszug" / "__init__.py").read_text() == "neu"
    assert not (repo / "src" / "opensteuerauszug" / "alt.py").exists()  # entferntes Upstream-Modul
    assert any("config.toml" in line for line in logs)


def _zip_repo(tmp_path):
    repo, src = tmp_path / "repo", tmp_path / "neu"
    wr(repo / "pyproject.toml", "alt")
    wr(repo / "src" / "opensteuerauszug" / "a.py", "alt-a")
    wr(repo / "src" / "opensteuerauszug" / "gone.py", "alt-gone")
    wr(src / "pyproject.toml", "neu")
    wr(src / "src" / "opensteuerauszug" / "a.py", "neu-a")
    wr(src / "src" / "opensteuerauszug" / "b.py", "neu-b")
    wr(src / "src" / "opensteuerauszug" / "sub" / "c.py", "neu-c")
    return repo, src


def test_replace_keeps_user_files_in_src_and_removes_upstream_removed(tmp_path):
    repo, src = _zip_repo(tmp_path)
    wr(repo / "src" / "opensteuerauszug" / "config" / "config.toml", "USER")
    wr(src / "src" / "opensteuerauszug" / "config" / "config.toml", "DEFAULT")
    wr(repo / "src" / "opensteuerauszug" / "eigen.py", "meins")  # nicht im Manifest
    ts.write_marker(repo, files=["src/opensteuerauszug/a.py", "src/opensteuerauszug/gone.py"])
    files = ts.replace_zip_source(src, repo, lambda s: None)
    base = repo / "src" / "opensteuerauszug"
    assert (base / "config" / "config.toml").read_text() == "USER"
    assert (base / "eigen.py").read_text() == "meins"
    assert not (base / "gone.py").exists()
    assert (base / "a.py").read_text() == "neu-a" and (base / "sub" / "c.py").read_text() == "neu-c"
    assert "src/opensteuerauszug/b.py" in files and "src/opensteuerauszug/gone.py" not in files


def test_replace_without_manifest_deletes_nothing(tmp_path):
    repo, src = _zip_repo(tmp_path)
    ts.replace_zip_source(src, repo, lambda s: None)
    assert (repo / "src" / "opensteuerauszug" / "gone.py").read_text() == "alt-gone"


def test_replace_rolls_back_on_error(tmp_path, monkeypatch):
    repo, src = _zip_repo(tmp_path)
    ts.write_marker(repo, files=["src/opensteuerauszug/gone.py"])
    real = shutil.copy2

    def flaky(s, d, *a, **k):
        if Path(d).name == "c.py":
            raise OSError("Platte voll")
        return real(s, d, *a, **k)

    monkeypatch.setattr(ts.shutil, "copy2", flaky)
    with pytest.raises(ts.SetupError, match="wiederhergestellt"):
        ts.replace_zip_source(src, repo, lambda s: None)
    base = repo / "src" / "opensteuerauszug"
    assert (repo / "pyproject.toml").read_text() == "alt"
    assert (base / "a.py").read_text() == "alt-a"
    assert (base / "gone.py").read_text() == "alt-gone"
    assert not (base / "b.py").exists() and not (base / "sub").exists()


# --------------------------------------------------------------------------- installed_version

def test_installed_version_git_uses_commit(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    wr(repo / "pyproject.toml", pyproject(NEW_LINE))

    deps = ts.Deps(which=lambda n: "git.exe", run_capture=lambda cmd, cwd, timeout=60: (0, "4633aab\n"))
    assert ts.installed_version(repo, deps) == "4633aab (Version 0.1.0)"


# --------------------------------------------------------------------------- run_install / run_update

def fake_streaming(calls, fail_substr=None, clone_pyproject=None, head_text=""):
    def run(cmd, cwd, log=lambda s: None, redact=()):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if fail_substr and fail_substr in " ".join(cmd):
            return 1
        if cmd[:3] == ["git", "checkout", "--"]:
            # Fake-Checkout setzt pyproject.toml auf den HEAD-Stand zurück.
            wr(Path(cwd) / "pyproject.toml", head_text)
        if cmd[:2] == ["git", "clone"]:
            wr(Path(cmd[-1]) / ".git" / "HEAD", "x")
        if cmd[:2] == ["git", "init"]:
            wr(Path(cwd) / ".git" / "HEAD", "x")
        if cmd[:3] == ["git", "checkout", "--detach"] and not (Path(cwd) / "pyproject.toml").exists():
            # Fake-Checkout legt bei einer Neuinstallation pyproject.toml an.
            wr(Path(cwd) / "pyproject.toml", clone_pyproject if clone_pyproject is not None else pyproject(OLD_LINE))
        if cmd[1:3] == ["-m", "venv"]:
            py = Path(cmd[-1]) / "Scripts" / "python.exe"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_bytes(b"")
        return 0
    return run


def fake_zip_download(entries):
    def download(url, dest, log, max_bytes=ts.MAX_ZIP_BYTES):
        make_zip(Path(dest), entries)
        log("Heruntergeladen: 0.0 MB")
    return download


BASE_PY = r"C:\Python\python.exe"


PIN_SHA = "4633aab493e90a0f13e6701e8aa2d85cf77e4bab"
OLD_SHA = "d3d3af7" + "0" * 33
NEW_SHA = "ffffff1" + "0" * 33
PIN = {"repo": "https://example.invalid/repo.git", "commit": PIN_SHA, "tested_on": "2026-11-23", "note": ""}


def make_deps(tmp_path, calls, capture_calls, *, git=True, streaming_fail=None, head=None,
              clone_pyproject=None, download=None, head_sha=OLD_SHA, has_pin=True, ancestor=None,
              shallow=False, branch="main", pin=None, zip_url="https://example.invalid/main.zip"):
    """ancestor: None (keine Beziehung), "pin_in_head" (HEAD neuer) oder "head_in_pin" (HEAD älter)."""
    head_text = head if head is not None else pyproject(NEW_LINE)

    def capture(cmd, cwd, timeout=60):
        cmd = [str(c) for c in cmd]
        capture_calls.append(cmd)
        if cmd[:2] == ["git", "show"]:
            return 0, head_text
        if cmd == ["git", "rev-parse", "HEAD"]:
            return 0, head_sha + "\n"
        if cmd[:3] == ["git", "cat-file", "-e"]:
            return (0 if has_pin else 128), ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            first = cmd[3]
            ok = (ancestor == "pin_in_head" and first == PIN_SHA) or (ancestor == "head_in_pin" and first != PIN_SHA)
            return (0 if ok else 1), ""
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return 0, "true\n" if shallow else "false\n"
        if cmd == ["git", "branch", "--show-current"]:
            return 0, branch + "\n"
        return 0, "4633aab\n"

    return ts.Deps(
        run_streaming=fake_streaming(calls, streaming_fail, clone_pyproject, head_text),
        run_capture=capture,
        which=lambda name: r"C:\git.exe" if (git and name == "git") else None,
        download=download or fake_zip_download([]),
        base_python=lambda: BASE_PY,
        check_tool=lambda repo: {"ok": True, "message": "Bereit."},
        tmp_root=tmp_path / "tmproot",
        zip_url=zip_url,
        repo_url="https://example.invalid/repo.git",
        pin=pin or dict(PIN),
    )


def _no_destructive(cmds):
    for cmd in cmds:
        joined = " ".join(cmd)
        assert "clean" not in joined, joined
        assert "reset" not in joined, joined
        assert "--hard" not in joined, joined


def test_install_git_command_order(tmp_path):
    calls, cap = [], []
    repo = tmp_path / "ost"
    logs = []
    deps = make_deps(tmp_path, calls, cap, clone_pyproject=pyproject(OLD_LINE))
    assert ts.run_install({"tool_repo": str(repo)}, logs.append, deps) is True
    py = str(repo / ".venv" / "Scripts" / "python.exe")
    assert calls == [
        ["git", "init"],
        ["git", "remote", "add", "origin", "https://example.invalid/repo.git"],
        ["git", "fetch", "--depth", "1", "origin", PIN_SHA],
        ["git", "checkout", "--detach", "FETCH_HEAD"],
        [BASE_PY, "-m", "venv", str(repo / ".venv")],
        [py, "-m", "pip", "install", "--upgrade", "pip"],
        [py, "-m", "pip", "install", "."],
    ]
    assert cap == []
    assert NEW_LINE in (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert any("ibflex-Fix angewendet" in line for line in logs)
    _no_destructive(calls)


def test_install_git_upstream_already_fixed(tmp_path):
    calls, cap = [], []
    repo = tmp_path / "ost"
    logs = []
    deps = make_deps(tmp_path, calls, cap, clone_pyproject=pyproject(NEW_LINE))
    assert ts.run_install({"tool_repo": str(repo)}, logs.append, deps) is True
    assert any("nicht nötig" in line for line in logs)
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == pyproject(NEW_LINE)


def test_install_zip_path_without_git(tmp_path):
    calls, cap = [], []
    repo = tmp_path / "ost"
    entries = [("opensteuerauszug-main/pyproject.toml", pyproject(OLD_LINE)),
               ("opensteuerauszug-main/src/opensteuerauszug/__init__.py", "")]
    deps = make_deps(tmp_path, calls, cap, git=False, download=fake_zip_download(entries))
    assert ts.run_install({"tool_repo": str(repo)}, lambda s: None, deps) is True
    assert not any(c[0] == "git" for c in calls)
    assert (repo / "src" / "opensteuerauszug" / "__init__.py").exists()
    assert NEW_LINE in (repo / "pyproject.toml").read_text(encoding="utf-8")
    marker = ts.read_marker(repo)
    assert marker["kind"] == "zip" and marker["source"] == "https://example.invalid/main.zip"
    py = str(repo / ".venv" / "Scripts" / "python.exe")
    assert calls[-2:] == [[py, "-m", "pip", "install", "--upgrade", "pip"], [py, "-m", "pip", "install", "."]]
    _no_destructive(calls)


def test_install_zip_into_empty_existing_folder(tmp_path):
    calls, cap = [], []
    repo = tmp_path / "ost"
    repo.mkdir()
    entries = [("opensteuerauszug-main/pyproject.toml", pyproject(NEW_LINE))]
    deps = make_deps(tmp_path, calls, cap, git=False, download=fake_zip_download(entries))
    assert ts.run_install({"tool_repo": str(repo)}, lambda s: None, deps) is True
    assert (repo / "pyproject.toml").exists()


def test_install_refuses_non_empty_folder_without_pyproject(tmp_path):
    calls, cap = [], []
    repo = tmp_path / "ost"
    wr(repo / "fremd.txt", "x")
    logs = []
    deps = make_deps(tmp_path, calls, cap)
    assert ts.run_install({"tool_repo": str(repo)}, logs.append, deps) is False
    assert calls == []
    assert any("nicht leer" in line for line in logs)
    assert (repo / "fremd.txt").exists()


def test_install_stops_on_pip_failure(tmp_path):
    calls, cap = [], []
    repo = tmp_path / "ost"
    logs = []
    deps = make_deps(tmp_path, calls, cap, streaming_fail="pip install .")
    assert ts.run_install({"tool_repo": str(repo)}, logs.append, deps) is False
    assert any(line.startswith("FEHLER") and "Tool installieren" in line for line in logs)


def _make_installed(repo: Path, working: str, git: bool = True):
    repo.mkdir(parents=True, exist_ok=True)
    if git:
        (repo / ".git").mkdir(parents=True)
    else:
        ts.write_marker(repo, source="https://example.invalid/main.zip")
    wr(repo / "pyproject.toml", working)
    py = repo / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")


def test_update_git_fix_only_fetches_checks_out_and_refixes(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))  # Arbeitskopie nur mit Fix
    calls, cap = [], []
    logs = []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(OLD_LINE), has_pin=False)
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is True
    py = str(repo / ".venv" / "Scripts" / "python.exe")
    assert calls == [
        ["git", "fetch", "origin", PIN_SHA],
        ["git", "checkout", "--", "pyproject.toml"],
        ["git", "checkout", "--detach", PIN_SHA],
        [py, "-m", "pip", "install", "."],
    ]
    assert ["git", "show", "HEAD:pyproject.toml"] in cap
    _no_destructive(calls + cap)


def test_update_git_unchanged_fetches_without_pyproject_reset(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap = [], []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(NEW_LINE), has_pin=False)
    assert ts.run_update({"tool_repo": str(repo)}, lambda s: None, deps) is True
    assert ["git", "checkout", "--", "pyproject.toml"] not in calls
    assert calls[0] == ["git", "fetch", "origin", PIN_SHA]
    assert calls[1] == ["git", "checkout", "--detach", PIN_SHA]
    _no_destructive(calls)


def test_update_git_shallow_repo_fetches_depth_one(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap = [], []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(NEW_LINE), has_pin=False, shallow=True)
    assert ts.run_update({"tool_repo": str(repo)}, lambda s: None, deps) is True
    assert calls[0] == ["git", "fetch", "--depth", "1", "origin", PIN_SHA]


def test_update_git_logs_detached_head_and_way_back(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap, logs = [], [], []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(NEW_LINE), has_pin=False, branch="main")
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is True
    text = "\n".join(logs)
    assert "detached HEAD" in text and "checkout main" in text


def test_update_git_already_on_pin_only_checks_tool(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap, logs = [], [], []
    deps = make_deps(tmp_path, calls, cap, head_sha=PIN_SHA)
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is True
    assert calls == []  # kein fetch, checkout oder pip
    assert any("bereits auf der getesteten Version" in line for line in logs)


def test_update_git_newer_than_pin_is_not_downgraded(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap, logs = [], [], []
    deps = make_deps(tmp_path, calls, cap, head_sha=NEW_SHA, ancestor="pin_in_head")
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is True
    assert calls == []
    assert any("neuer als die getestete Version" in line for line in logs)


def test_job_update_newer_without_force_reports_warning_result(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    deps = make_deps(tmp_path, [], [], head_sha=NEW_SHA, ancestor="pin_in_head")
    job = type("J", (), {"log": staticmethod(lambda s: None), "results": []})()
    assert ts.job_update({"tool_repo": str(repo)}, deps)(job) is True
    assert len(job.results) == 1 and job.results[0]["kind"] == "warning"
    assert "Nicht zurückgestuft" in job.results[0]["message"]


def test_job_update_newer_with_force_has_no_warning(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    deps = make_deps(tmp_path, [], [], head=pyproject(NEW_LINE), head_sha=NEW_SHA, ancestor="pin_in_head")
    job = type("J", (), {"log": staticmethod(lambda s: None), "results": []})()
    assert ts.job_update({"tool_repo": str(repo)}, deps, force=True)(job) is True
    assert job.results == []


def test_update_git_newer_than_pin_force_downgrades(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap = [], []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(NEW_LINE), head_sha=NEW_SHA, ancestor="pin_in_head")
    assert ts.run_update({"tool_repo": str(repo)}, lambda s: None, deps, force=True) is True
    assert ["git", "checkout", "--detach", PIN_SHA] in calls


def test_update_git_sha_fetch_failure_falls_back_to_fetch_origin(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap = [], []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(NEW_LINE), has_pin=False)
    inner = deps.run_streaming
    deps.run_streaming = lambda cmd, cwd, log=lambda s: None, redact=(): (
        1 if list(cmd) == ["git", "fetch", "origin", PIN_SHA] else inner(cmd, cwd, log))
    assert ts.run_update({"tool_repo": str(repo)}, lambda s: None, deps) is True
    assert ["git", "fetch", "origin"] in calls
    assert ["git", "checkout", "--detach", PIN_SHA] in calls


def test_update_git_aborts_on_foreign_pyproject_changes(tmp_path):
    repo = tmp_path / "ost"
    working = pyproject(NEW_LINE, extra="# eigene Änderung\n")
    _make_installed(repo, working)
    calls, cap = [], []
    logs = []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(OLD_LINE))
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is False
    assert calls == []  # weder fetch noch checkout
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == working
    assert any("eigene Änderungen" in line for line in logs)


def test_update_git_fetch_failure_leaves_everything_untouched(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap, logs = [], [], []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(OLD_LINE), streaming_fail="fetch", has_pin=False)
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is False
    assert ["git", "checkout", "--", "pyproject.toml"] not in calls
    assert NEW_LINE in (repo / "pyproject.toml").read_text(encoding="utf-8")


def test_update_git_checkout_failure_restores_fix(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap = [], []
    logs = []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(OLD_LINE), streaming_fail="--detach", has_pin=False)
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is False
    assert calls[:3] == [["git", "fetch", "origin", PIN_SHA], ["git", "checkout", "--", "pyproject.toml"],
                         ["git", "checkout", "--detach", PIN_SHA]]
    assert NEW_LINE in (repo / "pyproject.toml").read_text(encoding="utf-8")
    _no_destructive(calls)


def test_update_git_latest_fetches_main_and_detaches(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(NEW_LINE))
    calls, cap = [], []
    deps = make_deps(tmp_path, calls, cap, head=pyproject(NEW_LINE), head_sha=PIN_SHA)
    assert ts.run_update({"tool_repo": str(repo)}, lambda s: None, deps, target="latest") is True
    assert calls[:2] == [["git", "fetch", "origin", "main"], ["git", "checkout", "--detach", "FETCH_HEAD"]]
    _no_destructive(calls)


def test_update_zip_keeps_user_files_and_updates_source(tmp_path):
    repo = tmp_path / "ost"
    _make_installed(repo, pyproject(OLD_LINE), git=False)
    wr(repo / "config.toml", "USER-CONFIG")
    wr(repo / "data" / "security_identifiers.csv", "USER-IDS")
    wr(repo / "notes.txt", "meine Notizen")
    wr(repo / "src" / "opensteuerauszug" / "alt.py", "alt")
    entries = [
        ("opensteuerauszug-main/pyproject.toml", pyproject(NEW_LINE)),
        ("opensteuerauszug-main/config.toml", "DEFAULT"),
        ("opensteuerauszug-main/data/security_identifiers.csv", "NEW-IDS"),
        ("opensteuerauszug-main/src/opensteuerauszug/__init__.py", "neu"),
    ]
    calls, cap = [], []
    deps = make_deps(tmp_path, calls, cap, git=False, download=fake_zip_download(entries))
    assert ts.run_update({"tool_repo": str(repo)}, lambda s: None, deps) is True
    assert (repo / "config.toml").read_text() == "USER-CONFIG"
    assert (repo / "data" / "security_identifiers.csv").read_text() == "USER-IDS"
    assert (repo / "notes.txt").read_text() == "meine Notizen"
    assert (repo / "src" / "opensteuerauszug" / "__init__.py").read_text() == "neu"
    marker = ts.read_marker(repo)
    assert marker["kind"] == "zip" and marker["commit"] == PIN_SHA
    py = str(repo / ".venv" / "Scripts" / "python.exe")
    assert calls == [[py, "-m", "pip", "install", "."]]
    assert cap == []
    _no_destructive(calls)


def test_update_refuses_unknown_install_kind(tmp_path):
    repo = tmp_path / "ost"
    wr(repo / "pyproject.toml", pyproject(NEW_LINE))  # weder .git noch Marker
    calls, cap = [], []
    logs = []
    deps = make_deps(tmp_path, calls, cap)
    assert ts.run_update({"tool_repo": str(repo)}, logs.append, deps) is False
    assert calls == [] and cap == []
    assert any("Installationsart unbekannt" in line for line in logs)


def test_update_refuses_missing_tool(tmp_path):
    calls, cap = [], []
    deps = make_deps(tmp_path, calls, cap)
    assert ts.run_update({"tool_repo": str(tmp_path / "gibt-es-nicht")}, lambda s: None, deps) is False
    assert calls == [] and cap == []


def test_job_wrappers_run_with_injected_deps(tmp_path):
    calls, cap = [], []
    repo = tmp_path / "ost"
    deps = make_deps(tmp_path, calls, cap, clone_pyproject=pyproject(NEW_LINE))

    class Job:
        def __init__(self):
            self.lines = []

        def log(self, line):
            self.lines.append(line)

    job = Job()
    assert ts.job_install({"tool_repo": str(repo)}, deps)(job) is True
    assert job.lines


# --------------------------------------------------------------------------- API

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSTEUER_TOOL_REPO", raising=False)
    repo = tmp_path / "tool"
    repo.mkdir()
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"tool_repo": str(repo), "flex_token": "", "flex_queries": []}), encoding="utf-8")
    monkeypatch.setattr(st, "SETTINGS_FILE", sf)
    monkeypatch.setattr(main, "jobs", JobManager())
    return TestClient(main.app), repo


def _ok_tool(monkeypatch, ok: bool):
    monkeypatch.setattr(runner, "check_tool",
                        lambda repo: {"ok": ok, "message": "Bereit." if ok else "nicht da", "repo": str(repo)})


def test_install_409_when_tool_works(api, monkeypatch):
    client, _ = api
    _ok_tool(monkeypatch, True)
    r = client.post("/api/tool/install", json={})
    assert r.status_code == 409


def test_install_starts_when_tool_not_working(api, monkeypatch):
    client, _ = api
    _ok_tool(monkeypatch, False)
    monkeypatch.setattr(ts, "job_install", lambda s, deps=None, **kw: (lambda job: True))
    r = client.post("/api/tool/install", json={})
    assert r.status_code == 200 and "job_id" in r.json()


def test_install_force_starts_even_when_tool_works(api, monkeypatch):
    client, _ = api
    _ok_tool(monkeypatch, True)
    monkeypatch.setattr(ts, "job_install", lambda s, deps=None, **kw: (lambda job: True))
    r = client.post("/api/tool/install", json={"force": True})
    assert r.status_code == 200


def test_update_starts_job(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(ts, "job_update", lambda s, deps=None, **kw: (lambda job: True))
    r = client.post("/api/tool/update")
    assert r.status_code == 200 and "job_id" in r.json()


def test_update_409_while_other_job_runs(api, monkeypatch):
    client, _ = api
    release = threading.Event()
    main.jobs.start("kursliste", "blockiert", lambda job: release.wait(5) and True)
    monkeypatch.setattr(ts, "job_update", lambda s, deps=None, **kw: (lambda job: True))
    try:
        r = client.post("/api/tool/update")
        assert r.status_code == 409
    finally:
        release.set()


def test_status_has_new_fields(api, monkeypatch):
    client, repo = api
    _ok_tool(monkeypatch, False)
    ts.write_marker(repo, source="https://example.invalid/main.zip")
    wr(repo / "pyproject.toml", pyproject(NEW_LINE))
    body = client.get("/api/status").json()
    assert body["tool_repo"] == str(repo)
    assert body["tool_source"] == "settings"
    assert body["install_kind"] == "zip"
    assert body["version"].startswith("Version 0.1.0")
    assert body["ok"] is False
    assert body["running_job"] is None
    assert "default_year" in body


def test_update_settings_empty_tool_repo_resets_to_auto(clean_env, tmp_path):
    _, app_tool = clean_env
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"tool_repo": str(tmp_path / "mein")}), encoding="utf-8")
    current = st.load_settings(sf)
    new = st.update_settings(current, {"tool_repo": "  "})
    assert (new["tool_repo"], new["tool_repo_source"]) == (str(app_tool), "app")
    st.save_settings(new, sf)
    assert json.loads(sf.read_text(encoding="utf-8"))["tool_repo"] == ""
    # Ohne tool_repo im Patch bleibt ein gesetzter Pfad unverändert.
    assert st.update_settings(current, {})["tool_repo"] == str(tmp_path / "mein")


# --------------------------------------------------------------------------- version_status (echte Git-Repos)

GIT_ENV = {"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}


def _git(cwd, *args):
    cmd = ["git", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def _three_commit_repo(path: Path) -> list[str]:
    """Echtes Repo mit 3 Commits auf main. Rückgabe: [c1, c2, c3] als volle SHAs."""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    shas = []
    for i in range(1, 4):
        (path / "f.txt").write_text(f"stand {i}\n", encoding="utf-8")
        _git(path, "add", "f.txt")
        _git(path, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(path, "rev-parse", "HEAD"))
    return shas


def _status_against(repo: Path, pin_sha: str) -> dict:
    pin = {"repo": "https://example.invalid/repo.git", "commit": pin_sha, "tested_on": "2026-11-23", "note": ""}
    return ts.version_status(repo, ts.Deps(pin=pin))  # which/run_capture bleiben echt


def test_version_status_git_tested(tmp_path):
    c = _three_commit_repo(tmp_path / "repo")
    info = _status_against(tmp_path / "repo", c[2])
    assert info["version_state"] == "tested"
    assert info["version_state_text"] == ts.STATE_TEXT["tested"]
    assert info["installed_commit_full"] == c[2] and info["installed_commit"] == c[2][:7]
    assert info["tested_commit_full"] == c[2] and info["tested_commit"] == c[2][:7]


def test_version_status_git_older(tmp_path):
    repo = tmp_path / "repo"
    c = _three_commit_repo(repo)
    _git(repo, "checkout", "-q", "--detach", c[0])
    info = _status_against(repo, c[2])
    assert info["version_state"] == "older"
    assert info["installed_commit_full"] == c[0]


def test_version_status_git_newer(tmp_path):
    repo = tmp_path / "repo"
    c = _three_commit_repo(repo)
    info = _status_against(repo, c[0])  # HEAD = c3, Pin = c1
    assert info["version_state"] == "newer"
    assert info["version_state_text"] == ts.STATE_TEXT["newer"]


def test_version_status_git_unknown_on_diverged_branch(tmp_path):
    repo = tmp_path / "repo"
    c = _three_commit_repo(repo)
    _git(repo, "checkout", "-q", "-b", "side", c[0])
    (repo / "f.txt").write_text("abweichend\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "abweichend")
    info = _status_against(repo, c[2])  # Pin liegt lokal, aber weder Vorfahr noch Nachfahr
    assert info["version_state"] == "unknown"


def test_version_status_shallow_clone(tmp_path):
    c = _three_commit_repo(tmp_path / "src")
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", (tmp_path / "src").as_uri(), str(shallow))
    assert _status_against(shallow, c[2])["version_state"] == "tested"
    # Pin-Commit fehlt im flachen Repo: Historie ist abgeschnitten, also "unknown", nicht "older".
    assert _status_against(shallow, c[0])["version_state"] == "unknown"


def test_version_status_zip_marker(tmp_path):
    c = _three_commit_repo(tmp_path / "hist")
    zdir = tmp_path / "zip"
    zdir.mkdir()
    (zdir / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
    ts.write_marker(zdir, commit=c[2], ref="pinned")
    info = _status_against(zdir, c[2])
    assert info["version_state"] == "tested"
    assert info["installed_commit"] == c[2][:7]
    ts.write_marker(zdir, commit=c[0], ref="pinned")  # früherer Pin (Pins werden nur angehoben)
    info = _status_against(zdir, c[2])
    assert info["version_state"] == "older"
    assert info["version_state_text"] == "ältere getestete Version – Aktualisieren empfohlen"
    ts.write_marker(zdir, commit=c[0])  # ohne ref: unknown
    assert _status_against(zdir, c[2])["version_state"] == "unknown"
    ts.write_marker(zdir, commit=None, ref="pinned")  # ohne Commit: unknown
    assert _status_against(zdir, c[2])["version_state"] == "unknown"
    ts.write_marker(zdir, commit=None, ref="latest")  # main-ZIP
    assert _status_against(zdir, c[2])["version_state"] == "newer"


def test_version_status_unknown_install_kind(tmp_path):
    c = _three_commit_repo(tmp_path / "hist")
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "pyproject.toml").write_text("x", encoding="utf-8")
    info = _status_against(bare, c[2])
    assert info["version_state"] == "unknown" and info["installed_commit"] is None


def test_version_status_full_repo_missing_pin_on_diverged_branch(tmp_path):
    # Dokumentiertes Verhalten: offline nicht von "alt" unterscheidbar, daher "older" mit eigenem Text.
    repo = tmp_path / "repo"
    c = _three_commit_repo(repo)
    _git(repo, "checkout", "-q", "-b", "side", c[0])
    (repo / "f.txt").write_text("abweichend\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "abweichend")
    missing = "ab" * 20
    info = _status_against(repo, missing)
    assert info["version_state"] == "older"
    assert info["version_state_text"] == "älter als getestet oder abweichend – Aktualisieren empfohlen"


# --------------------------------------------------------------------------- API: update und status

def _capture_job_update(monkeypatch):
    seen = {}

    def fake(s, deps=None, **kw):
        seen.update(kw)
        return lambda job: True
    monkeypatch.setattr(ts, "job_update", fake)
    return seen


def test_update_without_body_targets_tested(api, monkeypatch):
    client, _ = api
    seen = _capture_job_update(monkeypatch)
    r = client.post("/api/tool/update")
    assert r.status_code == 200 and "job_id" in r.json()
    assert seen.get("target", "pinned") != "latest"
    assert seen.get("force", False) is False


def test_update_explicit_tested_and_force_passed(api, monkeypatch):
    client, _ = api
    seen = _capture_job_update(monkeypatch)
    r = client.post("/api/tool/update", json={"target": "tested", "force": True})
    assert r.status_code == 200
    assert seen.get("target", "pinned") != "latest" and seen["force"] is True


def test_update_latest_is_allowed(api, monkeypatch):
    client, _ = api
    seen = _capture_job_update(monkeypatch)
    r = client.post("/api/tool/update", json={"target": "latest"})
    assert r.status_code == 200 and "job_id" in r.json()
    assert seen["target"] == "latest"


@pytest.mark.parametrize("body", [{"target": "main"}, {"target": "pinned"}, {"target": 1}])
def test_update_invalid_target_is_400(api, monkeypatch, body):
    client, _ = api
    seen = _capture_job_update(monkeypatch)
    r = client.post("/api/tool/update", json=body)
    assert r.status_code == 400
    assert seen == {} and main.jobs.running() is None


def test_status_has_version_fields_from_pin(api, monkeypatch, tmp_path):
    client, repo = api
    _ok_tool(monkeypatch, False)
    pinfile = tmp_path / "tool_version.json"
    pinfile.write_text(json.dumps({"repo": "https://example.invalid/repo.git", "commit": PIN_SHA,
                                   "tested_on": "2026-11-23"}), encoding="utf-8")
    monkeypatch.setattr(ts, "PIN_FILE", pinfile)
    ts.write_marker(repo, commit=PIN_SHA, ref="pinned")
    wr(repo / "pyproject.toml", pyproject(NEW_LINE))
    body = client.get("/api/status").json()
    for key in ("installed_commit", "tested_commit", "version_state", "version_state_text"):
        assert key in body
    assert body["installed_commit"] == PIN_SHA[:7] and body["tested_commit"] == PIN_SHA[:7]
    assert body["version_state"] == "tested"
    assert body["version_state_text"] == ts.STATE_TEXT["tested"]
