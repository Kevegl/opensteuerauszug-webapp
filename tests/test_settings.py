"""Einstellungen: Maskierung, Token-Update, clear_token, Speichern (nur tmp_path)."""
import json

from app import settings as st


def test_mask_token_variants():
    assert st.mask_token("") == ""
    assert st.mask_token("abc") == "***"
    assert st.mask_token("abcd") == "****"
    assert st.mask_token("abcdefgh") == "****efgh"


def test_public_view_hides_token():
    s = {**st.DEFAULTS, "flex_token": "SECRET-TOKEN-1234", "flex_queries": []}
    view = st.public_view(s)
    assert "flex_token" not in view
    assert view["flex_token_masked"] == "*" * (len("SECRET-TOKEN-1234") - 4) + "1234"
    assert view["flex_token_set"] is True
    assert "SECRET" not in json.dumps(view)


def test_public_view_without_token():
    view = st.public_view({**st.DEFAULTS})
    assert view["flex_token_set"] is False and view["flex_token_masked"] == ""


def test_update_sets_new_token():
    new = st.update_settings({**st.DEFAULTS}, {"flex_token": "  NEU-TOKEN  "})
    assert new["flex_token"] == "NEU-TOKEN"


def test_empty_token_does_not_overwrite():
    cur = {**st.DEFAULTS, "flex_token": "ALT-TOKEN"}
    assert st.update_settings(cur, {"flex_token": ""})["flex_token"] == "ALT-TOKEN"
    assert st.update_settings(cur, {"flex_token": "   "})["flex_token"] == "ALT-TOKEN"


def test_masked_token_does_not_overwrite():
    cur = {**st.DEFAULTS, "flex_token": "ALT-TOKEN"}
    assert st.update_settings(cur, {"flex_token": "*******OKEN"})["flex_token"] == "ALT-TOKEN"


def test_clear_token():
    cur = {**st.DEFAULTS, "flex_token": "ALT-TOKEN"}
    new = st.update_settings(cur, {"clear_token": True})
    assert new["flex_token"] == "" and st.public_view(new)["flex_token_set"] is False


def test_update_does_not_mutate_current():
    cur = {**st.DEFAULTS, "flex_token": "ALT"}
    st.update_settings(cur, {"flex_token": "NEU"})
    assert cur["flex_token"] == "ALT"


def test_update_tool_repo_and_blank_ignored():
    cur = {**st.DEFAULTS}
    assert st.update_settings(cur, {"tool_repo": "  D:/tool "})["tool_repo"] == "D:/tool"
    assert st.update_settings(cur, {"tool_repo": "  "})["tool_repo"] == st.DEFAULTS["tool_repo"]


def test_queries_cleaned():
    new = st.update_settings({**st.DEFAULTS}, {"flex_queries": [
        {"name": " Konto A ", "query_id": " 111 "},
        {"name": "", "query_id": "222"},
        {"name": "leer", "query_id": "  "},
        "kein dict",
    ]})
    assert new["flex_queries"] == [
        {"name": "Konto A", "query_id": "111"},
        {"name": "Query 222", "query_id": "222"},
    ]


def test_load_missing_file_returns_defaults(tmp_path):
    s = st.load_settings(tmp_path / "fehlt.json")
    assert s["flex_token"] == "" and s["flex_queries"] == []


def test_load_broken_json_returns_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{kaputt", encoding="utf-8")
    assert st.load_settings(p)["flex_token"] == ""


def test_save_and_load_roundtrip_ignores_unknown_keys(tmp_path):
    p = tmp_path / "settings.json"
    st.save_settings({**st.DEFAULTS, "flex_token": "T-123456", "unbekannt": 1}, p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "unbekannt" not in data and data["flex_token"] == "T-123456"
    assert st.load_settings(p)["flex_token"] == "T-123456"
