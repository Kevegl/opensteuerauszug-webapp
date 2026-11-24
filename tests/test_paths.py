"""Datenordner je Plattform, Migration, Version."""
import os
from pathlib import Path

from app import paths, settings as st


def test_data_dir_windows_uses_localappdata(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "Local")}
    assert paths.data_dir("win32", env, tmp_path / "home") == tmp_path / "Local" / "Steuerauszug-Assistent"


def test_data_dir_windows_without_localappdata_falls_back_to_home(tmp_path):
    home = tmp_path / "home"
    assert paths.data_dir("win32", {}, home) == home / "AppData" / "Local" / "Steuerauszug-Assistent"


def test_data_dir_macos(tmp_path):
    assert paths.data_dir("darwin", {}, tmp_path) == tmp_path / "Library" / "Application Support" / "Steuerauszug-Assistent"


def test_data_dir_linux(tmp_path):
    assert paths.data_dir("linux", {}, tmp_path) == tmp_path / ".local" / "share" / "steuerauszug-assistent"


def test_env_overrides_every_platform(tmp_path):
    env = {"STEUERAUSZUG_DATA_DIR": str(tmp_path / "x"), "LOCALAPPDATA": "C:/ignored"}
    for platform in ("win32", "darwin", "linux"):
        assert paths.data_dir(platform, env, tmp_path) == tmp_path / "x"


def test_empty_env_is_ignored(tmp_path):
    assert paths.data_dir("linux", {"STEUERAUSZUG_DATA_DIR": "  "}, tmp_path).name == "steuerauszug-assistent"


def test_tests_never_use_real_profile(isolated_profile):
    assert paths.data_dir() == isolated_profile
    assert isolated_profile in st.SETTINGS_FILE.parents
    assert isolated_profile in st.UPLOAD_DIR.parents and isolated_profile in st.OUTPUT_DIR.parents


def test_version_file_matches_status():
    assert paths.version() == (paths.APP_DIR / "VERSION").read_text(encoding="utf-8").strip() == "1.5.0"


def test_port_env(monkeypatch):
    assert paths.port() == 8765
    monkeypatch.setenv("STEUERAUSZUG_PORT", "18999")
    assert paths.port() == 18999
    monkeypatch.setenv("STEUERAUSZUG_PORT", "kaputt")
    assert paths.port() == 8765


def _legacy(tmp_path):
    legacy = tmp_path / "project"
    (legacy / "work" / "uploads").mkdir(parents=True)
    (legacy / "output" / "2025").mkdir(parents=True)
    (legacy / "settings.json").write_text('{"flex_token": "alt"}', encoding="utf-8")
    (legacy / "work" / "uploads" / "a.xml").write_text("<a/>", encoding="utf-8")
    (legacy / "output" / "2025" / "x.pdf").write_bytes(b"pdf")
    return legacy


def test_migration_copies_once_and_keeps_original(tmp_path):
    legacy, data, logs = _legacy(tmp_path), tmp_path / "data", []
    assert paths.migrate_legacy(legacy, data, logs.append) is True
    assert (data / "settings.json").read_text(encoding="utf-8") == '{"flex_token": "alt"}'
    assert (data / "work" / "uploads" / "a.xml").read_text(encoding="utf-8") == "<a/>"
    assert (data / "output" / "2025" / "x.pdf").read_bytes() == b"pdf"
    assert (data / paths.MIGRATION_MARKER).is_file()
    # Original bleibt unverändert (kopiert, nicht verschoben)
    assert (legacy / "settings.json").is_file() and (legacy / "output" / "2025" / "x.pdf").is_file()
    assert len(logs) == 1 and "kopiert" in logs[0]
    # Zweiter Lauf: nichts mehr, auch wenn der Nutzer settings.json löscht
    (data / "settings.json").unlink()
    (legacy / "settings.json").write_text('{"flex_token": "neu"}', encoding="utf-8")
    assert paths.migrate_legacy(legacy, data, logs.append) is False
    assert not (data / "settings.json").exists()


def test_migration_skipped_when_data_has_settings(tmp_path):
    legacy, data = _legacy(tmp_path), tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text('{"flex_token": "behalten"}', encoding="utf-8")
    assert paths.migrate_legacy(legacy, data, lambda m: None) is False
    assert (data / "settings.json").read_text(encoding="utf-8") == '{"flex_token": "behalten"}'
    assert not (data / "work").exists() and not (data / paths.MIGRATION_MARKER).exists()


def test_migration_never_overwrites_existing_files(tmp_path):
    legacy, data = _legacy(tmp_path), tmp_path / "data"
    (data / "work" / "uploads").mkdir(parents=True)
    (data / "work" / "uploads" / "a.xml").write_text("<neu/>", encoding="utf-8")  # Datenordner ohne settings.json
    assert paths.migrate_legacy(legacy, data, lambda m: None) is True
    assert (data / "work" / "uploads" / "a.xml").read_text(encoding="utf-8") == "<neu/>"
    assert (data / "settings.json").is_file()


def test_migration_without_legacy_data_does_nothing(tmp_path):
    legacy = tmp_path / "empty"
    legacy.mkdir()
    data = tmp_path / "data"
    assert paths.migrate_legacy(legacy, data, lambda m: None) is False
    assert not data.exists()
    assert paths.migrate_legacy(None, data, lambda m: None) is False


def test_migration_same_folder_is_noop(tmp_path):
    legacy = _legacy(tmp_path)
    assert paths.migrate_legacy(legacy, legacy, lambda m: None) is False


def test_startup_creates_data_dir_and_migrates(tmp_path, monkeypatch, isolated_profile):
    monkeypatch.setattr(paths, "LEGACY_DIR", _legacy(tmp_path))
    assert paths.startup(log=lambda m: None) == isolated_profile
    assert (isolated_profile / "settings.json").is_file()


def test_save_settings_creates_missing_data_dir(tmp_path):
    target = tmp_path / "neu" / "settings.json"
    st.save_settings({"flex_queries": []}, target)
    assert target.is_file()
