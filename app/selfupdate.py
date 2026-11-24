"""Selbst-Update der App per Git (nur Fast-Forward, nie reset --hard, nie clean, nie Force).

Befehlsausführung läuft über ``Deps`` und lässt sich in Tests ersetzen. Git wird ohne Shell,
mit Timeout, im Projektordner und mit ``GIT_TERMINAL_PROMPT=0`` aufgerufen.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import runner, settings as st
from .platform_utils import git_usable

Log = Callable[[str], None]

RESTART_EXIT_CODE = 3
FETCH_TIMEOUT = 30
APPLY_FETCH_TIMEOUT = 120
LOCAL_TIMEOUT = 15
MAX_COMMITS = 20
FALLBACK_UPSTREAM = "origin/main"
CRED_RE = re.compile(r"(\w+://)[^/@\s]+@")


def default_run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    """Führt einen Befehl ohne Shell aus. Rückgabe: (Exit-Code, stdout, stderr); -1 bei Start-/Timeout-Fehler."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        res = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"Zeitüberschreitung nach {timeout} s"
    except OSError as exc:
        return -1, "", str(exc)
    return res.returncode, res.stdout or "", res.stderr or ""


@dataclass
class Deps:
    run: Callable[[list[str], Path, int], tuple[int, str, str]] = default_run
    run_streaming: Callable = runner.run_streaming  # (cmd, cwd, log) -> Exit-Code (für pip)
    which: Callable[[str], str | None] = field(default=None)  # type: ignore[assignment]
    platform: str | None = None
    python: str = ""
    base_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.which is None:
            import shutil
            self.which = shutil.which
        if not self.python:
            self.python = sys.executable

    @property
    def cwd(self) -> Path:
        return self.base_dir or st.BASE_DIR


# --------------------------------------------------------------------------- Zustand

_lock = threading.Lock()
_state: dict = {"restart_required": False, "last_check": None, "updated_from": None, "updated_to": None}


def reset_state() -> None:
    with _lock:
        _state.update(restart_required=False, last_check=None, updated_from=None, updated_to=None)


def restart_supported() -> bool:
    return os.environ.get("STEUERAUSZUG_LAUNCHER") == "1"


def _scrub(text: str) -> str:
    """Zugangsdaten in URLs (https://user:token@host) nie ausgeben."""
    return CRED_RE.sub(r"\1***@", text)


def _last_line(*texts: str) -> str:
    for text in texts:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            return _scrub(lines[-1])[:300]
    return ""


def _git(deps: Deps, args: list[str], timeout: int = LOCAL_TIMEOUT) -> tuple[int, str, str]:
    return deps.run(["git", *args], deps.cwd, timeout)


def _git_out(deps: Deps, args: list[str], timeout: int = LOCAL_TIMEOUT) -> str | None:
    code, out, _ = _git(deps, args, timeout)
    text = out.strip()
    return text if code == 0 and text else None


def _has_git(deps: Deps) -> bool:
    return git_usable(deps.which, lambda cmd: deps.run(cmd, deps.cwd, LOCAL_TIMEOUT)[0], deps.platform)


