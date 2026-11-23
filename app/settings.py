"""Einstellungen (settings.json im Projektordner)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BASE_DIR / "settings.json"
UPLOAD_DIR = BASE_DIR / "work" / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
APP_TOOL_DIR = BASE_DIR / "tool" / "opensteuerauszug"

DEFAULTS: dict[str, Any] = {
    # Leer = automatisch wählen (siehe resolve_tool_repo)
    "tool_repo": "",
    "flex_token": "",
    # [{"name": "Konto U123", "query_id": "123456"}]
    "flex_queries": [],
}

_lock = threading.Lock()


def home_tool_dir() -> Path:
    return Path.home() / "opensteuerauszug"


def resolve_tool_repo(explicit: str | None) -> tuple[str, str]:
    """Wählt den Tool-Ordner. Rückgabe: (Pfad, Herkunft) mit Herkunft settings | env | home | app.

    Reihenfolge: settings.json, Umgebungsvariable, bestehende Installation unter
    %USERPROFILE%/opensteuerauszug (nur mit vorhandenem .venv), sonst app-eigener Ordner.
    """
    if explicit and explicit.strip():
        return explicit.strip(), "settings"
    env = os.environ.get("OPENSTEUER_TOOL_REPO")
    if env and env.strip():
        return env.strip(), "env"
    home = home_tool_dir()
    if (home / ".venv" / "Scripts" / "python.exe").exists():
        return str(home), "home"
    return str(APP_TOOL_DIR), "app"


def load_settings(path: Path | None = None) -> dict[str, Any]:
    path = path or SETTINGS_FILE
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    result = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    explicit = result.get("tool_repo")
    result["tool_repo"], result["tool_repo_source"] = resolve_tool_repo(explicit if isinstance(explicit, str) else "")
    result["flex_queries"] = _clean_queries(result.get("flex_queries"))
    return result


def _clean_queries(queries: Any) -> list[dict[str, str]]:
    out = []
    for q in queries or []:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("query_id", "")).strip()
        if not qid:
            continue
        out.append({"name": str(q.get("name", "")).strip() or f"Query {qid}", "query_id": qid})
    return out


def save_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    path = path or SETTINGS_FILE
    clean = {k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}
    # Nur ein ausdrücklich gesetzter Pfad wird gespeichert; automatisch gewählte nicht.
    if settings.get("tool_repo_source", "settings") != "settings":
        clean["tool_repo"] = ""
    clean["flex_queries"] = _clean_queries(clean["flex_queries"])
    with _lock:
        path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 4:
        return "*" * len(token)
    return "*" * (len(token) - 4) + token[-4:]


def public_view(settings: dict[str, Any]) -> dict[str, Any]:
    """Einstellungen für die UI: Token nur maskiert."""
    view = {k: v for k, v in settings.items() if k != "flex_token"}
    view["flex_token_masked"] = mask_token(settings.get("flex_token", ""))
    view["flex_token_set"] = bool(settings.get("flex_token"))
    return view


def update_settings(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Wendet ein Patch aus der UI an. Leerer/maskierter Token = unverändert."""
    new = dict(current)
    if "tool_repo" in patch and not str(patch["tool_repo"] or "").strip():
        # Leer = ausdrücklich gesetzten Pfad entfernen, danach wählt resolve_tool_repo automatisch.
        if current.get("tool_repo_source", "settings") == "settings" and current.get("tool_repo"):
            new["tool_repo"], new["tool_repo_source"] = resolve_tool_repo("")
    elif "tool_repo" in patch:
        value = str(patch["tool_repo"]).strip()
        # Unveränderter, automatisch gewählter Pfad (UI schickt das Feld immer mit) wird nicht festgeschrieben.
        if not (current.get("tool_repo_source", "settings") != "settings" and value == current.get("tool_repo")):
            new["tool_repo"] = value
            new["tool_repo_source"] = "settings"
    token = patch.get("flex_token")
    if isinstance(token, str) and token.strip() and "*" not in token:
        new["flex_token"] = token.strip()
    if patch.get("clear_token"):
        new["flex_token"] = ""
    if "flex_queries" in patch:
        new["flex_queries"] = _clean_queries(patch["flex_queries"])
    return new
