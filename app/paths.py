"""Pfade: Datenordner (pro Benutzer), mitgelieferte Ressourcen, Version und einmalige Migration.

Gilt für beide Varianten (Programm und Python-Variante), damit man ohne Datenverlust wechseln kann.
Dieses Modul importiert bewusst nur die Standardbibliothek.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Callable, Mapping

APP_NAME = "Steuerauszug-Assistent"
ENV_DATA_DIR = "STEUERAUSZUG_DATA_DIR"
MIGRATION_MARKER = ".migriert-aus-projektordner"
DEFAULT_PORT = 8765
RELEASES_URL = "https://github.com/Kevegl/opensteuerauszug-webapp/releases/latest"


def system_tag(platform_name: str | None = None) -> str:
    """windows | macos | linux (so heißen die Systeme in den Namen der Release-Dateien)."""
    name = platform_name or sys.platform
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    return "linux"


def arch_tag(machine: str | None = None) -> str:
    """x64 | arm64 (oder die rohe Angabe in Kleinbuchstaben)."""
    m = (machine if machine is not None else platform.machine()).lower()
    return {"x86_64": "x64", "amd64": "x64", "x64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m or "unknown")


def asset_name(version: str, system: str | None = None, arch: str | None = None) -> str:
    """Name der ZIP-Datei im Release; build_app.py und die Update-Suche benutzen dieselbe Benennung."""
    return f"{APP_NAME}-{version}-{system or system_tag()}-{arch or arch_tag()}.zip"


def program_exe_relpath(system: str | None = None) -> Path:
    """Pfad der Programmdatei relativ zum entpackten ZIP (bzw. zu versions/<version>/)."""
    system = system or system_tag()
    if system == "windows":
        return Path(APP_NAME) / f"{APP_NAME}.exe"
    if system == "macos":
        return Path(f"{APP_NAME}.app") / "Contents" / "MacOS" / APP_NAME
    return Path(APP_NAME) / APP_NAME


def is_frozen() -> bool:
    """True im gebauten Programm (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def _resource_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).resolve().parent)
    return Path(__file__).resolve().parent.parent


# Projektordner (Python-Variante) bzw. Ordner des Programms
BASE_DIR = Path(sys.executable).resolve().parent if is_frozen() else Path(__file__).resolve().parent.parent
# Ordner mit static/ und app/VERSION: Projektordner bzw. sys._MEIPASS im gebauten Programm
RESOURCE_DIR = _resource_dir()
APP_DIR = RESOURCE_DIR / "app"
STATIC_DIR = RESOURCE_DIR / "static"
VERSION_FILE = APP_DIR / "VERSION"
# Ort der alten Datenstruktur (settings.json, work/, output/ im Projektordner). Tests ersetzen ihn.
LEGACY_DIR = None if is_frozen() else BASE_DIR


def port() -> int:
    """Port der App (nur 127.0.0.1). STEUERAUSZUG_PORT überschreibt ihn, z. B. für Tests."""
    try:
        return int((os.environ.get("STEUERAUSZUG_PORT") or "").strip() or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def version() -> str:
    """Programmversion aus app/VERSION."""
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text or "0.0.0"


def data_dir(platform: str | None = None, environ: Mapping[str, str] | None = None,
             home: Path | None = None) -> Path:
    """Datenordner des Benutzers. STEUERAUSZUG_DATA_DIR überschreibt den Standardort."""
    environ = os.environ if environ is None else environ
    override = (environ.get(ENV_DATA_DIR) or "").strip()
    if override:
        return Path(override).expanduser()
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    if platform.startswith("win"):
        local = (environ.get("LOCALAPPDATA") or "").strip()
        return (Path(local) if local else home / "AppData" / "Local") / APP_NAME
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    return home / ".local" / "share" / "steuerauszug-assistent"


def ensure_data_dir(path: Path | None = None) -> Path:
    path = path or data_dir()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def _copy_tree_keep_existing(src: Path, dst: Path) -> int:
    """Kopiert src nach dst, ohne vorhandene Dateien zu überschreiben. Gibt die Zahl kopierter Dateien zurück."""
    count = 0
    for item in sorted(src.rglob("*")):
        target = dst / item.relative_to(src)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count


def migrate_legacy(legacy: Path | None = None, data: Path | None = None,
                   log: Callable[[str], None] = print) -> bool:
    """Kopiert (nicht verschieben) settings.json, work/ und output/ aus dem Projektordner einmalig in den
    Datenordner. Nur wenn der Datenordner noch keine settings.json und keine Marker-Datei enthält.
    Vorhandenes wird nie überschrieben. Gibt True zurück, wenn kopiert wurde."""
    legacy = LEGACY_DIR if legacy is None else legacy
    if legacy is None:
        return False
    data = data or data_dir()
    legacy = Path(legacy)
    try:
        if legacy.resolve() == data.resolve():
            return False
        if (data / MIGRATION_MARKER).exists() or (data / "settings.json").exists():
            return False
        items = [n for n in ("settings.json", "work", "output") if (legacy / n).exists()]
        if not items:
            return False
        ensure_data_dir(data)
        copied = 0
        for name in items:
            src, dst = legacy / name, data / name
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                copied += _copy_tree_keep_existing(src, dst)
            elif not dst.exists():
                shutil.copy2(src, dst)
                copied += 1
        (data / MIGRATION_MARKER).write_text(
            f"Einmalig kopiert aus {legacy} ({', '.join(items)}). Das Original wurde nicht verändert.\n",
            encoding="utf-8")
    except OSError as exc:
        log(f"Migration in den Datenordner fehlgeschlagen: {exc}")
        return False
    log(f"Daten aus dem Projektordner in den Datenordner kopiert ({copied} Dateien): {data}")
    return True


def startup(log: Callable[[str], None] = print) -> Path:
    """Datenordner anlegen und einmalig migrieren."""
    data = ensure_data_dir()
    migrate_legacy(data=data, log=log)
    return data
