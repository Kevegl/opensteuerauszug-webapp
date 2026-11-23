"""Job-Abläufe: Kursliste, Flex-Abruf, Steuerauszug, Alles automatisch."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from . import flex, kursliste, runner
from . import settings as st

Log = Callable[[str], None]
FILENAME_RE = re.compile(r"^[A-Za-z0-9._ -]+\.xml$")


def upload_files() -> list[Path]:
    if not st.UPLOAD_DIR.is_dir():
        return []
    return sorted(st.UPLOAD_DIR.glob("*.xml"), key=lambda p: p.stat().st_mtime, reverse=True)


def ensure_kursliste(settings: dict, year: int, log: Log) -> bool:
    repo = settings["tool_repo"]
    if kursliste.is_available(repo, year):
        log(f"Kursliste {year} ist vorhanden.")
        return True
    log(f"Kursliste {year} fehlt, starte Download.")
    return kursliste.download(repo, year, log)


def fetch_flex_all(settings: dict, year: int, log: Log, job_results: list[dict] | None = None) -> list[Path]:
    token = settings.get("flex_token", "")
    queries = settings.get("flex_queries", [])
    if not token:
        raise RuntimeError("Kein Flex-Token gespeichert.")
    if not queries:
        raise RuntimeError("Keine Flex-Query-IDs gespeichert.")
    st.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for q in queries:
        log(f"--- Flex-Abruf: {q['name']} ---")
        body = flex.fetch_statement(token, q["query_id"], year, log=log)
        name = f"flex_{year}_{runner.safe_name(q['name'])}.xml"
        path = st.UPLOAD_DIR / name
        path.write_text(body, encoding="utf-8")
        log(f"Gespeichert: {name}")
        saved.append(path)
    return saved


def generate(settings: dict, year: int, files: list[Path], log: Log, results: list[dict]) -> bool:
    repo = settings["tool_repo"]
    if not files:
        log("FEHLER: Keine XML-Dateien ausgewählt.")
        return False
    out_dir = st.OUTPUT_DIR / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for f in files:
        base = runner.output_basename(f, year)
        pdf = out_dir / f"{base}.pdf"
        xml = out_dir / f"{base}.xml"
        log(f"=== Erzeuge Steuerauszug für {f.name} -> {pdf.name} ===")
        cmd = runner.build_steuerauszug_cmd(repo, f, year, pdf, xml)
        code = runner.run_streaming(cmd, repo, log)
        if code == 0 and pdf.exists():
            log(f"OK: {pdf.name}")
            results.append({"year": year, "name": pdf.name, "ok": True})
            if xml.exists():
                results.append({"year": year, "name": xml.name, "ok": True})
        else:
            all_ok = False
            log(f"FEHLER: Lauf für {f.name} fehlgeschlagen (Exit-Code {code}).")
    return all_ok


def resolve_selected(names: list[str] | None) -> list[Path]:
    """Dateinamen aus der UI auf Uploads abbilden (nur Basisnamen, kein Pfad-Traversal)."""
    if not names:
        return upload_files()
    out = []
    for n in names:
        if Path(n).name != n or not FILENAME_RE.match(n):
            continue
        p = st.UPLOAD_DIR / n
        if p.is_file():
            out.append(p)
    return out


def job_kursliste(settings: dict, year: int):
    def run(job):
        return kursliste.download(settings["tool_repo"], year, job.log)
    return run


def job_flex(settings: dict, year: int):
    def run(job):
        try:
            fetch_flex_all(settings, year, job.log)
        except (flex.FlexError, RuntimeError) as exc:
            job.log(f"FEHLER: {exc}")
            return False
        return True
    return run


def job_generate(settings: dict, year: int, files: list[Path]):
    def run(job):
        if not ensure_kursliste(settings, year, job.log):
            job.log(f"FEHLER: Ohne Kursliste {year} kann kein Steuerauszug erzeugt werden.")
            return False
        return generate(settings, year, files, job.log, job.results)
    return run


def job_all(settings: dict, year: int):
    def run(job):
        job.log("Schritt 1/3: Kursliste prüfen")
        if not ensure_kursliste(settings, year, job.log):
            return False
        job.log("Schritt 2/3: Flex-Daten abrufen")
        try:
            files = fetch_flex_all(settings, year, job.log)
        except (flex.FlexError, RuntimeError) as exc:
            job.log(f"FEHLER: {exc}")
            return False
        job.log("Schritt 3/3: Steuerauszüge erzeugen")
        return generate(settings, year, files, job.log, job.results)
    return run


def history() -> list[dict]:
    items = []
    if st.OUTPUT_DIR.is_dir():
        for ydir in sorted(st.OUTPUT_DIR.iterdir(), reverse=True):
            if ydir.is_dir() and ydir.name.isdigit():
                files = [
                    {"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
                    for f in sorted(ydir.iterdir()) if f.is_file()
                ]
                items.append({"year": int(ydir.name), "files": files})
    return items
