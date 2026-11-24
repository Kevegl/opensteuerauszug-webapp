"""Plattformabhängige Hilfen (Windows und macOS/Linux)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable


def is_windows() -> bool:
    return os.name == "nt"


def venv_python(venv_root: str | Path) -> Path:
    """Python-Interpreter in einem venv-Ordner (z. B. <Projekt>/.venv)."""
    root = Path(venv_root)
    if is_windows():
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def venv_python_hint() -> str:
    """Plattformneutrale Beschreibung für Meldungen."""
    return ".venv/Scripts/python.exe" if is_windows() else ".venv/bin/python"


def git_usable(
    which: Callable[[str], str | None],
    run_capture: Callable[[list[str]], int],
    platform: str | None = None,
) -> bool:
    """True, wenn git benutzbar ist.

    macOS: /usr/bin/git ist ohne Xcode Command Line Tools nur ein Stub, der einen
    Installationsdialog öffnet. Dann gilt git nur als vorhanden, wenn ``xcode-select -p`` Exit 0 liefert.
    ``run_capture(cmd)`` gibt den Exit-Code zurück.
    """
    path = which("git")
    if not path:
        return False
    if (platform or sys.platform) == "darwin" and path == "/usr/bin/git":
        return run_capture(["xcode-select", "-p"]) == 0
    return True
