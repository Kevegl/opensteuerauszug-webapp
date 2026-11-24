"""Bundled-Modus: Befehlsbau, --run-tool-Allowlist, Tool-Endpunkte, open-folder, quit, Programm-Update-Karte."""
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, paths, program_update, runner, settings as st, toolrun, toolsetup
from app.jobs import JobManager

REPO = Path("repo")
LAUNCHER = Path(__file__).resolve().parent.parent / "app" / "launcher.py"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "jobs", JobManager())
    return TestClient(main.app)


@pytest.fixture
def bundled(monkeypatch):
    monkeypatch.setenv("STEUERAUSZUG_BUNDLED_TOOL", "1")


# ------------------------------------------------------------------ Befehlsbau

def test_venv_mode_by_default():
    assert runner.bundled_mode() is False
    cmd = runner.build_kursliste_download_cmd(REPO, 2025)
    assert cmd[:3] == [str(runner.tool_python(REPO)), "-m", "opensteuerauszug.kursliste"]
    assert runner.build_kursliste_dir_cmd(REPO)[1] == "-c"


def test_frozen_prefix_is_program_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert runner.bundled_mode() is True
    assert runner.tool_prefix(REPO, toolrun.STEUERAUSZUG_MODULE) == [sys.executable, "--run-tool", "opensteuerauszug.steuerauszug"]
    assert runner.build_kursliste_dir_cmd(REPO) == [sys.executable, "--tool-kursliste-dir"]


def test_all_build_cmds_use_bundled_prefix(bundled):
    gen = runner.build_steuerauszug_cmd(REPO, "in.xml", 2025, "out.pdf", "out.xml", kursliste_dir="kl")
    assert gen[0] == sys.executable
    assert gen[2:4] == ["--run-tool", "opensteuerauszug.steuerauszug"]
    assert gen[4:6] == ["in.xml", "--importer"] and "--kursliste-dir" in gen
    dl = runner.build_kursliste_download_cmd(REPO, 2024, "dest", convert=False)
    assert dl[2:5] == ["--run-tool", "opensteuerauszug.kursliste", "download"] and dl[-1] == "--no-convert"
    d = runner.build_kursliste_dir_cmd(REPO)
    assert d[-1] == "--tool-kursliste-dir" and "-c" not in d
    for cmd in (gen, dl, d):
        assert str(runner.tool_python(REPO)) not in cmd
        assert all(Path(p).name != ".venv" for p in cmd)


def test_tool_prefix_rejects_unknown_module(bundled):
    with pytest.raises(ValueError):
        runner.tool_prefix(REPO, "os")


def test_check_tool_bundled_uses_importability(bundled, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "bundled_module_importable", lambda: True)
    info = runner.check_tool(tmp_path)
    assert info["ok"] is True and info["bundled"] is True and info["importable"] is True
    monkeypatch.setattr(runner, "bundled_module_importable", lambda: False)
    assert runner.check_tool(tmp_path)["ok"] is False


def test_bundled_workdir_is_created(bundled, tmp_path):
    target = tmp_path / "neu" / "tool"
    code, out = runner.run_capture([sys.executable, "-c", "print('x')"], target)
    assert code == 0 and target.is_dir()


def test_resolve_tool_repo_bundled(bundled, isolated_profile):
    assert st.resolve_tool_repo("irgendwas") == (str(isolated_profile / "tool"), "bundled")


def test_describe_bundled_shows_short_commit(bundled):
    d = toolsetup.describe("x", "bundled")
    assert d["bundled"] is True and d["tool_source"] == "bundled"
    assert d["bundled_text"] == "mitgeliefert, Version 4633aab"
    assert d["installed_commit"] == "4633aab"


# ------------------------------------------------------------------ --run-tool

def test_allowlist_is_fixed():
    assert toolrun.ALLOWED_MODULES == {"opensteuerauszug.steuerauszug", "opensteuerauszug.kursliste"}


def test_run_module_rejects_other_modules(capsys):
    assert toolrun.run_module("os", ["x"]) == 2
    assert toolrun.run_module("opensteuerauszug.evil", []) == 2
    assert "nicht erlaubt" in capsys.readouterr().err


def test_run_module_sets_argv_and_returns_exit_code(monkeypatch):
    seen = {}

    def fake(module, run_name=None, alter_sys=False):
        seen.update(module=module, run_name=run_name, alter_sys=alter_sys, argv=list(sys.argv))
        raise SystemExit(3)

    monkeypatch.setattr(toolrun.runpy, "run_module", fake)
    monkeypatch.setattr(sys, "argv", ["prog"])
    assert toolrun.run_module("opensteuerauszug.kursliste", ["download", "--year", "2025"]) == 3
    assert seen == {"module": "opensteuerauszug.kursliste", "run_name": "__main__", "alter_sys": True,
                    "argv": ["opensteuerauszug.kursliste", "download", "--year", "2025"]}


