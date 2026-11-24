"""Update der eigenständigen Programmversion (gebautes Programm) über GitHub Releases.

  check()        fragt die neueste Release-Version ab (api.github.com, Timeout, kein Personenbezug)
  job_apply()    Job: lädt das ZIP (Größenlimit, Prüfsumme), entpackt es sicher nach <Datenordner>/versions/<version>/
                 und trägt es als "pending" ein; der Wechsel passiert beim nächsten Start (siehe versions.py)
  rollback()     "Vorherige Version wiederherstellen"
  auto_check_*   höchstens einmal täglich im Hintergrund, Ergebnis in <Datenordner>/updates/status.json

Anders als bei der Python-Variante (app/selfupdate.py, git) wird nicht nur Code überlagert: Das Programm bündelt
Python, Abhängigkeiten und das Tool, deshalb wird das komplette Programm-ZIP der neuen Version installiert.
Testbar: ``STEUERAUSZUG_UPDATE_API`` (Basis-URL statt https://api.github.com) und ``STEUERAUSZUG_UPDATE_REPO``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from . import paths, settings as st, versions

DEFAULT_REPO = "Kevegl/opensteuerauszug-webapp"
DEFAULT_API = "https://api.github.com"
ENV_API = "STEUERAUSZUG_UPDATE_API"
ENV_REPO = "STEUERAUSZUG_UPDATE_REPO"
MAX_DOWNLOAD = 200 * 1024 * 1024
MAX_UNPACKED = 1500 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_NOTES = 20000
API_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 30
CHECK_INTERVAL = 24 * 3600
SHA_RX = re.compile(r"\b[0-9a-fA-F]{64}\b")


class UpdateError(Exception):
    """Fehler mit deutscher Meldung für den Benutzer."""


# ---------------------------------------------------------------- Pfade

def updates_dir() -> Path:
    return paths.data_dir() / "updates"


def status_file() -> Path:
    return updates_dir() / "status.json"


def download_file() -> Path:
    return updates_dir() / "download.tmp"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- Netzwerk

def api_base() -> str:
    return (os.environ.get(ENV_API) or DEFAULT_API).strip().rstrip("/")


def repo_name() -> str:
    return (os.environ.get(ENV_REPO) or DEFAULT_REPO).strip().strip("/")


def latest_url() -> str:
    return f"{api_base()}/repos/{repo_name()}/releases/latest"


def _url_allowed(url: str) -> bool:
    """Nur https; http nur für den lokalen Testserver (wenn STEUERAUSZUG_UPDATE_API gesetzt ist)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return True
    return (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
            and bool(os.environ.get(ENV_API)))


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001  (ohne certifi: Zertifikate des Systems)
        return ssl.create_default_context()


def _open(url: str, timeout: float = API_TIMEOUT, accept: str | None = None):
    """Öffnet eine URL (Antwortobjekt mit .read(n) und .headers). Von Tests ersetzbar."""
    if not _url_allowed(url):
        raise UpdateError("Ungültige Adresse im Release (nur https erlaubt).")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Steuerauszug-Assistent/{paths.version()}",
        "Accept": accept or "*/*",
    })
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())  # noqa: S310


def net_error_text(exc: BaseException) -> str:
    if isinstance(exc, UpdateError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return "GitHub hat die Anfrage vorübergehend abgelehnt (Anfragelimit). Bitte später erneut versuchen."
        if exc.code == 404:
            return "Es gibt (noch) kein veröffentlichtes Release."
        return f"GitHub antwortet mit Fehler {exc.code}."
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLError):
        return "Die sichere Verbindung zu GitHub ist fehlgeschlagen (Zertifikat). Bitte Uhrzeit und Netzwerk prüfen."
    if isinstance(exc, (TimeoutError, OSError)) or isinstance(reason, (TimeoutError, OSError)):
        return "Keine Verbindung zu GitHub. Bitte die Internetverbindung prüfen."
    return f"Unerwarteter Fehler bei der Update-Suche: {exc.__class__.__name__}"


# ---------------------------------------------------------------- Prüfen

def _empty_result(current: str) -> dict:
    return {"ok": False, "current": current, "latest": None, "update_available": False, "notes": "",
            "asset_name": None, "asset_url": None, "asset_size": None, "sha_url": None, "html_url": None,
            "installable": False, "install_problem": None, "error": None}