def _upstream(deps: Deps) -> str | None:
    up = _git_out(deps, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if up:
        return up
    code, _, _ = _git(deps, ["rev-parse", "--verify", "--quiet", f"{FALLBACK_UPSTREAM}^{{commit}}"])
    return FALLBACK_UPSTREAM if code == 0 else None


def _dirty_files(deps: Deps) -> list[str] | None:
    code, out, _ = _git(deps, ["status", "--porcelain", "--untracked-files=no"])
    if code != 0:
        return None
    return [ln[3:].strip() for ln in out.splitlines() if ln.strip()]


def _blocker(branch: str | None, dirty: list[str]) -> str | None:
    if branch is None:
        return ("Das Update ist nicht möglich: HEAD ist losgelöst (detached). "
                "Bitte zuerst auf einen Branch wechseln, z. B. mit: git checkout main")
    if dirty:
        return ("Das Update ist nicht möglich: Verfolgte Dateien wurden geändert ("
                + ", ".join(dirty[:10]) + (" ..." if len(dirty) > 10 else "")
                + "). Änderungen bitte sichern, z. B. mit: git stash")
    return None


def status(deps: Deps | None = None) -> dict:
    """Lokaler Zustand ohne Netzwerk."""
    deps = deps or Deps()
    with _lock:
        snap = dict(_state)
    info: dict = {
        "is_git": False, "reason": None, "branch": None, "commit": None, "version": None,
        "dirty": False, "dirty_files": [], "upstream": None, "blocker": None,
        "restart_supported": restart_supported(), "restart_required": snap["restart_required"],
        "last_check": snap["last_check"],
    }
    if not (deps.cwd / ".git").exists():
        info["reason"] = "kein Git-Klon"
        return info
    if not _has_git(deps):
        info["reason"] = "git nicht installiert"
        return info
    info["is_git"] = True
    code, out, _ = _git(deps, ["symbolic-ref", "--short", "-q", "HEAD"])
    info["branch"] = out.strip() if code == 0 and out.strip() else None
    info["commit"] = _git_out(deps, ["rev-parse", "--short", "HEAD"])
    info["version"] = _git_out(deps, ["describe", "--tags", "--always", "--dirty"]) or info["commit"]
    dirty = _dirty_files(deps)
    info["dirty_files"] = dirty or []
    info["dirty"] = bool(dirty)
    info["upstream"] = _upstream(deps)
    info["blocker"] = _blocker(info["branch"], info["dirty_files"])
    return info


def app_version(deps: Deps | None = None) -> str | None:
    """Billige Versionsangabe für /api/status (ohne Netz, None ohne Git)."""
    deps = deps or Deps()
    if not (deps.cwd / ".git").exists() or not _has_git(deps):
        return None
    return _git_out(deps, ["describe", "--tags", "--always", "--dirty"])


# --------------------------------------------------------------------------- Prüfen

def _counts(deps: Deps, upstream: str) -> tuple[int, int] | None:
    """(ahead, behind) von HEAD gegenüber upstream."""
    out = _git_out(deps, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
    if not out:
        return None
    parts = out.split()
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def _fetch(deps: Deps, timeout: int) -> str | None:
    """Gibt None bei Erfolg zurück, sonst eine deutsche Fehlermeldung."""
    code, out, err = _git(deps, ["fetch", "--tags", "--prune", "origin"], timeout)
    if code == 0:
        return None
    detail = _last_line(err, out)
    if "Zeitüberschreitung" in detail:
        return f"Keine Antwort vom Server ({detail}). Bitte die Internetverbindung prüfen."
    return ("Die Update-Suche ist fehlgeschlagen (kein Netz oder Remote nicht erreichbar)"
            + (f": {detail}" if detail else "."))


def check(deps: Deps | None = None) -> dict:
    """Sucht Updates (Netz). Wirft nie eine Exception, Fehler stehen in ``error``."""
    deps = deps or Deps()
    result: dict = {
        "ok": False, "error": None, "behind": 0, "ahead": 0, "latest_tag": None, "commits": [],
        "update_available": False, "upstream": None, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        if not (deps.cwd / ".git").exists():
            result["error"] = "Kein Git-Klon: Die Update-Funktion braucht eine Installation per git clone."
        elif not _has_git(deps):
            result["error"] = "git ist nicht installiert."
        else:
            err = _fetch(deps, FETCH_TIMEOUT)
            upstream = _upstream(deps)
            result["upstream"] = upstream
            if err:
                result["error"] = err
            elif not upstream:
                result["error"] = "Kein Upstream-Branch gefunden (weder @{u} noch origin/main)."
            else:
                counts = _counts(deps, upstream)
                if counts is None:
                    result["error"] = "Der Stand konnte nicht mit dem Upstream verglichen werden."
                else:
                    result["ahead"], result["behind"] = counts
                    result["latest_tag"] = _git_out(deps, ["describe", "--tags", "--abbrev=0", upstream])
                    out = _git_out(deps, ["log", "--format=%h%x09%s", f"-n{MAX_COMMITS}", f"HEAD..{upstream}"]) or ""
                    for line in out.splitlines():
                        h, _, subject = line.partition("\t")
                        result["commits"].append({"hash": h, "subject": subject})
                    result["update_available"] = result["behind"] > 0
                    result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - nie eine Exception nach außen
        result["error"] = f"Unerwarteter Fehler bei der Update-Suche: {_scrub(str(exc))[:200]}"
    with _lock:
        _state["last_check"] = result
    return result


# --------------------------------------------------------------------------- Installieren

def run_apply(log: Log, deps: Deps | None = None) -> bool:
    """Fast-Forward auf den Upstream, ggf. pip. True bei Erfolg (auch wenn schon aktuell)."""
    deps = deps or Deps()
    try:
        return _apply(log, deps)
    except Exception as exc:  # noqa: BLE001
        log(f"FEHLER: {_scrub(str(exc))}")
        return False


def _apply(log: Log, deps: Deps) -> bool:
    if not (deps.cwd / ".git").exists():
        log("FEHLER: Kein Git-Klon. Die Update-Funktion braucht eine Installation per git clone.")
        return False
    if not _has_git(deps):
        log("FEHLER: git ist nicht installiert.")
        return False
    code, out, _ = _git(deps, ["symbolic-ref", "--short", "-q", "HEAD"])
    branch = out.strip() if code == 0 and out.strip() else None
    dirty = _dirty_files(deps)
    if dirty is None:
        log("FEHLER: Der Zustand des Arbeitsverzeichnisses konnte nicht gelesen werden.")
        return False
    blocker = _blocker(branch, dirty)
    if blocker:
        log("FEHLER: " + blocker)
        return False
    upstream = _upstream(deps)
    if not upstream:
        log("FEHLER: Kein Upstream-Branch gefunden (weder @{u} noch origin/main).")
        return False
    old = _git_out(deps, ["rev-parse", "HEAD"])
    if not old:
        log("FEHLER: Der aktuelle Stand (HEAD) konnte nicht gelesen werden.")
        return False

    log(f"Hole Änderungen von {upstream} ...")
    err = _fetch(deps, APPLY_FETCH_TIMEOUT)
    if err:
        log("FEHLER: " + err)
        return False
    counts = _counts(deps, upstream)
    if counts is None:
        log("FEHLER: Der Stand konnte nicht mit dem Upstream verglichen werden.")
        return False
    ahead, behind = counts
    if ahead > 0:
        log(f"FEHLER: Der lokale Stand ist {ahead} Commit(s) voraus"
            + (f" und {behind} zurück" if behind else "")
            + " (divergiert). Es wird nichts verändert; bitte manuell mit git abgleichen.")
        return False
    if behind == 0:
        log("Die App ist bereits auf dem neuesten Stand.")
        return True

    log(f"Installiere {behind} neue Änderung(en) (nur Fast-Forward) ...")
    code, out, err = _git(deps, ["merge", "--ff-only", upstream], APPLY_FETCH_TIMEOUT)
    if code != 0:
        log("FEHLER: Das Update konnte nicht eingespielt werden: " + (_last_line(err, out) or f"Exit-Code {code}"))
        return False
    new = _git_out(deps, ["rev-parse", "HEAD"]) or upstream

    names = _git_out(deps, ["diff", "--name-only", old, new]) or ""
    if "requirements.txt" in {n.strip() for n in names.splitlines()}:
        log("requirements.txt hat sich geändert, installiere Abhängigkeiten ...")
        pip_code = deps.run_streaming([deps.python, "-m", "pip", "install", "-r", "requirements.txt"], deps.cwd, log)
        if pip_code != 0:
            log(f"FEHLER: pip install ist fehlgeschlagen (Exit-Code {pip_code}). Stelle den alten Stand wieder her ...")
            rcode, _, rerr = _git(deps, ["reset", "--keep", old], APPLY_FETCH_TIMEOUT)
            if rcode == 0:
                log("Der alte Stand wurde wiederhergestellt. Das Update wurde nicht installiert.")
            else:
                log("FEHLER: Der alte Stand konnte nicht wiederhergestellt werden: " + _last_line(rerr)
                    + f" (alter Stand: {old[:7]}; manuell: git reset --keep {old[:7]})")
            return False

    with _lock:
        _state.update(restart_required=True, updated_from=old[:7], updated_to=new[:7], last_check=None)
    log(f"Update installiert: {old[:7]} → {new[:7]}. Bitte App neu starten.")
    return True


def job_apply(deps: Deps | None = None):
    def run(job) -> bool:
        return run_apply(job.log, deps)
    return run


# --------------------------------------------------------------------------- Neustart

def schedule_restart(exit_func: Callable[[int], None], delay: float = 0.5) -> threading.Timer:
    """Beendet den Prozess nach ``delay`` Sekunden mit RESTART_EXIT_CODE (Starter startet neu)."""
    timer = threading.Timer(delay, lambda: exit_func(RESTART_EXIT_CODE))
    timer.daemon = True
    timer.start()
    return timer