def test_run_module_zero_without_exit(monkeypatch):
    monkeypatch.setattr(toolrun.runpy, "run_module", lambda *a, **k: None)
    assert toolrun.run_module("opensteuerauszug.steuerauszug", []) == 0


def test_main_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(toolrun, "run_module", lambda m, a: calls.append((m, a)) or 0)
    assert toolrun.main(["prog", "--run-tool", "opensteuerauszug.steuerauszug", "--help"]) == 0
    assert calls == [("opensteuerauszug.steuerauszug", ["--help"])]
    assert toolrun.main(["prog", "--run-tool"]) == 2
    assert toolrun.is_tool_invocation(["p", "--tool-kursliste-dir"]) and not toolrun.is_tool_invocation(["p", "--no-window"])


def test_launcher_script_rejects_forbidden_module_in_real_process():
    res = subprocess.run([sys.executable, str(LAUNCHER), "--run-tool", "os"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 2 and "nicht erlaubt" in res.stderr


# ------------------------------------------------------------------ Endpunkte

def test_tool_endpoints_409_when_bundled(client, bundled):
    r = client.post("/api/tool/install", json={"force": True})
    assert r.status_code == 409 and "mitgeliefert" in r.json()["detail"]
    for body in ({}, {"target": "latest"}):
        assert client.post("/api/tool/update", json=body).status_code == 409


def test_status_reports_bundled(client, bundled, monkeypatch):
    monkeypatch.setattr(runner, "bundled_module_importable", lambda: True)
    body = client.get("/api/status").json()
    assert body["ok"] is True and body["bundled"] is True
    assert body["app_version_number"] == "1.5.0"
    assert body["bundled_text"] == "mitgeliefert, Version 4633aab"
    assert body["data_dir"] == str(paths.data_dir())


def test_health_endpoint(client):
    assert client.get("/api/health").json()["app"] == "steuerauszug-assistent"


def test_open_folder_fixed_targets_only(client, monkeypatch, isolated_profile):
    opened = []
    monkeypatch.setattr(main, "open_folder", opened.append)
    assert client.post("/api/open-folder", json={"which": "data"}).status_code == 200
    assert client.post("/api/open-folder", json={"which": "output"}).status_code == 200
    assert opened == [isolated_profile, st.OUTPUT_DIR]
    assert isolated_profile.is_dir() and st.OUTPUT_DIR.is_dir()
    for bad in ({"which": "/etc"}, {"which": "../x"}, {"which": ""}, {}, {"which": ["data"]}):
        assert client.post("/api/open-folder", json=bad).status_code == 400
    assert len(opened) == 2


def test_quit_with_launcher_event(client, monkeypatch):
    exits = []
    monkeypatch.setattr(main, "exit_process", exits.append)
    monkeypatch.setattr(main, "quit_via_event", True)
    main.quit_event.clear()
    try:
        assert client.post("/api/app/quit", json={}).json() == {"quitting": True}
        assert main.quit_event.is_set() and exits == []
    finally:
        main.quit_event.clear()


def test_quit_without_launcher_exits_process(client, monkeypatch):
    done, codes = threading.Event(), []
    monkeypatch.setattr(main, "exit_process", lambda code: (codes.append(code), done.set()))
    monkeypatch.setattr(main, "RESTART_DELAY", 0)
    monkeypatch.setattr(main, "quit_via_event", False)
    main.quit_event.clear()
    try:
        assert client.post("/api/app/quit").status_code == 200
        assert done.wait(5) and codes == [0]
    finally:
        main.quit_event.clear()


def test_quit_refused_while_job_runs_unless_forced(client, monkeypatch):
    monkeypatch.setattr(main, "quit_via_event", True)
    gate = threading.Event()
    main.jobs.start("x", "x", lambda job: gate.wait(5) or True)
    main.quit_event.clear()
    try:
        assert client.post("/api/app/quit", json={}).status_code == 409
        assert not main.quit_event.is_set()
        assert client.post("/api/app/quit", json={"force": True}).status_code == 200
        assert main.quit_event.is_set()
    finally:
        gate.set()
        main.quit_event.clear()


# ------------------------------------------------------------------ Programm-Update-Karte

def test_program_mode_update_status(client, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    body = client.get("/api/app/update").json()
    assert body["mode"] == "program" and body["is_git"] is False
    assert body["version"] == "1.5.0" and body["releases_url"].endswith("/releases/latest")
    assert client.post("/api/app/update/check").status_code == 409
    assert client.post("/api/app/update/apply").status_code == 409


def test_program_update_is_separate_module():
    assert hasattr(program_update, "status") and hasattr(program_update, "check") and hasattr(program_update, "job_apply")
