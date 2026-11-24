"""Tool-Modus des Programms: führt opensteuerauszug im selben (gebauten) Programm aus.

Im gebauten Programm gibt es keine Tool-venv. Tool-Aufrufe starten das Programm selbst mit
``--run-tool <modul> <argumente>`` bzw. ``--tool-kursliste-dir``. Erlaubt ist nur eine feste Liste von
Modulen, nie beliebiger Code. Nur Standardbibliothek, damit der Start schnell bleibt und nichts anderes
importiert wird, bevor der Modus feststeht.
"""
from __future__ import annotations

import os
import runpy
import sys

RUN_TOOL_FLAG = "--run-tool"
KURSLISTE_DIR_FLAG = "--tool-kursliste-dir"
STEUERAUSZUG_MODULE = "opensteuerauszug.steuerauszug"
KURSLISTE_MODULE = "opensteuerauszug.kursliste"
ALLOWED_MODULES = frozenset({STEUERAUSZUG_MODULE, KURSLISTE_MODULE})


def is_tool_invocation(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[1] in (RUN_TOOL_FLAG, KURSLISTE_DIR_FLAG)


def _ensure_streams() -> None:
    """Fenster-Programm ohne Konsole: fehlende Standardströme durch devnull ersetzen."""
    for name in ("stdin", "stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "r" if name == "stdin" else "w", encoding="utf-8"))  # noqa: SIM115
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def run_module(module: str, args: list[str]) -> int:
    """Führt ein erlaubtes Tool-Modul wie ``python -m <modul> <args>`` aus und gibt den Exit-Code zurück."""
    if module not in ALLOWED_MODULES:
        print(f"Modul nicht erlaubt: {module}", file=sys.stderr)
        return 2
    sys.argv = [module, *args]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def print_kursliste_dir() -> int:
    from opensteuerauszug.config.paths import resolve_kursliste_dir
    print(resolve_kursliste_dir())
    return 0


def main(argv: list[str] | None = None) -> int:
    """Einstieg für ``--run-tool`` und ``--tool-kursliste-dir`` (argv wie sys.argv, also mit Programmname)."""
    argv = list(sys.argv if argv is None else argv)
    _ensure_streams()
    if len(argv) >= 2 and argv[1] == KURSLISTE_DIR_FLAG:
        return print_kursliste_dir()
    if len(argv) >= 3 and argv[1] == RUN_TOOL_FLAG:
        return run_module(argv[2], argv[3:])
    print(f"Aufruf: {RUN_TOOL_FLAG} <modul> [argumente] | {KURSLISTE_DIR_FLAG}", file=sys.stderr)
    return 2
