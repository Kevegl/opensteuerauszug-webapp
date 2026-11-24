"""Versionswechsel des Programms (nur Standardbibliothek, wird ganz früh vom Startprogramm benutzt).

Aufbau im Datenordner:
  versions/<version>/      entpacktes Programm-ZIP einer per Update installierten Version
  versions/state.json      {"current": "1.5.1", "previous": "1.5.0", "pending": null,
                            "failed": {"version": "1.5.2", "reason": "...", "at": "..."},
                            "original": {"version": "1.5.0", "exe": "/pfad/zum/original-programm"}}

``current`` ist die Version, die laufen soll; ``previous`` die Version davor (Ziel von "Vorherige Version
wiederherstellen"); ``pending`` eine frisch installierte, noch nicht bestätigte Version. ``original`` merkt sich das
vom Benutzer heruntergeladene Programm (ohne Ordner in versions/), damit man dorthin zurückkehren kann.

Jedes gestartete Programm (Original-Download oder Version aus versions/) ruft ``delegate`` auf:
  * ``pending`` neuer als die eigene Version: neue Version starten, bis zu 60 s auf /api/health mit dieser Version
    warten (dieser Prozess bleibt dafür am Leben). Erfolg: current=pending. Misserfolg: Kind beenden, pending
    verwerfen, ``failed`` merken und selbst normal weiterstarten.
  * ``current`` neuer als die eigene Version (oder das eigene Programm liegt in versions/ und ``current`` ist ein
    anderes): diese Version starten und beenden. Eine vom Benutzer von Hand gestartete, neuere Version gewinnt.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import paths

APP_ID = "steuerauszug-assistent"
DELEGATED_ENV = "STEUERAUSZUG_DELEGATED"     # gesetzt im vom Startprogramm gestarteten Kind (keine Endlosschleife)
TRIAL_ENV = "STEUERAUSZUG_TRIAL"             # Kind läuft im Probebetrieb: keine Fehlerfenster
HEALTH_WAIT = 60
VERSION_RX = re.compile(r"^v?(\d+(?:\.\d+){1,3})$")
KEEP_VERSIONS = 2


def now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- Versionen

def parse_version(text) -> tuple[int, ...] | None:
    m = VERSION_RX.match(str(text or "").strip())
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def norm_version(text) -> str | None:
    """'v1.5.0' -> '1.5.0' (nur gültige Versionsnummern, sonst None). Schützt auch vor Pfadtricks."""
    m = VERSION_RX.match(str(text or "").strip())
    return m.group(1) if m else None


def is_newer(a, b) -> bool:
    pa, pb = parse_version(a), parse_version(b)
    return pa is not None and pb is not None and pa > pb


# ---------------------------------------------------------------- Zustand

def versions_root(data) -> Path:
    return Path(data) / "versions"


def state_file(data) -> Path:
    return versions_root(data) / "state.json"


def _clean_failed(raw):
    if isinstance(raw, dict) and norm_version(raw.get("version")):
        return {"version": norm_version(raw["version"]), "reason": str(raw.get("reason") or "")[:500],
                "at": str(raw.get("at") or "")}
    return None


def _clean_original(raw):
    if isinstance(raw, dict) and norm_version(raw.get("version")) and isinstance(raw.get("exe"), str):
        return {"version": norm_version(raw["version"]), "exe": raw["exe"]}
    return None


def read_state(data) -> dict:
    try:
        raw = json.loads(state_file(data).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    return {"current": norm_version(raw.get("current")), "previous": norm_version(raw.get("previous")),
            "pending": norm_version(raw.get("pending")), "failed": _clean_failed(raw.get("failed")),
            "original": _clean_original(raw.get("original"))}


def write_state(data, state: dict) -> None:
    root = versions_root(data)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "state.json.new"
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_file(data))


def version_dir(data, version) -> Path:
    v = norm_version(version)
    if not v:
        raise ValueError(f"Ungültige Versionsnummer: {version!r}")
    return versions_root(data) / v


def exe_in(directory: Path, system: str | None = None) -> Path:
    return Path(directory) / paths.program_exe_relpath(system)


def resolve_exe(data, version, state: dict | None = None) -> Path | None:
    """Programmdatei einer Version: aus versions/<version>/ oder das gemerkte Original. None, wenn nicht vorhanden."""
    v = norm_version(version)
    if not v:
        return None
    exe = exe_in(version_dir(data, v))
    if exe.is_file():
        return exe
    state = state if state is not None else read_state(data)
    orig = state.get("original")
    if orig and orig["version"] == v and Path(orig["exe"]).is_file():
        return Path(orig["exe"])
    return None


def is_managed(data, exe) -> bool:
    """True, wenn das Programm in versions/ liegt (per Update installiert)."""
    try:
        Path(exe).resolve().relative_to(versions_root(data).resolve())
        return True
    except (ValueError, OSError):
        return False


def note_original(data, version, exe) -> None:
    state = read_state(data)
    entry = {"version": norm_version(version), "exe": str(exe)}
    if entry["version"] and state["original"] != entry:
        state["original"] = entry
        try:
            write_state(data, state)
        except OSError:
            pass


def promote(data, version, previous) -> dict:
    """pending wurde gestartet und meldet sich: current=version, previous=bisher laufende Version."""
    state = read_state(data)
    state["current"] = norm_version(version)
    state["previous"] = norm_version(previous)
    state["pending"] = None
    failed = state["failed"]
    if failed and not is_newer(failed["version"], version):
        state["failed"] = None
    write_state(data, state)
    return state


def mark_failed(data, version, reason) -> dict:
    state = read_state(data)
    if state["pending"] == norm_version(version):
        state["pending"] = None
    state["failed"] = {"version": norm_version(version), "reason": str(reason)[:500], "at": now_text()}
    write_state(data, state)
    return state


def cleanup(data, protect=None) -> list[str]:
    """Löscht alles in versions/ außer current, previous und pending (höchstens 2 Versionen + eine neue).
    Nur direkte Unterordner von versions/ mit gültigem Versionsnamen bzw. liegengebliebene .partial-Ordner;
    der Ordner des laufenden Programms (``protect``) bleibt stehen. Gibt die gelöschten Namen zurück."""
    root = versions_root(data)
    if not root.is_dir():
        return []
    state = read_state(data)
    keep = {v for v in (state["current"], state["previous"], state["pending"]) if v}
    try:
        protected = Path(protect).resolve() if protect else None
    except OSError:
        protected = None
    removed = []
    root_real = root.resolve()
    for item in sorted(root.iterdir()):
        name = item.name
        stale_partial = name.endswith(".partial") and norm_version(name[:-len(".partial")])
        if not (norm_version(name) == name or stale_partial) or name in keep:
            continue
        try:
            if item.is_symlink():
                item.unlink()
            elif item.is_dir():
                real = item.resolve()
                if real.parent != root_real:
                    continue
                if protected is not None and (protected == real or real in protected.parents):
                    continue
                shutil.rmtree(real, ignore_errors=True)
            else:
                continue
            removed.append(name)
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- Programmstart

def clean_env(env=None, drop=()) -> dict:
    """Umgebung für ein neu gestartetes Programm: PyInstaller-Variablen des laufenden Programms entfernen."""
    env = dict(os.environ if env is None else env)
    for key in list(env):
        if key.startswith("_PYI_") or key == "_MEIPASS2":
            del env[key]
    for lib in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(lib + "_ORIG", None)
        if orig is not None:
            if orig:
                env[lib] = orig
            else:
                env.pop(lib, None)
    for key in drop:
        env.pop(key, None)
    return env


def popen_detached(cmd, env=None):
    """Startet ein Programm unabhängig vom aufrufenden Prozess (überlebt dessen Beenden)."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "env": env, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([str(c) for c in cmd], **kwargs)  # noqa: S603


