#!/usr/bin/env python3
"""Baut die eigenständige Programmversion (Windows .exe, macOS .app, Linux-Ordner) mit PyInstaller.

Läuft in GitHub Actions (.github/workflows/release.yml), geht aber auch lokal:

  pip install -r requirements.txt -r packaging/requirements-build.txt
  python packaging/build_app.py     -> dist/Steuerauszug-Assistent-<version>-<system>-<arch>.zip (+ .sha256)
  python packaging/build_app.py --out <Ordner>   baut nach <Ordner>/build und <Ordner>/dist (z. B. für Tests)

Das Programm enthält Python, die App, das Startprogramm (app/launcher.py) und das Tool opensteuerauszug samt
Abhängigkeiten. Der Commit des Tools steht nur in app/tool_version.json; fehlt er in der Build-Umgebung, wird er
hier installiert (pip install "opensteuerauszug @ git+<repo>@<commit>").
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import paths  # noqa: E402  (nur Standardbibliothek; Benennung der Release-Dateien an einer Stelle)

APP = ROOT / "app"
NAME = paths.APP_NAME
BUNDLE_ID = "io.github.kevegl.steuerauszug-assistent"
# Pakete mit Daten, Schriften oder dynamischen Importen: komplett einsammeln
COLLECT_ALL = ["opensteuerauszug", "reportlab", "pdf417gen", "barcode", "ibflex", "holidays", "pydantic_xml", "pypdf"]
COLLECT_SUBMODULES = ["uvicorn", "app"]
HIDDEN_IMPORTS = ["python_multipart", "multipart", "h11", "tkinter", "tkinter.ttk", "tkinter.messagebox"]
EXCLUDES = ["pytest", "_pytest"]

for _s in (sys.stdout, sys.stderr):   # Windows-Konsole in GitHub Actions: cp1252 -> UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def version():
    return (APP / "VERSION").read_text(encoding="utf-8").strip()


def tool_pin():
    pin = json.loads((APP / "tool_version.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{40}", pin.get("commit", "")):
        raise SystemExit("app/tool_version.json: 'commit' muss ein vollständiger SHA-1 sein.")
    return pin


def pin_spec(pin):
    return f"opensteuerauszug @ git+{pin['repo']}@{pin['commit']}"


def installed_tool_commit():
    from importlib import metadata
    try:
        raw = metadata.distribution("opensteuerauszug").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    try:
        return json.loads(raw or "{}").get("vcs_info", {}).get("commit_id")
    except ValueError:
        return None


def ensure_tool(pin):
    """Stellt sicher, dass genau der gepinnte Commit des Tools installiert ist."""
    if installed_tool_commit() == pin["commit"]:
        print("Tool opensteuerauszug:", pin["commit"][:7], "(wie in app/tool_version.json)", flush=True)
        return
    print("Installiere das Tool im gepinnten Stand", pin["commit"][:7], "…", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", pin_spec(pin)], check=True)
    if installed_tool_commit() != pin["commit"]:
        raise SystemExit("Das installierte Tool entspricht nicht dem Commit aus app/tool_version.json.")


def pyinstaller_cmd(dist, build, system):
    sep = os.pathsep
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
           "--distpath", str(dist), "--workpath", str(build / "work"), "--specpath", str(build),
           "--paths", str(ROOT),
           "--add-data", f"{ROOT / 'static'}{sep}static",
           "--add-data", f"{APP / 'VERSION'}{sep}app",
           "--add-data", f"{APP / 'tool_version.json'}{sep}app",
           "--osx-bundle-identifier", BUNDLE_ID]
    for pkg in COLLECT_ALL:
        cmd += ["--collect-all", pkg]
    for pkg in COLLECT_SUBMODULES:
        cmd += ["--collect-submodules", pkg]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(APP / "launcher.py"))
    return cmd


def main():
    ap = argparse.ArgumentParser(description="Baut das Programm (PyInstaller) und das Release-ZIP samt .sha256")
    ap.add_argument("--out", type=Path, default=ROOT, help="Ausgabeordner mit build/ und dist/ (Standard: Projektordner)")
    args = ap.parse_args()
    system = paths.system_tag()
    arch = paths.arch_tag()
    build = args.out.resolve() / "build"
    dist = args.out.resolve() / "dist"
    pin = tool_pin()
    ensure_tool(pin)
    shutil.rmtree(build, ignore_errors=True)
    shutil.rmtree(dist, ignore_errors=True)
    print(f"PyInstaller baut {NAME} {version()} für {system}-{arch} …", flush=True)
    subprocess.run(pyinstaller_cmd(dist, build, system), check=True, cwd=str(ROOT))

    out = dist / paths.asset_name(version(), system, arch)
    if system == "macos":
        # ditto erhält Symlinks und Signatur des .app-Pakets
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(dist / f"{NAME}.app"), str(out)], check=True)
    else:
        folder = dist / NAME
        shutil.copy(ROOT / "packaging" / "LIESMICH.txt", folder / "LIESMICH.txt")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(folder.rglob("*")):
                z.write(f, Path(NAME) / f.relative_to(folder))
    # Prüfsumme für die Integritätsprüfung beim Programm-Update (Format wie sha256sum: "<hash>  <datei>")
    digest = hashlib.sha256()
    with open(out, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    sha_file = out.with_name(out.name + ".sha256")
    sha_file.write_text(f"{digest.hexdigest()}  {out.name}\n", encoding="utf-8")
    print("Fertig:", out, f"({out.stat().st_size / 1e6:.1f} MB)")
    print("Prüfsumme:", sha_file.name, digest.hexdigest())
    # Pfade für den Kurztest und den Upload in GitHub Actions
    exe = dist / paths.program_exe_relpath(system)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"zip={out}\nsha={sha_file}\nexe={exe}\n")


if __name__ == "__main__":
    main()
