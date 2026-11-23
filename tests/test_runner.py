"""Befehlsaufbau, Konto-ID, Dateinamen (kein Netzwerk, kein echter Tool-Aufruf)."""
import sys
from pathlib import Path

import pytest

from app import runner

REPO = Path("C:/fake/opensteuerauszug")


def test_steuerauszug_cmd_basics(tmp_path):
    xml_in = tmp_path / "in.xml"
    pdf = tmp_path / "out.pdf"
    xml_out = tmp_path / "out.xml"
    cmd = runner.build_steuerauszug_cmd(REPO, xml_in, 2025, pdf, xml_out)
    assert cmd[0] == str(runner.tool_python(REPO))
    assert cmd[1:4] == ["-m", "opensteuerauszug.steuerauszug", str(xml_in)]
    assert cmd[4:6] == ["--importer", "ibkr"]
    assert cmd[6:8] == ["--tax-year", "2025"]
    i = cmd.index("-o")
    assert cmd[i + 1] == str(pdf)
    j = cmd.index("--xml-output")
    assert cmd[j + 1] == str(xml_out)
    assert cmd[cmd.index("--log-level") + 1] == "INFO"


def test_steuerauszug_cmd_without_xml_output_has_no_flag(tmp_path):
    cmd = runner.build_steuerauszug_cmd(REPO, tmp_path / "in.xml", 2024, tmp_path / "o.pdf")
    assert "--xml-output" not in cmd
    assert "--tax-year" in cmd and cmd[cmd.index("--tax-year") + 1] == "2024"


def test_steuerauszug_cmd_optional_flags(tmp_path):
    kdir = tmp_path / "kl"
    cmd = runner.build_steuerauszug_cmd(
        REPO, "a.xml", 2025, "a.pdf", kursliste_dir=kdir,
        tax_calculation_level="x", payment_reconciliation=True, log_level="",
    )
    assert "--log-level" not in cmd
    assert cmd[cmd.index("--kursliste-dir") + 1] == str(kdir)
    assert cmd[cmd.index("--tax-calculation-level") + 1] == "x"
    assert "--payment-reconciliation" in cmd
    cmd2 = runner.build_steuerauszug_cmd(REPO, "a.xml", 2025, "a.pdf", payment_reconciliation=False)
    assert "--no-payment-reconciliation" in cmd2
    assert "--payment-reconciliation" not in cmd2


def test_steuerauszug_cmd_is_list_of_strings_without_shell(tmp_path):
    cmd = runner.build_steuerauszug_cmd(REPO, tmp_path / "a b.xml", 2025, tmp_path / "o.pdf")
    assert all(isinstance(c, str) for c in cmd)


def test_kursliste_download_cmd_convert_default():
    cmd = runner.build_kursliste_download_cmd(REPO, 2025)
    assert cmd == [str(runner.tool_python(REPO)), "-m", "opensteuerauszug.kursliste",
                   "download", "--year", "2025", "--convert"]


def test_kursliste_download_cmd_destination_no_convert(tmp_path):
    cmd = runner.build_kursliste_download_cmd(REPO, 2024, destination=tmp_path, convert=False)
    assert "--destination" in cmd and cmd[cmd.index("--destination") + 1] == str(tmp_path)
    assert cmd[-1] == "--no-convert"
    assert cmd[cmd.index("--year") + 1] == "2024"


def test_kursliste_dir_and_check_cmd_use_tool_venv():
    assert runner.build_kursliste_dir_cmd(REPO)[0] == str(runner.tool_python(REPO))
    assert runner.build_check_cmd(REPO)[0] == str(runner.tool_python(REPO))


def test_extract_account_id_basic(tmp_path):
    p = tmp_path / "f.xml"
    p.write_text('<FlexQueryResponse><AccountInformation accountId="U1234567" name="x"/>',
                 encoding="utf-8")
    assert runner.extract_account_id(p) == "U1234567"


def test_extract_account_id_attribute_order(tmp_path):
    p = tmp_path / "f.xml"
    p.write_text('<AccountInformation name="x" accountId="DU99" />', encoding="utf-8")
    assert runner.extract_account_id(p) == "DU99"


def test_extract_account_id_missing_or_unreadable(tmp_path):
    p = tmp_path / "f.xml"
    p.write_text("<FlexQueryResponse/>", encoding="utf-8")
    assert runner.extract_account_id(p) is None
    assert runner.extract_account_id(tmp_path / "nope.xml") is None


def test_safe_name_plain_and_traversal():
    assert runner.safe_name("Konto U1/2") == "Konto_U1_2"
    assert runner.safe_name("..\\x") == "x"
    assert runner.safe_name("../../etc/passwd") == "etc_passwd"
    assert runner.safe_name("..\\..\\windows\\system32") == "windows_system32"
    assert runner.safe_name("") == "datei"
    assert runner.safe_name("...") == "datei"


@pytest.mark.parametrize("raw", ["..\\x", "../x", "a/b\\c", "C:evil", "x\x00y", "..", "."])
def test_safe_name_never_contains_separators(raw):
    out = runner.safe_name(raw)
    assert "/" not in out and "\\" not in out and ":" not in out
    assert out not in ("", ".", "..")


def test_output_basename_uses_account(tmp_path):
    p = tmp_path / "f.xml"
    p.write_text('<AccountInformation accountId="U1234567"/>', encoding="utf-8")
    assert runner.output_basename(p, 2025) == "Steuerauszug_U1234567_2025"


def test_output_basename_fallback_to_stem(tmp_path):
    p = tmp_path / "flex 2025.xml"
    p.write_text("<x/>", encoding="utf-8")
    assert runner.output_basename(p, 2025) == "Steuerauszug_flex_2025_2025"


def test_output_basename_sanitizes_account(tmp_path):
    p = tmp_path / "f.xml"
    p.write_text('<AccountInformation accountId="../../evil"/>', encoding="utf-8")
    name = runner.output_basename(p, 2025)
    assert "/" not in name and "\\" not in name and ".." not in name.split("_")


def test_run_streaming_redacts_secret_and_returns_exit_code(tmp_path):
    lines = []
    code = runner.run_streaming(
        [sys.executable, "-c", "print('hallo'); print('geheim-123'); raise SystemExit(3)"],
        tmp_path, lines.append, redact=["geheim-123"],
    )
    assert code == 3
    assert "hallo" in lines
    assert not any("geheim-123" in l for l in lines)
    assert "***" in lines


def test_run_streaming_missing_executable_returns_minus_one(tmp_path):
    lines = []
    code = runner.run_streaming([str(tmp_path / "gibt_es_nicht.exe")], tmp_path, lines.append)
    assert code == -1
    assert lines and lines[0].startswith("FEHLER")


def test_check_tool_without_venv(tmp_path):
    info = runner.check_tool(tmp_path)
    assert info["repo_exists"] is True
    assert info["python_exists"] is False
    assert info["ok"] is False
    assert "venv" in info["message"]


def test_check_tool_missing_repo(tmp_path):
    info = runner.check_tool(tmp_path / "nicht-da")
    assert info["repo_exists"] is False and info["ok"] is False


def test_check_tool_ok_with_faked_capture(tmp_path, monkeypatch):
    py = runner.tool_python(tmp_path)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    monkeypatch.setattr(runner, "run_capture", lambda cmd, cwd, timeout=60: (0, "ok\n"))
    info = runner.check_tool(tmp_path)
    assert info["ok"] is True and info["message"] == "Bereit."


def test_tool_env_sets_utf8_and_no_color():
    env = runner.tool_env()
    assert env["PYTHONUTF8"] == "1" and env["NO_COLOR"] == "1"