def probe_version(port, timeout=2) -> str | None:
    """Version der auf dem Port laufenden App (/api/health) oder None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:  # noqa: S310
            body = json.loads(r.read(100_000).decode("utf-8", "replace"))
        if r.status == 200 and body.get("app") == APP_ID:
            return str(body.get("version") or "")
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return None


def plan(data, own_version, own_exe) -> tuple[str, str | None, Path | None]:
    """Entscheidet: ("self", None, None) | ("run", version, exe) | ("trial", version, exe).
    Verwirft dabei einen unbrauchbaren ``pending``-Eintrag."""
    state = read_state(data)
    managed = is_managed(data, own_exe)
    pending = state["pending"]
    if pending:
        exe = resolve_exe(data, pending, state)
        if is_newer(pending, own_version) and exe:
            return "trial", pending, exe
        if is_newer(pending, own_version):
            mark_failed(data, pending, "Die Programmdatei der neuen Version fehlt.")
        else:                                   # von Hand eine gleich neue/neuere Version gestartet
            state["pending"] = None
            write_state(data, state)
        state = read_state(data)
    current = state["current"]
    if current and current != norm_version(own_version):
        exe = resolve_exe(data, current, state)
        try:
            same = exe is not None and exe.resolve() == Path(own_exe).resolve()
        except OSError:
            same = False
        # neuere current-Version nutzen; ist das eigene Programm selbst verwaltet, gilt immer current
        # (Rückkehr zur vorherigen Version). Ein von Hand gestartetes, neueres Programm gewinnt.
        if exe and not same and (is_newer(current, own_version) or managed):
            return "run", current, exe
    return "self", None, None


def _stop(proc, wait=5) -> None:
    try:
        proc.terminate()
        proc.wait(wait)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
            proc.wait(wait)
        except Exception:  # noqa: BLE001
            pass


def delegate(data, own_version, own_exe, argv, *, port, env=None, popen: Callable = popen_detached,
             probe: Callable = probe_version, sleep: Callable = time.sleep, clock: Callable = time.monotonic,
             wait: float = HEALTH_WAIT, say: Callable = print) -> str:
    """Wechselt bei Bedarf auf eine andere installierte Version. Rückgabe: "self" (dieses Programm läuft
    weiter) oder "delegated" (eine andere Version läuft; dieser Prozess soll sich beenden)."""
    env = dict(os.environ if env is None else env)
    own_exe = Path(own_exe)
    managed = is_managed(data, own_exe)
    if not managed:
        note_original(data, own_version, own_exe)
    if env.get(DELEGATED_ENV) == "1":
        cleanup(data, protect=own_exe)
        return "self"
    if probe(port, 1) is not None:              # läuft schon: nichts umstellen, der normale Start öffnet nur den Browser
        return "self"
    action, version, exe = plan(data, own_version, own_exe)
    if action == "self":
        cleanup(data, protect=own_exe)
        return "self"
    child_env = clean_env(env)
    child_env[DELEGATED_ENV] = "1"
    if action == "trial":
        child_env[TRIAL_ENV] = "1"
    say(f"Starte Version {version} aus {exe}" + (" (Probebetrieb)" if action == "trial" else ""))
    try:
        proc = popen([exe, *argv], child_env)
    except OSError as exc:
        say(f"Version {version} lässt sich nicht starten: {exc}")
        if action == "trial":
            mark_failed(data, version, f"Programm lässt sich nicht starten: {exc}")
        return "self"
    if action == "run":
        return "delegated"
    deadline = clock() + wait
    reason = f"Die neue Version hat sich nicht innerhalb von {int(wait)} Sekunden gemeldet."
    while clock() < deadline:
        got = probe(port, 2)
        if got is not None and norm_version(got) == version:
            promote(data, version, own_version)
            cleanup(data, protect=own_exe)
            say(f"Version {version} läuft; vorherige Version: {own_version}.")
            return "delegated"
        code = proc.poll()
        if code is not None:
            reason = f"Die neue Version wurde beendet (Code {code}), bevor sie sich meldete."
            break
        sleep(0.4)
    _stop(proc)
    mark_failed(data, version, reason)
    cleanup(data, protect=own_exe)
    say(f"Update auf {version} fehlgeschlagen: {reason} Die vorherige Version ({own_version}) startet.")
    return "self"
