"""Konsistenz von Version, Tool-Pin, Build-Abhängigkeiten, Changelog und Workflow."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver_and_in_changelog():
    version = (ROOT / "app" / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[v{re.escape(version)}\] – ", changelog, re.M)
    assert f"[v{version}]: https://github.com/Kevegl/opensteuerauszug-webapp/releases/tag/v{version}" in changelog


def test_build_requirements_match_tool_pin():
    pin = json.loads((ROOT / "app" / "tool_version.json").read_text(encoding="utf-8"))
    reqs = (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
    assert re.search(r"^pyinstaller==6\.\d+\.\d+$", reqs, re.M)
    assert f"opensteuerauszug @ git+{pin['repo']}@{pin['commit']}" in reqs
    assert "opensteuerauszug" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_build_script_pin_spec_uses_tool_version_json():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_app", ROOT / "packaging" / "build_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pin = mod.tool_pin()
    assert mod.pin_spec(pin).endswith("@" + pin["commit"])
    assert mod.NAME == "Steuerauszug-Assistent" and mod.BUNDLE_ID == "io.github.kevegl.steuerauszug-assistent"
    cmd = " ".join(mod.pyinstaller_cmd(Path("dist"), Path("build"), "linux"))
    for needle in ("--windowed", "--collect-all opensteuerauszug", "tool_version.json", "VERSION", "launcher.py"):
        assert needle in cmd


def test_release_workflow_basics():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for needle in ("tags: [\"v*\"]", "workflow_dispatch", "windows-latest", "macos-latest", "tests/smoke_app.py",
                   "gh release upload", "app/VERSION", "CHANGELOG.md"):
        assert needle in wf