def parse_release(rel: dict, current: str, system: str | None = None, arch: str | None = None) -> dict:
    """Wertet die Antwort von releases/latest aus (ohne Netzwerk)."""
    out = _empty_result(current)
    if not isinstance(rel, dict):
        out["error"] = "Unerwartete Antwort von GitHub."
        return out
    out["html_url"] = rel.get("html_url") if isinstance(rel.get("html_url"), str) else paths.RELEASES_URL
    if rel.get("draft") or rel.get("prerelease"):
        out["ok"] = True
        out["error"] = None
        return out
    latest = versions.norm_version(rel.get("tag_name"))
    if not latest:
        out["error"] = f"Die Release-Version „{str(rel.get('tag_name'))[:40]}“ ist keine gültige Versionsnummer."
        return out
    out["ok"] = True
    out["latest"] = latest
    out["notes"] = str(rel.get("body") or "")[:MAX_NOTES]
    out["update_available"] = versions.is_newer(latest, current)
    name = paths.asset_name(latest, system, arch)
    assets = {a.get("name"): a for a in rel.get("assets") or [] if isinstance(a, dict)}
    asset, sha = assets.get(name), assets.get(name + ".sha256")
    if asset:
        out["asset_name"] = name
        out["asset_url"] = asset.get("browser_download_url")
        size = asset.get("size")
        out["asset_size"] = size if isinstance(size, int) and size >= 0 else None
    if sha:
        out["sha_url"] = sha.get("browser_download_url")
    if out["update_available"]:
        if not asset or not isinstance(out["asset_url"], str):
            out["install_problem"] = (f"Für dieses System ({paths.system_tag()}-{paths.arch_tag()}) gibt es in "
                                      f"Version {latest} noch kein Programmpaket ({name}).")
        elif not isinstance(out["sha_url"], str):
            out["install_problem"] = "Das Release enthält keine Prüfsummen-Datei (.sha256); Installation nicht möglich."
        elif out["asset_size"] is not None and out["asset_size"] > MAX_DOWNLOAD:
            out["install_problem"] = "Das Paket ist größer als das erlaubte Limit (200 MB)."
        out["installable"] = out["install_problem"] is None
    return out


