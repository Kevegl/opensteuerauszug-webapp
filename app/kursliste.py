"""Kursliste: Status je Jahr und Download (über das Tool)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

from . import runner

UNIQUE_BUG = "UNIQUE constraint failed"


def year_status(directory: Path | None, year: int) -> dict:
    """Reine Dateisystem-Abfrage. state: none | xml | xml+sqlite."""
    res = {"year": year, "state": "none", "xml_size": None, "sqlite_size": None, "date": None}
    if directory is None:
        return res
    xml = Path(directory) / f"kursliste_{year}.xml"
    sq = Path(directory) / f"kursliste_{year}.sqlite"
    if xml.is_file():
        st = xml.stat()
        res["state"] = "xml"
        res["xml_size"] = st.st_size
        res["date"] = dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")
        if sq.is_file() and sq.stat().st_size > 0:
            res["state"] = "xml+sqlite"
            res["sqlite_size"] = sq.stat().st_size
    return res


def year_range(first: int = 2020, today: dt.date | None = None) -> list[int]:
    today = today or dt.date.today()
    return list(range(first, today.year + 1))


def list_status(directory: Path | None, years: list[int] | None = None) -> list[dict]:
    return [year_status(directory, y) for y in (years or year_range())]


def resolve_dir(repo: str | Path) -> Path | None:
    code, out = runner.run_capture(runner.build_kursliste_dir_cmd(repo), repo, timeout=30)
    if code != 0:
        return None
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return Path(lines[-1]) if lines else None


def output_has_convert_failure(output_lines: list[str]) -> bool:
    text = "\n".join(output_lines)
    return "Failed to convert" in text or UNIQUE_BUG in text


def download(repo: str | Path, year: int, log: Callable[[str], None]) -> bool:
    """Lädt die Kursliste. True, wenn mindestens das XML vorhanden ist."""
    lines: list[str] = []

    def collect(line: str) -> None:
        lines.append(line)
        log(line)

    log(f"Lade Kursliste {year} herunter ...")
    code = runner.run_streaming(runner.build_kursliste_download_cmd(repo, year), repo, collect)
    directory = resolve_dir(repo)
    status = year_status(directory, year)

    if status["state"] != "none" and directory is not None and output_has_convert_failure(lines):
        # Bekannter Bug: SQLite-Konvertierung scheitert, das XML ist aber korrekt.
        sq = Path(directory) / f"kursliste_{year}.sqlite"
        deleted = False
        if sq.exists():
            try:
                sq.unlink()
                deleted = True
                log("Halbfertige SQLite-Datei gelöscht.")
            except OSError as exc:
                log(f"Warnung: SQLite-Datei konnte nicht gelöscht werden: {exc}")
        status = year_status(directory, year)
        suffix = ("die unvollständige SQLite-Datei wurde gelöscht"
                  if deleted else "es wurde keine SQLite-Datei gelöscht")
        log(f"Hinweis: SQLite-Konvertierung fehlgeschlagen (bekannter Fehler), {suffix}; "
            "das XML ist vorhanden und wird direkt verwendet.")
    if status["state"] == "none":
        log(f"FEHLER: Kursliste {year} nicht vorhanden (Exit-Code {code}).")
        return False
    label = "XML+SQLite" if status["state"] == "xml+sqlite" else "XML"
    log(f"Kursliste {year} verfügbar ({label}).")
    return True


def is_available(repo: str | Path, year: int) -> bool:
    return year_status(resolve_dir(repo), year)["state"] != "none"
