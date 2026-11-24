import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Vor jedem Import von app.*: Datenordner in ein Temp-Verzeichnis legen, damit Tests nie das echte
# Benutzerprofil (%LOCALAPPDATA%, ~/Library/..., ~/.local/share) berühren.
_SESSION_DATA = tempfile.mkdtemp(prefix="steuerauszug-tests-")
atexit.register(shutil.rmtree, _SESSION_DATA, ignore_errors=True)
os.environ["STEUERAUSZUG_DATA_DIR"] = str(Path(_SESSION_DATA) / "data")
os.environ.pop("STEUERAUSZUG_BUNDLED_TOOL", None)
os.environ.pop("STEUERAUSZUG_PORT", None)
# Nie das echte GitHub ansprechen: Update-Suche zeigt auf einen Port ohne Server (Tests ersetzen das bei Bedarf)
os.environ["STEUERAUSZUG_UPDATE_API"] = "http://127.0.0.1:9"
os.environ.pop("STEUERAUSZUG_UPDATE_REPO", None)
os.environ.pop("STEUERAUSZUG_DELEGATED", None)

import pytest  # noqa: E402

# Kurztest und Update-End-to-End-Test des gebauten Programms sind eigene Skripte, keine pytest-Tests
collect_ignore_glob = ["smoke_*.py", "update_e2e.py"]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths, settings as _st  # noqa: E402

assert Path(_SESSION_DATA).resolve() in _st.SETTINGS_FILE.resolve().parents, "Datenordner der Tests nicht isoliert"


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """Jeder Test bekommt einen eigenen Datenordner; die Migration findet keinen Projektordner."""
    data = tmp_path / "_data"
    monkeypatch.setenv("STEUERAUSZUG_DATA_DIR", str(data))
    monkeypatch.delenv("STEUERAUSZUG_BUNDLED_TOOL", raising=False)
    monkeypatch.setattr(paths, "LEGACY_DIR", None)
    monkeypatch.setattr(_st, "DATA_DIR", data)
    monkeypatch.setattr(_st, "SETTINGS_FILE", data / "settings.json")
    monkeypatch.setattr(_st, "UPLOAD_DIR", data / "work" / "uploads")
    monkeypatch.setattr(_st, "OUTPUT_DIR", data / "output")
    # Nie ein echtes Programm/Explorer öffnen
    from app import main
    monkeypatch.setattr(main, "open_folder", lambda p: (_ for _ in ()).throw(AssertionError("open_folder ungemockt")))
    yield data


@pytest.fixture
def tmp_settings_file(tmp_path):
    return tmp_path / "settings.json"


@pytest.fixture
def sample_flex_xml(tmp_path):
    p = tmp_path / "flex.xml"
    p.write_text(
        '<FlexQueryResponse><FlexStatements><FlexStatement accountId="U1234567">'
        '<AccountInformation accountId="U1234567" name="Test"/></FlexStatement>'
        "</FlexStatements></FlexQueryResponse>",
        encoding="utf-8",
    )
    return p
