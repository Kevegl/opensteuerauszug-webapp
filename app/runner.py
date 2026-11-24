"""Befehlsaufbau und Subprozess-Aufrufe für das Tool opensteuerauszug.

Das Tool wird nie in die App importiert, sondern immer als Subprozess gestartet:
  * venv-Modus (Python-Variante): über das Python seiner eigenen Tool-venv,
  * Bundled-Modus (gebautes Programm): über das Programm selbst (``--run-tool <modul>``, siehe toolrun.py).
Die Befehlspräfixe liefern ausschließlich ``tool_prefix`` und ``tool_dir_prefix``.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

from . import paths, toolrun
from .platform_utils import venv_python, venv_python_hint

BUNDLED_ENV = "STEUERAUSZUG_BUNDLED_TOOL"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ACCOUNT_RE = re.compile(r"<AccountInformation\b[^>]*?\baccountId=\"([^\"]+)\"")
SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def tool_python(repo: str | Path) -> Path:
    return venv_python(Path(repo) / ".venv")


def bundled_mode() -> bool:
    """True, wenn das Tool im Programm mitgeliefert ist (gebaut oder per STEUERAUSZUG_BUNDLED_TOOL=1)."""
    return paths.is_frozen() or os.environ.get(BUNDLED_ENV, "").strip() == "1"


def _self_prefix() -> list[str]:
    """Befehl, der das Programm selbst startet (vor den Tool-Optionen)."""
    if paths.is_frozen():
        return [sys.executable]
    # Entwicklung mit erzwungenem Bundled-Modus: die Startdatei des Projekts mit dem aktuellen Python
    return [sys.executable, str(paths.BASE_DIR / "app" / "launcher.py")]


def tool_prefix(repo: str | Path, module: str) -> list[str]:
    """Befehlspräfix zum Ausführen eines Tool-Moduls (wie ``python -m <modul>``)."""
    if bundled_mode():
        if module not in toolrun.ALLOWED_MODULES:
            raise ValueError(f"Tool-Modul nicht erlaubt: {module}")
        return [*_self_prefix(), toolrun.RUN_TOOL_FLAG, module]
    return [str(tool_python(repo)), "-m", module]


def tool_dir_prefix(repo: str | Path) -> list[str]:
    """Befehl, der den Kursliste-Ordner des Tools auf stdout ausgibt."""
    if bundled_mode():
        return [*_self_prefix(), toolrun.KURSLISTE_DIR_FLAG]
    code = "from opensteuerauszug.config.paths import resolve_kursliste_dir as r; print(r())"
    return [str(tool_python(repo)), "-c", code]


def build_steuerauszug_cmd(
    repo: str | Path,
    xml_in: str | Path,
    year: int,
    pdf_out: str | Path,
    xml_out: str | Path | None = None,
    log_level: str = "INFO",
    kursliste_dir: str | Path | None = None,
    tax_calculation_level: str | None = None,
    payment_reconciliation: bool | None = None,
) -> list[str]:
    cmd = [
        *tool_prefix(repo, toolrun.STEUERAUSZUG_MODULE),
        str(xml_in), "--importer", "ibkr", "--tax-year", str(int(year)),
        "-o", str(pdf_out),
    ]
    if xml_out:
        cmd += ["--xml-output", str(xml_out)]
    if log_level:
        cmd += ["--log-level", log_level]
    if kursliste_dir:
        cmd += ["--kursliste-dir", str(kursliste_dir)]
    if tax_calculation_level:
        cmd += ["--tax-calculation-level", tax_calculation_level]
    if payment_reconciliation is True:
        cmd += ["--payment-reconciliation"]
    elif payment_reconciliation is False:
        cmd += ["--no-payment-reconciliation"]
    return cmd


def build_kursliste_download_cmd(
    repo: str | Path, year: int, destination: str | Path | None = None, convert: bool = True
) -> list[str]:
    cmd = [*tool_prefix(repo, toolrun.KURSLISTE_MODULE), "download", "--year", str(int(year))]
    if destination:
        cmd += ["--destination", str(destination)]
    cmd += ["--convert" if convert else "--no-convert"]
    return cmd


def build_kursliste_dir_cmd(repo: str | Path) -> list[str]:
    return tool_dir_prefix(repo)


def build_check_cmd(repo: str | Path) -> list[str]:
    """Nur im venv-Modus; im Bundled-Modus prüft check_tool das Modul im selben Prozess."""
    return [str(tool_python(repo)), "-c", "import opensteuerauszug; print('ok')"]


def extract_account_id(xml_path: str | Path) -> str | None:
    """accountId aus AccountInformation lesen (nur Dateianfang, ohne Volltext-Parsing)."""
    try:
        with open(xml_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(2_000_000)
    except OSError:
        return None
    m = ACCOUNT_RE.search(text)
    if m:
        return m.group(1)
    m = re.search(r"<AccountInformation\b[^>]*>", text)
    if m:
        m2 = re.search(r"accountId=\"([^\"]+)\"", m.group(0))
        if m2:
            return m2.group(1)
    return None


def safe_name(name: str) -> str:
    return SAFE_RE.sub("_", name).strip("._") or "datei"


def output_basename(xml_path: str | Path, year: int) -> str:
    account = extract_account_id(xml_path) or safe_name(Path(xml_path).stem)
    return f"Steuerauszug_{safe_name(account)}_{int(year)}"


def clean_line(line: str) -> str:
    return ANSI_RE.sub("", line).rstrip("\r\n")


def tool_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["COLUMNS"] = "200"
    return env


def _workdir(cwd: str | Path) -> str:
    """Im Bundled-Modus ist der Tool-Ordner nur ein Arbeitsordner und wird bei Bedarf angelegt."""
    if bundled_mode():
        try:
            Path(cwd).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return str(cwd)


def run_streaming(
    cmd: Iterable[str],
    cwd: str | Path,
    log: Callable[[str], None],
    redact: Iterable[str] = (),
) -> int:
    """Startet den Befehl, reicht jede Ausgabezeile an log() weiter, gibt den Exit-Code zurück."""
    secrets = [s for s in redact if s]

    def emit(line: str) -> None:
        for s in secrets:
            line = line.replace(s, "***")
        log(line)

    try:
        proc = subprocess.Popen(
            list(cmd), cwd=_workdir(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=tool_env(), text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        emit(f"FEHLER: Prozess konnte nicht gestartet werden: {exc}")
        return -1
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = clean_line(raw)
        if line.strip():
            emit(line)
    return proc.wait()


def run_capture(cmd: Iterable[str], cwd: str | Path, timeout: int = 60) -> tuple[int, str]:
    try:
        res = subprocess.run(
            list(cmd), cwd=_workdir(cwd), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=tool_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def bundled_module_importable() -> bool:
    """True, wenn das Tool im laufenden Programm vorhanden ist (ohne es zu importieren)."""
    try:
        return importlib.util.find_spec(toolrun.STEUERAUSZUG_MODULE) is not None
    except (ImportError, ValueError):
        return False


def check_tool_bundled(repo: str | Path) -> dict:
    repo = Path(repo)
    ok = bundled_module_importable()
    info = {
        "repo": str(repo), "repo_exists": True, "python": sys.executable, "python_exists": True,
        "importable": ok, "bundled": True,
        "config_exists": (repo / "config.toml").exists(),
        "identifiers_exist": (repo / "data" / "security_identifiers.csv").exists(),
        "message": "Bereit (mitgeliefert)." if ok else
        "Das mitgelieferte Tool opensteuerauszug fehlt im Programm. Bitte das Programm neu herunterladen.",
    }
    info["ok"] = ok
    return info


def check_tool(repo: str | Path) -> dict:
    """Status des Tool-Setups."""
    if bundled_mode():
        return check_tool_bundled(repo)
    repo = Path(repo)
    py = tool_python(repo)
    info = {
        "repo": str(repo), "repo_exists": repo.is_dir(), "python": str(py),
        "python_exists": py.exists(), "importable": False,
        "config_exists": (repo / "config.toml").exists(),
        "identifiers_exist": (repo / "data" / "security_identifiers.csv").exists(),
        "message": "",
    }
    if not info["repo_exists"]:
        info["message"] = "Tool-Ordner nicht gefunden."
    elif not info["python_exists"]:
        info["message"] = f"Tool-venv ({venv_python_hint()}) nicht gefunden."
    else:
        code, out = run_capture(build_check_cmd(repo), repo)
        info["importable"] = code == 0 and "ok" in out
        info["message"] = "Bereit." if info["importable"] else "opensteuerauszug nicht importierbar: " + out.strip()[-300:]
    info["ok"] = info["importable"]
    return info
