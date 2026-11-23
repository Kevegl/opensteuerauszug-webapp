import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
