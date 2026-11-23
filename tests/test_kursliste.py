"""Kursliste: Status je Jahr und UNIQUE-Bug-Behandlung (ohne Download)."""
import datetime as dt
from pathlib import Path

from app import kursliste, runner


def test_year_status_no_directory():
    assert kursliste.year_status(None, 2025)["state"] == "none"


def test_year_status_no_file(tmp_path):
    res = kursliste.year_status(tmp_path, 2025)
    assert res["state"] == "none" and res["xml_size"] is None and res["sqlite_size"] is None


def test_year_status_xml_only(tmp_path):
    (tmp_path / "kursliste_2025.xml").write_bytes(b"<x/>" * 10)
    res = kursliste.year_status(tmp_path, 2025)
    assert res["state"] == "xml"
    assert res["xml_size"] == 40
    assert res["date"] and dt.datetime.fromisoformat(res["date"])


def test_year_status_xml_and_sqlite(tmp_path):
    (tmp_path / "kursliste_2025.xml").write_bytes(b"<x/>")
    (tmp_path / "kursliste_2025.sqlite").write_bytes(b"SQLite format 3\x00")
    res = kursliste.year_status(tmp_path, 2025)
    assert res["state"] == "xml+sqlite"
    assert res["sqlite_size"] == 16


def test_year_status_empty_sqlite_counts_as_xml(tmp_path):
    (tmp_path / "kursliste_2025.xml").write_bytes(b"<x/>")
    (tmp_path / "kursliste_2025.sqlite").write_bytes(b"")
    assert kursliste.year_status(tmp_path, 2025)["state"] == "xml"


def test_year_status_sqlite_without_xml_is_none(tmp_path):
    (tmp_path / "kursliste_2025.sqlite").write_bytes(b"abc")
    assert kursliste.year_status(tmp_path, 2025)["state"] == "none"


def test_list_status_with_explicit_years(tmp_path):
    (tmp_path / "kursliste_2024.xml").write_bytes(b"<x/>")
    rows = kursliste.list_status(tmp_path, [2024, 2025])
    assert [r["year"] for r in rows] == [2024, 2025]
    assert [r["state"] for r in rows] == ["xml", "none"]


def test_year_range_starts_2020():
    years = kursliste.year_range(today=dt.date(2026, 11, 23))
    assert years[0] == 2020 and years[-1] == 2026


def _fake_download(directory, lines, code=1, create_xml=True, create_sqlite=True):
    def fake(cmd, cwd, log, redact=()):
        if create_xml:
            (directory / "kursliste_2025.xml").write_bytes(b"<kursliste/>")
        if create_sqlite:
            (directory / "kursliste_2025.sqlite").write_bytes(b"partial")
        for line in lines:
            log(line)
        return code
    return fake


def test_unique_bug_deletes_sqlite_and_year_counts_as_xml(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(
        tmp_path, ["Konvertierung ...", "sqlite3.IntegrityError: UNIQUE constraint failed: signs.kl_id"],
        code=1))
    logs = []
    ok = kursliste.download(tmp_path, 2025, logs.append)
    assert ok is True
    assert not (tmp_path / "kursliste_2025.sqlite").exists()
    assert kursliste.year_status(tmp_path, 2025)["state"] == "xml"
    assert any("Halbfertige SQLite-Datei gelöscht" in l for l in logs)
    assert any("Kursliste 2025 verfügbar (XML)." in l for l in logs)


def test_failed_to_convert_message_also_triggers_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(
        tmp_path, ["Failed to convert kursliste"], code=2))
    assert kursliste.download(tmp_path, 2025, lambda s: None) is True
    assert not (tmp_path / "kursliste_2025.sqlite").exists()


def test_success_keeps_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(tmp_path, ["ok"], code=0))
    logs = []
    assert kursliste.download(tmp_path, 2025, logs.append) is True
    assert (tmp_path / "kursliste_2025.sqlite").exists()
    assert any("XML+SQLite" in l for l in logs)


def test_no_files_after_download_is_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(
        tmp_path, ["Network down"], code=1, create_xml=False, create_sqlite=False))
    logs = []
    assert kursliste.download(tmp_path, 2025, logs.append) is False
    assert any(l.startswith("FEHLER: Kursliste 2025 nicht vorhanden") for l in logs)


def test_unique_error_without_xml_is_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(
        tmp_path, ["UNIQUE constraint failed: signs.kl_id"], code=1,
        create_xml=False, create_sqlite=True))
    assert kursliste.download(tmp_path, 2025, lambda s: None) is False


def test_output_has_convert_failure_detection():
    assert kursliste.output_has_convert_failure(["UNIQUE constraint failed: signs.kl_id"])
    assert kursliste.output_has_convert_failure(["Failed to convert"])
    assert not kursliste.output_has_convert_failure(["alles gut"])


def test_convert_failure_message_when_sqlite_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(
        tmp_path, ["UNIQUE constraint failed: signs.kl_id"], code=1))
    logs = []
    assert kursliste.download(tmp_path, 2025, logs.append) is True
    hint = [l for l in logs if l.startswith("Hinweis:")]
    assert len(hint) == 1
    assert "die unvollständige SQLite-Datei wurde gelöscht" in hint[0]
    assert "keine SQLite-Datei gelöscht" not in hint[0]


def test_convert_failure_message_when_no_sqlite_present(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(
        tmp_path, ["Failed to convert kursliste"], code=2, create_sqlite=False))
    logs = []
    assert kursliste.download(tmp_path, 2025, logs.append) is True
    hint = [l for l in logs if l.startswith("Hinweis:")]
    assert len(hint) == 1
    assert "es wurde keine SQLite-Datei gelöscht" in hint[0]
    assert "wurde gelöscht" not in hint[0]
    assert not any("Halbfertige SQLite-Datei gelöscht" in l for l in logs)


def test_convert_failure_when_sqlite_cannot_be_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: tmp_path)
    monkeypatch.setattr(runner, "run_streaming", _fake_download(
        tmp_path, ["UNIQUE constraint failed: signs.kl_id"], code=1))

    def locked(self, *a, **k):
        raise PermissionError("gesperrt")
    monkeypatch.setattr(Path, "unlink", locked)
    logs = []
    assert kursliste.download(tmp_path, 2025, logs.append) is True
    assert any("SQLite-Datei konnte nicht gelöscht werden" in l for l in logs)
    hint = [l for l in logs if l.startswith("Hinweis:")]
    assert "es wurde keine SQLite-Datei gelöscht" in hint[0]
