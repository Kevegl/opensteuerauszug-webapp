"""Installation und Aktualisierung des Tools opensteuerauszug.

Befehlsaufbau und Textlogik sind reine Funktionen. Netzwerk und Subprozesse laufen über
das Objekt ``Deps`` und lassen sich in Tests ersetzen. Nutzerdaten im Tool-Ordner werden nie
gelöscht (kein ``git clean``, kein ``reset --hard``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from . import runner

Log = Callable[[str], None]

REPO_URL = "https://github.com/vroonhof/opensteuerauszug.git"
DEFAULT_SLUG = "vroonhof/opensteuerauszug"
PIN_FILE = Path(__file__).with_name("tool_version.json")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MARKER_NAME = ".steuerauszug-assistent.json"
MAX_ZIP_BYTES = 300 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
PROTECTED_FILES = ("config.toml", "data/security_identifiers.csv")
KEEP_DIRS = {".venv", ".git"}

IBFLEX_OLD = '"ibflex @ git+https://github.com/vroonhof/ibflex.git"'
IBFLEX_NEW = '"ibflex2 @ git+https://github.com/vroonhof/ibflex.git"'
IBFLEX_OLD_RE = re.compile(r'"ibflex @ (git\+https://github\.com/vroonhof/ibflex\.git)"')
IBFLEX_NEW_RE = re.compile(r'"ibflex2 @ git\+https://github\.com/vroonhof/ibflex\.git"')


class SetupError(Exception):
    """Deutsche Fehlermeldung für den Nutzer."""


# --------------------------------------------------------------------------- Abhängigkeiten

def default_base_python() -> str:
    """Basis-Python des laufenden (venv-)Interpreters."""
    base = getattr(sys, "_base_executable", None)
    if base and Path(base).exists() and Path(base).resolve() != Path(sys.executable).resolve():
        return str(base)
    for cand in (Path(sys.base_prefix) / "python.exe", Path(sys.base_prefix) / "bin" / "python3"):
        if cand.exists():
            return str(cand)
    return sys.executable


def default_download(url: str, dest: Path, log: Log, max_bytes: int = MAX_ZIP_BYTES) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "steuerauszug-assistent"})
    total = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:  # noqa: S310
        while chunk := resp.read(1024 * 256):
            total += len(chunk)
            if total > max_bytes:
                raise SetupError("Download ist unerwartet gross, Abbruch.")
            out.write(chunk)
    log(f"Heruntergeladen: {total / 1e6:.1f} MB")


@dataclass
class Deps:
    run_streaming: Callable = runner.run_streaming  # (cmd, cwd, log) -> exit code
    run_capture: Callable = runner.run_capture      # (cmd, cwd) -> (code, output)
    which: Callable[[str], str | None] = shutil.which
    download: Callable = default_download           # (url, dest, log) -> None
    base_python: Callable[[], str] = default_base_python
    check_tool: Callable = runner.check_tool
    tmp_root: Path | None = None
    zip_url: str | None = None   # Override für Tests; sonst aus Pin bzw. main abgeleitet
    repo_url: str | None = None  # Override für Tests; sonst aus dem Pin
    pin: dict | None = None      # Override für Tests; sonst app/tool_version.json
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- Befehle (rein)

def build_git_clone_cmd(target: str | Path, repo_url: str = REPO_URL) -> list[str]:
    """Voller Clone (Fallback, wenn ein Fetch per SHA nicht geht)."""
    return ["git", "clone", repo_url, str(target)]


def build_git_init_cmd() -> list[str]:
    return ["git", "init"]


def build_git_remote_add_cmd(repo_url: str) -> list[str]:
    return ["git", "remote", "add", "origin", repo_url]


def build_git_fetch_cmd(ref: str, shallow: bool = True) -> list[str]:
    """fetch eines Commits (SHA) oder Branches; flach, solange nicht anders verlangt."""
    return ["git", "fetch", *(["--depth", "1"] if shallow else []), "origin", ref]


def build_git_fetch_all_cmd() -> list[str]:
    return ["git", "fetch", "origin"]


def build_git_checkout_detach_cmd(ref: str) -> list[str]:
    return ["git", "checkout", "--detach", ref]


def build_git_checkout_pyproject_cmd() -> list[str]:
    return ["git", "checkout", "--", "pyproject.toml"]


def build_git_show_head_pyproject_cmd() -> list[str]:
    return ["git", "show", "HEAD:pyproject.toml"]


def build_git_head_cmd() -> list[str]:
    return ["git", "rev-parse", "--short", "HEAD"]


def build_git_head_full_cmd() -> list[str]:
    return ["git", "rev-parse", "HEAD"]


def build_git_has_commit_cmd(commit: str) -> list[str]:
    return ["git", "cat-file", "-e", f"{commit}^{{commit}}"]


def build_git_is_ancestor_cmd(ancestor: str, descendant: str) -> list[str]:
    return ["git", "merge-base", "--is-ancestor", ancestor, descendant]


def build_git_is_shallow_cmd() -> list[str]:
    return ["git", "rev-parse", "--is-shallow-repository"]


def build_git_branch_cmd() -> list[str]:
    return ["git", "branch", "--show-current"]


def build_venv_cmd(base_python: str, repo: str | Path) -> list[str]:
    return [base_python, "-m", "venv", str(Path(repo) / ".venv")]


def build_pip_upgrade_cmd(repo: str | Path) -> list[str]:
    return [str(runner.tool_python(repo)), "-m", "pip", "install", "--upgrade", "pip"]


def build_pip_install_cmd(repo: str | Path) -> list[str]:
    return [str(runner.tool_python(repo)), "-m", "pip", "install", "."]


# --------------------------------------------------------------------------- ibflex-Fix

def apply_ibflex_fix_text(text: str) -> tuple[str, str]:
    """Gibt (neuer Text, Status) zurück. Status: fixed | already | missing."""
    if IBFLEX_OLD_RE.search(text):
        return IBFLEX_OLD_RE.sub(lambda m: '"ibflex2 @ ' + m.group(1) + '"', text), "fixed"
    if IBFLEX_NEW_RE.search(text):
        return text, "already"
    return text, "missing"


def revert_ibflex_fix_text(text: str) -> str:
    return IBFLEX_NEW_RE.sub(lambda m: IBFLEX_OLD, text)


def apply_ibflex_fix(pyproject: Path, log: Log) -> bool:
    """Wendet den Fix an, falls nötig. True, wenn die Datei geändert wurde."""
    try:
        with open(pyproject, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except OSError as exc:
        log(f"Warnung: pyproject.toml nicht lesbar ({exc}).")
        return False
    new, status = apply_ibflex_fix_text(text)
    if status == "already":
        log("ibflex-Fix: nicht nötig (Abhängigkeit zeigt bereits auf ibflex2, Upstream korrigiert).")
        return False
    if status == "missing":
        log("ibflex-Fix: Abhängigkeitszeile für ibflex nicht gefunden, nichts geändert.")
        return False
    with open(pyproject, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    log("ibflex-Fix angewendet: ibflex -> ibflex2 in pyproject.toml.")
    return True


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def classify_pyproject(working: str, head: str) -> str:
    """unchanged | fix_only | other (Vergleich der Arbeitskopie mit HEAD)."""
    w, h = _norm(working), _norm(head)
    if w == h:
        return "unchanged"
    if revert_ibflex_fix_text(w) == h:
        return "fix_only"
    return "other"


# --------------------------------------------------------------------------- Installationsart / Version

def detect_install_kind(repo: str | Path) -> str:
    repo = Path(repo)
    if (repo / ".git").exists():
        return "git"
    if (repo / MARKER_NAME).is_file():
        return "zip"
    return "unknown"


def read_marker(repo: str | Path) -> dict:
    try:
        return json.loads((Path(repo) / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_marker(repo: str | Path, **extra) -> None:
    data = {"kind": "zip", "updated": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra}
    (Path(repo) / MARKER_NAME).write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_pyproject_version(repo: str | Path) -> str | None:
    try:
        text = (Path(repo) / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def installed_version(repo: str | Path, deps: Deps | None = None) -> str | None:
    deps = deps or Deps()
    repo = Path(repo)
    if not repo.is_dir():
        return None
    kind = detect_install_kind(repo)
    if kind == "git" and deps.which("git"):
        code, out = deps.run_capture(build_git_head_cmd(), repo)
        commit = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else None
        if commit:
            ver = read_pyproject_version(repo)
            return f"{commit} (Version {ver})" if ver else commit
    ver = read_pyproject_version(repo)
    if kind == "zip":
        when = read_marker(repo).get("updated")
        parts = [p for p in (ver and f"Version {ver}", when and f"Stand {when}") if p]
        return ", ".join(parts) or None
    return ver


def describe(repo: str | Path, source: str, deps: Deps | None = None) -> dict:
    return {
        "tool_repo": str(repo),
        "tool_source": source,  # settings | env | home | app
        "install_kind": detect_install_kind(repo),  # git | zip | unknown
        "version": installed_version(repo, deps),
        **version_status(repo, deps),
    }


# --------------------------------------------------------------------------- Pin (getestete Version)

def load_pin(path: str | Path | None = None) -> dict:
    """Liest app/tool_version.json (repo, commit, tested_on, note)."""
    p = Path(path) if path else PIN_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"Die Versionsdatei {p.name} ist nicht lesbar: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("repo"), str) or not data["repo"]:
        raise SetupError(f"{p.name}: Eintrag 'repo' fehlt.")
    commit = data.get("commit")
    if not isinstance(commit, str) or not SHA_RE.match(commit):
        raise SetupError(f"{p.name}: Eintrag 'commit' muss ein vollständiger SHA-1 (40 Hex-Zeichen) sein.")
    return data


def _pin(deps: Deps) -> dict:
    return deps.pin if deps.pin is not None else load_pin()


def _repo_url(deps: Deps, pin: dict) -> str:
    return deps.repo_url or pin.get("repo") or REPO_URL


def zip_url_for(deps: Deps, pin: dict, ref: str) -> str:
    """ref: 'pinned' (Pin-Commit) oder 'latest' (main)."""
    if deps.zip_url:
        return deps.zip_url
    m = re.match(r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", pin.get("repo", ""))
    slug = m.group(1) if m else DEFAULT_SLUG
    tail = "refs/heads/main" if ref == "latest" else pin["commit"]
    return f"https://codeload.github.com/{slug}/zip/{tail}"


def _short(commit: str | None) -> str | None:
    return commit[:7] if commit else None


def _same_commit(a: str | None, b: str | None) -> bool:
    if not a or not b or min(len(a), len(b)) < 7:
        return False
    return a.startswith(b) or b.startswith(a)


STATE_TEXT = {
    "tested": "getestete Version",
    "older": "älter als getestet – Aktualisieren empfohlen",
    "older_diverged": "älter als getestet oder abweichend – Aktualisieren empfohlen",
    "older_pinned_zip": "ältere getestete Version – Aktualisieren empfohlen",
    "newer": "neuer als getestet (ungetestet)",
    "unknown": "abweichend/unbekannt",
}


def git_head_full(deps: Deps, repo: Path) -> str | None:
    code, out = deps.run_capture(build_git_head_full_cmd(), repo)
    line = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""
    return line if re.fullmatch(r"[0-9a-f]{7,40}", line) else None


def git_is_shallow(deps: Deps, repo: Path) -> bool:
    code, out = deps.run_capture(build_git_is_shallow_cmd(), repo)
    return code == 0 and out.strip().lower() == "true"


def git_relation(deps: Deps, repo: Path, pin_commit: str) -> tuple[str, str | None]:
    """Vergleicht HEAD mit dem Pin ohne Netzwerk. Gibt (tested|older|newer|unknown, HEAD) zurück.

    Ist der Pin-Commit lokal vorhanden, entscheidet merge-base --is-ancestor (beide Richtungen).
    Fehlt er lokal, kann HEAD ihn nicht enthalten: Bei einem vollständigen Repo gilt das als "älter"
    (ein neuerer HEAD hätte den Pin in seiner Historie). Bei einem flachen Repo ist die Historie
    abgeschnitten, dort lässt sich nichts sagen: "unknown".
    """
    head = git_head_full(deps, repo)
    if not head:
        return "unknown", None
    if _same_commit(head, pin_commit):
        return "tested", head
    if deps.run_capture(build_git_has_commit_cmd(pin_commit), repo)[0] == 0:
        if deps.run_capture(build_git_is_ancestor_cmd(pin_commit, head), repo)[0] == 0:
            return "newer", head
        if deps.run_capture(build_git_is_ancestor_cmd(head, pin_commit), repo)[0] == 0:
            return "older", head
        return "unknown", head  # abweichender Zweig
    return ("unknown" if git_is_shallow(deps, repo) else "older"), head


def _pin_is_local(deps: Deps, repo: Path, pin_commit: str) -> bool:
    return deps.run_capture(build_git_has_commit_cmd(pin_commit), repo)[0] == 0


def version_status(repo: str | Path, deps: Deps | None = None) -> dict:
    """Installierter Commit, getesteter Commit und Zustand. Kein Netzwerk."""
    deps = deps or Deps()
    repo = Path(repo)
    out = {"installed_commit": None, "installed_commit_full": None, "tested_commit": None,
           "tested_commit_full": None, "tested_on": None, "version_state": "unknown",
           "version_state_text": STATE_TEXT["unknown"]}
    try:
        pin = _pin(deps)
    except SetupError:
        return out
    out.update(tested_commit=_short(pin["commit"]), tested_commit_full=pin["commit"], tested_on=pin.get("tested_on"))
    state, head, text = "unknown", None, None
    kind = detect_install_kind(repo) if repo.is_dir() else "unknown"
    if kind == "git" and deps.which("git"):
        state, head = git_relation(deps, repo, pin["commit"])
        if state == "older" and not _pin_is_local(deps, repo, pin["commit"]):
            text = STATE_TEXT["older_diverged"]  # offline nicht von divergierter Historie unterscheidbar
    elif kind == "zip":
        marker = read_marker(repo)
        head = marker.get("commit") or None
        if marker.get("ref") == "latest":
            state = "newer"
        elif head:
            if _same_commit(head, pin["commit"]):
                state = "tested"
            elif marker.get("ref") == "pinned":
                # Pins werden nur angehoben: ein anderer Pin-Commit stammt von einem früheren Pin.
                state, text = "older", STATE_TEXT["older_pinned_zip"]
    out.update(installed_commit=_short(head), installed_commit_full=head,
               version_state=state, version_state_text=text or STATE_TEXT[state])
    return out


# --------------------------------------------------------------------------- ZIP

def _member_target(dest: Path, rel: str) -> Path:
    """Löst einen Archivpfad relativ zu dest auf und stellt sicher, dass er innerhalb liegt."""
    if not rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel) or "\x00" in rel:
        raise SetupError(f"Unsicherer Pfad im ZIP-Archiv: {rel!r}")
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if ".." in parts:
        raise SetupError(f"Unsicherer Pfad im ZIP-Archiv: {rel!r}")
    target = (dest / Path(*parts)).resolve()
    root = dest.resolve()
    if target != root and root not in target.parents:
        raise SetupError(f"Pfad ausserhalb des Zielordners im ZIP-Archiv: {rel!r}")
    return target


def safe_extract_zip(zip_path: Path, dest: Path, strip_top: bool = True) -> int:
    """Entpackt nach dest (Zip-Slip-sicher, ohne Symlinks). Gibt die Zahl der Dateien zurück."""
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    unpacked = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if not infos:
            raise SetupError("Das ZIP-Archiv ist leer.")
        for i in infos:  # jeden Eintrag prüfen, bevor ein Top-Ordner abgestreift wird
            if i.filename.strip("/\\"):
                _member_target(dest, i.filename)
        tops = {PurePosixPath(i.filename.replace("\\", "/")).parts[0] for i in infos if i.filename.strip("/")}
        prefix = tops.pop() if strip_top and len(tops) == 1 else None
        for info in infos:
            name = info.filename.replace("\\", "/")
            if not name.strip("/"):
                continue
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SetupError(f"Symbolischer Link im ZIP-Archiv nicht erlaubt: {info.filename!r}")
            rel = name
            if prefix is not None:
                parts = PurePosixPath(name).parts
                if parts[0] != prefix:
                    raise SetupError(f"Unerwarteter Pfad im ZIP-Archiv: {info.filename!r}")
                rel = "/".join(parts[1:])
                if not rel:
                    continue
            target = _member_target(dest, rel)
            if info.is_dir() or name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            unpacked += info.file_size
            if unpacked > MAX_UNPACKED_BYTES:
                raise SetupError("ZIP-Archiv entpackt unerwartet gross, Abbruch.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def fetch_zip_source(deps: Deps, tmp: Path, log: Log, url: str) -> Path:
    """Lädt das ZIP in tmp, entpackt nach tmp/src und gibt den Quellordner zurück."""
    log(f"Lade ZIP: {url}")
    zip_path = tmp / "source.zip"
    try:
        deps.download(url, zip_path, log)
    except SetupError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SetupError(f"Download fehlgeschlagen: {exc}") from exc
    src = tmp / "src"
    try:
        n = safe_extract_zip(zip_path, src)
    except zipfile.BadZipFile as exc:
        raise SetupError(f"Ungültiges ZIP-Archiv: {exc}") from exc
    if not (src / "pyproject.toml").is_file():
        raise SetupError("Das ZIP enthält keine pyproject.toml, Abbruch.")
    log(f"Entpackt: {n} Dateien.")
    return src


def _tmp(deps: Deps, parent: Path):
    root = deps.tmp_root or parent
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=".setup-", dir=str(root))


def _is_protected(rel: str) -> bool:
    return PurePosixPath(rel).name == "config.toml" or rel in PROTECTED_FILES


def _list_source_files(src: Path) -> list[str]:
    out = []
    for entry in sorted(src.iterdir()):
        if entry.name in KEEP_DIRS:
            continue
        if entry.is_dir():
            out.extend(p.relative_to(src).as_posix() for p in sorted(entry.rglob("*")) if p.is_file())
        elif entry.is_file():
            out.append(entry.name)
    return out


def _stale_upstream_files(repo: Path, old_manifest, new_files: set[str]) -> list[str]:
    """*.py unter src/, die im alten Manifest standen und im neuen Quellcode fehlen."""
    stale = []
    for rel in old_manifest if isinstance(old_manifest, list) else []:
        if not isinstance(rel, str) or not rel.startswith("src/") or not rel.endswith(".py") or rel in new_files:
            continue
        try:
            target = _member_target(repo, rel)
        except SetupError:
            continue
        if target.is_file():
            stale.append(rel)
    return stale


def replace_zip_source(src: Path, repo: Path, log: Log) -> list[str]:
    """Gleicht neuen Quellcode Datei für Datei mit repo ab und gibt die Dateiliste (Manifest) zurück.

    Dateien aus dem neuen Quellcode überschreiben alte. Nur lokal vorhandene Dateien bleiben,
    ausser *.py unter src/, die im alten Marker-Manifest standen und upstream entfernt wurden.
    config.toml (überall) und data/security_identifiers.csv werden nie überschrieben, .venv und
    .git nie angefasst. Bei einem Fehler wird der Ausgangszustand aus einem Backup im Temp
    wiederhergestellt.
    """
    new_files = _list_source_files(src)
    stale = _stale_upstream_files(repo, read_marker(repo).get("files"), set(new_files))
    to_write = [r for r in new_files if not (_is_protected(r) and (repo / r).exists())]

    backup = Path(tempfile.mkdtemp(prefix="steuerauszug-backup-"))
    backed: list[str] = []      # Dateien, deren alter Stand im Backup liegt
    created: list[str] = []     # neu angelegte Dateien
    made_dirs: list[Path] = []  # neu angelegte Ordner
    try:
        for rel in to_write + stale:
            dst = repo / rel
            if dst.is_file():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / rel)
                backed.append(rel)
        for rel in to_write:
            dst = repo / rel
            missing = [d for d in reversed(dst.parents) if repo in d.parents and not d.exists()]
            dst.parent.mkdir(parents=True, exist_ok=True)
            made_dirs.extend(missing)
            if rel not in backed:
                created.append(rel)
            shutil.copy2(src / rel, dst)
        for rel in stale:
            (repo / rel).unlink()
            log(f"Entferntes Upstream-Modul gelöscht: {rel}")
    except Exception as exc:  # noqa: BLE001
        problems = []
        for rel in created:
            try:
                (repo / rel).unlink(missing_ok=True)
            except OSError as e:
                problems.append(f"{rel}: {e}")
        for rel in backed:
            try:
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup / rel, repo / rel)
            except OSError as e:
                problems.append(f"{rel}: {e}")
        for d in reversed(made_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        if problems:
            raise SetupError(
                f"Kopieren fehlgeschlagen ({exc}) und der Rollback ist NICHT vollständig gelungen. "
                f"Der alte Stand liegt im Backup-Ordner: {backup}. Probleme: " + "; ".join(problems[:5])) from exc
        shutil.rmtree(backup, ignore_errors=True)
        raise SetupError(f"Kopieren fehlgeschlagen ({exc}). Der Ausgangszustand wurde wiederhergestellt.") from exc
    shutil.rmtree(backup, ignore_errors=True)
    log("Quellcode abgeglichen (config.toml, data\\security_identifiers.csv, eigene Dateien und .venv unverändert).")
    return new_files


# --------------------------------------------------------------------------- Schritte

def _step(deps: Deps, cmd: list[str], cwd: Path, log: Log, what: str) -> None:
    log(f"> {what}")
    code = deps.run_streaming(cmd, cwd, log)
    if code != 0:
        raise SetupError(f"{what} fehlgeschlagen (Exit-Code {code}).")


def ensure_venv(deps: Deps, repo: Path, log: Log) -> None:
    if runner.tool_python(repo).exists():
        log("venv vorhanden.")
        return
    base = deps.base_python()
    log(f"Lege venv an mit {base}")
    _step(deps, build_venv_cmd(base, repo), repo, log, "venv anlegen")
    if not runner.tool_python(repo).exists():
        raise SetupError("venv wurde nicht angelegt (.venv\\Scripts\\python.exe fehlt).")


def pip_install(deps: Deps, repo: Path, log: Log, upgrade_pip: bool = True) -> None:
    if upgrade_pip:
        _step(deps, build_pip_upgrade_cmd(repo), repo, log, "pip aktualisieren")
    _step(deps, build_pip_install_cmd(repo), repo, log, "Tool installieren (pip install .)")


def finish_check(deps: Deps, repo: Path, log: Log) -> bool:
    info = deps.check_tool(repo)
    if info.get("ok"):
        log("Prüfung: Tool ist bereit.")
        return True
    log("FEHLER: Prüfung fehlgeschlagen: " + str(info.get("message", "")))
    return False


def _repo_has_content(repo: Path) -> bool:
    return repo.is_dir() and any(repo.iterdir())


def _rmtree_force(path: Path) -> None:
    def onerr(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerr)


def _install_git(repo: Path, deps: Deps, log: Log, pin: dict) -> None:
    """Holt genau den Pin-Commit in einen leeren Ordner."""
    commit, url = pin["commit"], _repo_url(deps, pin)
    repo.mkdir(parents=True, exist_ok=True)
    try:
        _step(deps, build_git_init_cmd(), repo, log, "git init")
        _step(deps, build_git_remote_add_cmd(url), repo, log, "git remote add origin")
        log(f"> git fetch --depth 1 origin {commit[:7]}")
        if deps.run_streaming(build_git_fetch_cmd(commit), repo, log) == 0:
            _step(deps, build_git_checkout_detach_cmd("FETCH_HEAD"), repo, log, "git checkout --detach FETCH_HEAD")
        else:
            log("Fetch per Commit-SHA nicht möglich, wechsle auf vollständigen Clone.")
            _rmtree_force(repo / ".git")  # nur das eben angelegte, leere Repo
            _step(deps, build_git_clone_cmd(repo, url), repo.parent, log, "git clone")
            _step(deps, build_git_checkout_detach_cmd(commit), repo, log, f"git checkout --detach {commit[:7]}")
    except SetupError:
        if not (repo / "pyproject.toml").is_file() and (repo / ".git").exists():
            _rmtree_force(repo / ".git")  # halbfertiges Repo entfernen, damit ein Neuversuch möglich bleibt
        raise
    log(f"Installiert: getestete Version {commit[:7]} (fester Commit, kein Branch).")


def run_install(settings: dict, log: Log, deps: Deps | None = None) -> bool:
    deps = deps or Deps()
    repo = Path(settings["tool_repo"])
    try:
        pin = _pin(deps)
        log(f"Installation nach {repo}")
        log(f"Getestete Version: {pin['commit'][:7]} (getestet am {pin.get('tested_on', '?')})")
        if (repo / "pyproject.toml").is_file():
            log("Quellcode bereits vorhanden, überspringe Download (setze nur venv und Pakete auf).")
        elif _repo_has_content(repo):
            raise SetupError(
                "Der Zielordner ist nicht leer und enthält kein Tool (keine pyproject.toml). "
                "Bitte einen leeren Ordner wählen oder den Ordner leeren.")
        elif deps.which("git"):
            repo.parent.mkdir(parents=True, exist_ok=True)
            _install_git(repo, deps, log, pin)
        else:
            log("git nicht gefunden, nutze ZIP-Download.")
            repo.parent.mkdir(parents=True, exist_ok=True)
            url = zip_url_for(deps, pin, "pinned")
            with _tmp(deps, repo.parent) as td:
                src = fetch_zip_source(deps, Path(td), log, url)
                if repo.exists():
                    repo.rmdir()  # nur leerer Ordner, sonst Fehler
                shutil.move(str(src), str(repo))
            write_marker(repo, source=url, commit=pin["commit"], ref="pinned", files=_list_source_files(repo))
        apply_ibflex_fix(repo / "pyproject.toml", log)
        ensure_venv(deps, repo, log)
        pip_install(deps, repo, log)
        return finish_check(deps, repo, log)
    except (SetupError, OSError) as exc:
        log(f"FEHLER: {exc}")
        return False


def _warn_newer(log: Log, head: str | None, commit: str, outcome: dict | None = None) -> None:
    if outcome is not None:
        outcome["not_downgraded"] = f"Nicht zurückgestuft: installiert {_short(head)}, getestet {commit[:7]}."
    log(f"WARNUNG: Der installierte Stand ({_short(head)}) ist neuer als die getestete Version ({commit[:7]}). "
        "Es wird nicht zurückgestuft (nur mit force).")


def _update_git(repo: Path, deps: Deps, log: Log, pin: dict, latest: bool, force: bool,
                outcome: dict | None = None) -> bool:
    """Bringt die Git-Installation auf den Pin (oder main). True, wenn der Quellcode gewechselt wurde.

    Es gibt weder clean noch reset --hard: Untracked Dateien bleiben, ein Checkout, der lokale
    Änderungen überschreiben würde, schlägt von selbst fehl.
    """
    if not deps.which("git"):
        raise SetupError("git wurde nicht gefunden, die Git-Installation kann nicht aktualisiert werden.")
    commit = pin["commit"]
    if not latest:
        rel, head = git_relation(deps, repo, commit)
        if rel == "tested":
            log(f"Das Tool ist bereits auf der getesteten Version ({commit[:7]}). Nichts zu tun.")
            return False
        if rel == "newer" and not force:
            _warn_newer(log, head, commit, outcome)
            return False
        if rel == "newer":
            log(f"Hinweis: force gesetzt, stufe von {_short(head)} auf die getestete Version {commit[:7]} zurück.")
    pyproject = repo / "pyproject.toml"
    code, head_py = deps.run_capture(build_git_show_head_pyproject_cmd(), repo)
    if code != 0:
        raise SetupError("Der Git-Stand von pyproject.toml ist nicht lesbar. Update abgebrochen: " + head_py.strip()[-200:])
    try:
        with open(pyproject, "r", encoding="utf-8", newline="") as fh:
            working = fh.read()
    except OSError as exc:
        raise SetupError(f"pyproject.toml nicht lesbar: {exc}") from exc
    state = classify_pyproject(working, head_py)
    if state == "other":
        raise SetupError(
            "pyproject.toml enthält eigene Änderungen ausser dem ibflex-Fix. "
            "Update abgebrochen, damit nichts verloren geht. Bitte die Änderungen selbst sichern oder zurücksetzen.")

    shallow = git_is_shallow(deps, repo)
    ref = "main" if latest else commit
    what = "main (neueste, ungetestete Version)" if latest else f"Commit {commit[:7]}"
    log(f"> git fetch {'--depth 1 ' if shallow else ''}origin {ref if latest else commit[:7]}")
    if deps.run_streaming(build_git_fetch_cmd(ref, shallow), repo, log) != 0:
        if latest:
            raise SetupError("git fetch origin main ist fehlgeschlagen. Das Tool wurde nicht verändert.")
        log("Fetch per Commit-SHA nicht möglich, hole alle Branches.")
        _step(deps, build_git_fetch_all_cmd(), repo, log, "git fetch origin")
    if not latest:
        rel, head = git_relation(deps, repo, commit)  # jetzt ist der Pin lokal vorhanden
        if rel == "newer" and not force:
            _warn_newer(log, head, commit, outcome)
            return False
    target = "FETCH_HEAD" if latest else commit

    code, branch = deps.run_capture(build_git_branch_cmd(), repo)
    branch = branch.strip().splitlines()[0] if code == 0 and branch.strip() else ""
    if state == "fix_only":
        log("pyproject.toml enthält nur den ibflex-Fix, setze die Datei für den Wechsel zurück.")
        _step(deps, build_git_checkout_pyproject_cmd(), repo, log, "git checkout -- pyproject.toml")
    try:
        _step(deps, build_git_checkout_detach_cmd(target), repo, log,
              "git checkout --detach " + ("origin/main (FETCH_HEAD)" if latest else commit[:7]))
    except SetupError:
        apply_ibflex_fix(pyproject, log)  # Ausgangszustand wiederherstellen
        raise SetupError(
            f"git checkout --detach ist fehlgeschlagen (lokale Änderungen oder Konflikte mit {what}?). "
            "Eigene Dateien wurden nicht angefasst.") from None
    log(f"Auf {what} gewechselt. Eigene (untracked) Dateien und lokale Branches blieben unverändert.")
    if branch:
        log(f"Hinweis: Das Tool steht jetzt auf einem festen Commit (detached HEAD), nicht mehr auf Branch '{branch}'. "
            f'Zurück zum Branch: git -C "{repo}" checkout {branch}')
    apply_ibflex_fix(pyproject, log)
    return True


def _update_zip(repo: Path, deps: Deps, log: Log, pin: dict, latest: bool) -> bool:
    if not (repo / "pyproject.toml").is_file():
        raise SetupError("Im Tool-Ordner fehlt pyproject.toml, Update abgebrochen.")
    marker = read_marker(repo)
    if not latest and marker.get("ref") != "latest" and _same_commit(marker.get("commit"), pin["commit"]):
        log(f"Das Tool ist bereits auf der getesteten Version ({pin['commit'][:7]}). Nichts zu tun.")
        return False
    url = zip_url_for(deps, pin, "latest" if latest else "pinned")
    with _tmp(deps, repo.parent) as td:
        src = fetch_zip_source(deps, Path(td), log, url)
        files = replace_zip_source(src, repo, log)
    write_marker(repo, source=url, files=files, commit=None if latest else pin["commit"],
                 ref="latest" if latest else "pinned")
    apply_ibflex_fix(repo / "pyproject.toml", log)
    return True


def run_update(settings: dict, log: Log, deps: Deps | None = None, target: str = "pinned",
               force: bool = False, outcome: dict | None = None) -> bool:
    """target: 'pinned' (getestete Version) oder 'latest' (main, ungetestet).
    outcome: optionales dict; bei unterlassenem Downgrade wird "not_downgraded" (Text) gesetzt."""
    deps = deps or Deps()
    repo = Path(settings["tool_repo"])
    latest = target == "latest"
    try:
        pin = _pin(deps)
        if not repo.is_dir() or not (repo / "pyproject.toml").is_file():
            raise SetupError("Kein installiertes Tool gefunden. Bitte zuerst installieren.")
        kind = detect_install_kind(repo)
        log(f"Aktualisiere {repo} (Installationsart: {kind})")
        if latest:
            log('ACHTUNG: Neueste Version von main (ungetestet). Bei Problemen führt "Zurück zur getesteten Version" '
                "zurück auf die getestete Version.")
        else:
            log(f"Ziel: getestete Version {pin['commit'][:7]} (getestet am {pin.get('tested_on', '?')})")
        if kind == "git":
            changed = _update_git(repo, deps, log, pin, latest, force, outcome)
        elif kind == "zip":
            changed = _update_zip(repo, deps, log, pin, latest)
        else:
            raise SetupError(
                "Installationsart unbekannt (weder Git-Klon noch von dieser App per ZIP installiert). "
                "Update abgebrochen, bitte von Hand aktualisieren.")
        if changed:
            ensure_venv(deps, repo, log)
            pip_install(deps, repo, log, upgrade_pip=False)
        return finish_check(deps, repo, log)
    except (SetupError, OSError) as exc:
        log(f"FEHLER: {exc}")
        return False


def job_install(settings: dict, deps: Deps | None = None):
    def run(job):
        return run_install(settings, job.log, deps)
    return run


def job_update(settings: dict, deps: Deps | None = None, target: str = "pinned", force: bool = False):
    def run(job):
        outcome: dict = {}
        ok = run_update(settings, job.log, deps, target=target, force=force, outcome=outcome)
        if outcome.get("not_downgraded"):
            job.results.append({"kind": "warning", "ok": True, "message": outcome["not_downgraded"]
                                + " Zum Zurücksetzen \"Zurück zur getesteten Version\" verwenden."})
        return ok
    return run