def _save_status(result: dict) -> dict:
    saved = {**result, "checked_ts": time.time(), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        _write_json(status_file(), saved)
    except OSError:
        pass
    return saved


def check(save: bool = True) -> dict:
    """Fragt GitHub ab. Gibt immer ein Ergebnis zurück; Fehler stehen (deutsch) in ``error``."""
    current = paths.version()
    try:
        with _open(latest_url(), API_TIMEOUT, accept="application/vnd.github+json") as r:
            rel = json.loads(r.read(5_000_000).decode("utf-8", "replace"))
        result = parse_release(rel, current)
    except Exception as exc:  # noqa: BLE001
        result = _empty_result(current)
        result["error"] = net_error_text(exc)
    return _save_status(result) if save else result


def last_check() -> dict | None:
    data = _read_json(status_file())
    return data if isinstance(data, dict) else None


def auto_check_due(now: float | None = None) -> bool:
    last = last_check()
    if not last:
        return True
    ts = last.get("checked_ts")
    now = time.time() if now is None else now
    return not isinstance(ts, (int, float)) or ts > now or now - ts >= CHECK_INTERVAL


def auto_check_enabled() -> bool:
    return bool(st.load_settings().get("auto_update_check", True))


def auto_check_if_due(sync: bool = False) -> threading.Thread | None:
    """Beim Start: höchstens einmal täglich im Hintergrund prüfen (abschaltbar). Fehler bleiben still."""
    try:
        if not auto_check_enabled() or not auto_check_due():
            return None
    except Exception:  # noqa: BLE001
        return None
    def run():
        try:
            check()
        except Exception:  # noqa: BLE001
            pass
    if sync:
        run()
        return None
    t = threading.Thread(target=run, name="update-check", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- Status für die UI

def status() -> dict:
    """Lokaler Zustand ohne Netzwerk, gleiche Schlüssel wie selfupdate.status plus mode/releases_url."""
    own = paths.version()
    data = paths.data_dir()
    state = versions.read_state(data)
    last = last_check()
    if last:
        last = dict(last)
        # Aktualisierung gegen die jetzt laufende Version (nach einem Update ist "update_available" nicht mehr wahr)
        last["current"] = own
        last["update_available"] = bool(last.get("ok") and versions.is_newer(last.get("latest"), own)
                                        and not state["pending"])
        if last["update_available"] and state["pending"] == last.get("latest"):
            last["update_available"] = False
    prev = state["previous"]
    can_rollback = bool(prev and prev != own and versions.resolve_exe(data, prev, state))
    failed = state["failed"] if state["failed"] and versions.is_newer(state["failed"]["version"], own) else None
    return {
        "mode": "program", "is_git": False,
        "reason": "Programmversion: Updates kommen über GitHub Releases.",
        "branch": None, "commit": None, "version": own, "dirty": False, "dirty_files": [],
        "upstream": None, "blocker": None,
        "restart_supported": True,
        "restart_required": bool(state["pending"]), "pending": state["pending"],
        "previous": prev if can_rollback else None, "can_rollback": can_rollback,
        "failed": failed,
        "auto_check": auto_check_enabled(),
        "last_check": last, "releases_url": paths.RELEASES_URL,
    }


# ---------------------------------------------------------------- Herunterladen

def _download(url: str, dest: Path, limit: int, log: Callable[[str], None]) -> tuple[str, int]:
    """Lädt url nach dest, bricht über ``limit`` Bytes ab. Gibt (sha256, Größe) zurück."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with _open(url, DOWNLOAD_TIMEOUT) as r:
        length = None
        try:
            length = int(r.headers.get("Content-Length")) if r.headers.get("Content-Length") else None
        except (TypeError, ValueError):
            length = None
        if length is not None and length > limit:
            raise UpdateError(f"Das Paket ist zu groß ({length // (1024 * 1024)} MB, erlaubt sind "
                              f"{limit // (1024 * 1024)} MB). Download abgebrochen.")
        step = max(length // 10, 1) if length else 10 * 1024 * 1024
        next_log = step
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UpdateError(f"Das Paket ist größer als das Limit von {limit // (1024 * 1024)} MB. "
                                      "Download abgebrochen.")
                digest.update(chunk)
                f.write(chunk)
                if total >= next_log:
                    pct = f" ({total * 100 // length} %)" if length else ""
                    log(f"  {total / 1e6:.1f} MB heruntergeladen{pct}")
                    next_log += step
    if length is not None and total != length:
        raise UpdateError(f"Download unvollständig ({total} von {length} Bytes).")
    return digest.hexdigest(), total


def _fetch_expected_sha(url: str) -> str:
    with _open(url, DOWNLOAD_TIMEOUT) as r:
        text = r.read(4097).decode("utf-8", "replace")
    if len(text) > 4096:
        raise UpdateError("Die Prüfsummen-Datei ist ungültig (zu groß).")
    m = SHA_RX.search(text)
    if not m:
        raise UpdateError("Die Prüfsummen-Datei enthält keine gültige SHA-256-Prüfsumme.")
    return m.group(0).lower()


# ---------------------------------------------------------------- Entpacken

def check_member_name(name: str) -> str:
    """Prüft einen ZIP-Eintragsnamen (kein Zip-Slip). Gibt den bereinigten Namen zurück."""
    if not name or "\x00" in name:
        raise UpdateError("Ungültiger Dateiname im Paket.")
    norm = name.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        raise UpdateError(f"Unsicherer Pfad im Paket (absolut): {name[:80]}")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        raise UpdateError(f"Unsicherer Pfad im Paket: {name[:80]}")
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _check_tree_links(root: Path) -> None:
    """Nach dem Entpacken (macOS): Symlinks dürfen nur innerhalb des Ordners zeigen."""
    base = root.resolve()
    for p in root.rglob("*"):
        if p.is_symlink():
            target = (p.parent / os.readlink(p)).resolve() if not os.path.isabs(os.readlink(p)) \
                else Path(os.readlink(p)).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                raise UpdateError(f"Unsicherer Symlink im Paket: {p.name}") from None


def safe_extract(zip_path: Path, dest: Path, system: str | None = None,
                 run: Callable = subprocess.run) -> int:
    """Entpackt das Programm-ZIP nach dest (leer anlegen). Gibt die Zahl der Einträge zurück."""
    system = system or paths.system_tag()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise UpdateError("Das heruntergeladene Paket ist kein gültiges ZIP.") from exc
    with zf:
        infos = zf.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise UpdateError("Das Paket ist leer oder hat zu viele Einträge.")
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise UpdateError("Das entpackte Paket wäre zu groß.")
        names = [check_member_name(i.filename) for i in infos]
        dest.mkdir(parents=True, exist_ok=True)
        if system == "macos":
            # ditto erhält Symlinks, Rechte und Signatur der .app; Symlinks nur innerhalb des Ordners
            res = run(["ditto", "-x", "-k", str(zip_path), str(dest)], capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                raise UpdateError("Entpacken fehlgeschlagen: " + (res.stderr or "").strip()[:200])
            _check_tree_links(dest)
            return len(infos)
        base = dest.resolve()
        for info, name in zip(infos, names):
            if _is_symlink(info):
                raise UpdateError(f"Symlinks sind im Paket nicht erlaubt: {name[:80]}")
            target = (base / name)
            try:
                target.resolve().relative_to(base)
            except ValueError:
                raise UpdateError(f"Unsicherer Pfad im Paket: {name[:80]}") from None
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            mode = (info.external_attr >> 16) & 0o777
            if os.name == "posix" and mode & 0o111:
                os.chmod(target, 0o755)
    return len(infos)


def verify_program(root: Path, system: str | None = None) -> Path:
    """Prüft, dass die Programmdatei vorhanden und ausführbar ist (setzt auf macOS/Linux das Ausführungsbit)."""
    exe = versions.exe_in(root, system)
    if not exe.is_file():
        raise UpdateError(f"Das Paket enthält die Programmdatei nicht ({paths.program_exe_relpath(system).as_posix()}).")
    if os.name == "posix":
        os.chmod(exe, exe.stat().st_mode | 0o111)
        if not os.access(exe, os.X_OK):
            raise UpdateError("Die Programmdatei ist nicht ausführbar.")
    return exe


def install_zip(zip_path: Path, version: str, data: Path, log: Callable[[str], None],
                system: str | None = None) -> Path:
    """Entpackt nach versions/<version>/ (über <version>.partial) und prüft die Programmdatei."""
    final = versions.version_dir(data, version)
    partial = final.with_name(final.name + ".partial")
    root = versions.versions_root(data)
    root.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(partial, ignore_errors=True)
    try:
        count = safe_extract(zip_path, partial, system)
        log(f"  {count} Einträge entpackt.")
        verify_program(partial, system)
        if final.exists():
            shutil.rmtree(final)
        os.replace(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return versions.exe_in(final, system)


# ---------------------------------------------------------------- Installieren (Job)

def job_apply() -> Callable:
    """Funktion für JobManager.start: Update herunterladen, prüfen, entpacken, als pending eintragen."""
    def run(job) -> bool:
        log = job.log
        data = paths.data_dir()
        tmp = download_file()
        try:
            log("Suche die neueste Version ...")
            info = check()
            if info.get("error"):
                log("FEHLER: " + info["error"])
                return False
            state = versions.read_state(data)
            if not info["update_available"]:
                log(f"Es ist keine neuere Version als {info['current']} verfügbar.")
                return False
            if state["pending"] == info["latest"] and versions.resolve_exe(data, state["pending"], state):
                log(f"Version {info['latest']} ist bereits installiert. Zum Aktivieren bitte neu starten.")
                return True
            if not info["installable"]:
                log("FEHLER: " + (info["install_problem"] or "Installation nicht möglich."))
                return False
            version = info["latest"]
            size = info["asset_size"] or 0
            free = shutil.disk_usage(data).free if data.exists() else shutil.disk_usage(data.parent).free
            if size and free < size * 3:
                log(f"FEHLER: Zu wenig freier Speicherplatz (benötigt etwa {size * 3 // (1024 * 1024)} MB).")
                return False
            log(f"Version {version} wird heruntergeladen ({info['asset_name']}"
                + (f", {size / 1e6:.1f} MB" if size else "") + ") ...")
            digest, total = _download(info["asset_url"], tmp, MAX_DOWNLOAD, log)
            log(f"Download fertig ({total / 1e6:.1f} MB). Prüfe Prüfsumme ...")
            expected = _fetch_expected_sha(info["sha_url"])
            if digest != expected:
                log("FEHLER: Die Prüfsumme stimmt nicht überein. Das Paket wird verworfen und nicht installiert.")
                return False
            log("Prüfsumme stimmt (SHA-256).")
            log("Entpacke ...")
            install_zip(tmp, version, data, log)
            state = versions.read_state(data)
            state["pending"] = version
            if state["failed"] and state["failed"]["version"] == version:
                state["failed"] = None
            versions.write_state(data, state)
            versions.cleanup(data)
            log(f"Version {version} ist installiert. Neustart nötig, um sie zu starten; "
                "bei einem Startproblem kehrt das Programm automatisch zur jetzigen Version zurück.")
            job.results.append({"kind": "restart_required", "version": version})
            return True
        except UpdateError as exc:
            log("FEHLER: " + str(exc))
            return False
        except OSError as exc:
            log(f"FEHLER: {net_error_text(exc) if isinstance(exc, (urllib.error.URLError, TimeoutError)) else exc}")
            return False
        finally:
            tmp.unlink(missing_ok=True)
    return run


# ---------------------------------------------------------------- Zurück

def rollback() -> str:
    """Setzt current auf die vorherige Version (previous wird leer). Gibt die Version zurück."""
    data = paths.data_dir()
    state = versions.read_state(data)
    prev = state["previous"]
    if not prev or not versions.resolve_exe(data, prev, state):
        raise UpdateError("Es gibt keine vorherige Version, zu der zurückgekehrt werden kann.")
    if prev == paths.version():
        raise UpdateError("Die vorherige Version läuft bereits.")
    state["current"], state["previous"], state["pending"] = prev, None, None
    versions.write_state(data, state)
    return prev
