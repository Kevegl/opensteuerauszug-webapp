"""IBKR Flex Web Service: URL-Aufbau, Parsen, Polling (netzwerkfrei)."""
import traceback
from urllib.parse import parse_qs, urlparse

import pytest

from app import flex

TOKEN = "TOKEN-GEHEIM-9876"
SUCCESS = "<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF42</ReferenceCode><Url>https://x/y</Url></FlexStatementResponse>"
STATEMENT = '<FlexQueryResponse queryName="q"><FlexStatements count="1"><FlexStatement accountId="U1"><AccountInformation accountId="U1"/></FlexStatement></FlexStatements></FlexQueryResponse>'


def _pending():
    return '<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>'


def test_send_params_fd_td_for_full_year():
    p = flex.build_send_params(TOKEN, "123456", 2025)
    assert p == {"t": TOKEN, "q": "123456", "v": "3", "fd": "20250101", "td": "20251231"}


def test_send_params_without_year_has_no_dates():
    p = flex.build_send_params(TOKEN, "123456")
    assert "fd" not in p and "td" not in p


def test_send_url_contains_dates_and_base():
    url = flex.build_send_url(TOKEN, "123456", 2024)
    q = parse_qs(urlparse(url).query)
    assert url.startswith(flex.SEND_URL + "?")
    assert q["fd"] == ["20240101"] and q["td"] == ["20241231"]
    assert q["q"] == ["123456"] and q["t"] == [TOKEN]


def test_get_url_contains_reference_code():
    url = flex.build_get_url(TOKEN, "REF42")
    q = parse_qs(urlparse(url).query)
    assert url.startswith(flex.GET_URL + "?")
    assert q == {"q": ["REF42"], "t": [TOKEN], "v": ["3"]}


def test_parse_send_success():
    res = flex.parse_send_response(SUCCESS)
    assert res.reference_code == "REF42" and res.url == "https://x/y"


def test_parse_send_error_with_code():
    body = "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1012</ErrorCode><ErrorMessage>Token expired</ErrorMessage></FlexStatementResponse>"
    with pytest.raises(flex.FlexError) as ei:
        flex.parse_send_response(body)
    assert ei.value.code == "1012" and "Token expired" in str(ei.value)


def test_parse_send_success_without_reference_is_error():
    with pytest.raises(flex.FlexError):
        flex.parse_send_response("<FlexStatementResponse><Status>Success</Status></FlexStatementResponse>")


def test_parse_send_invalid_xml_is_error():
    with pytest.raises(flex.FlexError, match="kein XML"):
        flex.parse_send_response("<html>Service down")


def test_parse_statement_ready():
    state, body = flex.parse_statement_response(STATEMENT)
    assert state == "ready" and body.startswith("<FlexQueryResponse")


def test_parse_statement_pending_1019():
    assert flex.parse_statement_response(_pending()) == ("pending", None)


def test_parse_statement_error_other_code():
    body = "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode><ErrorMessage>Invalid request</ErrorMessage></FlexStatementResponse>"
    with pytest.raises(flex.FlexError) as ei:
        flex.parse_statement_response(body)
    assert ei.value.code == "1020"


def test_parse_statement_empty_and_unexpected():
    with pytest.raises(flex.FlexError):
        flex.parse_statement_response("   ")
    with pytest.raises(flex.FlexError, match="Unerwartete"):
        flex.parse_statement_response("<Foo/>")
    with pytest.raises(flex.FlexError):
        flex.parse_statement_response("<<<not xml")


def test_fetch_statement_polls_on_1019_and_returns_body(monkeypatch):
    calls, sleeps, logs = [], [], []
    responses = [SUCCESS, _pending(), _pending(), STATEMENT]

    def http_get(url):
        calls.append(url)
        return responses.pop(0)

    body = flex.fetch_statement(TOKEN, "123456", 2025, log=logs.append,
                                http_get=http_get, sleep=sleeps.append, delay=0.5)
    assert body == STATEMENT
    assert len(calls) == 4
    assert "fd=20250101" in calls[0] and "td=20251231" in calls[0]
    assert all("q=REF42" in u for u in calls[1:])
    assert sleeps == [0.5, 0.5]
    assert any("Versuch 2/20" in l for l in logs)


def test_fetch_statement_times_out_after_max_attempts():
    sleeps = []
    responses = [SUCCESS] + [_pending()] * 3

    def http_get(url):
        return responses.pop(0) if responses else _pending()

    with pytest.raises(flex.FlexError, match="Zeitüberschreitung"):
        flex.fetch_statement(TOKEN, "1", 2025, http_get=http_get, sleep=sleeps.append,
                             max_attempts=3, delay=1)
    assert len(sleeps) == 3


def test_fetch_statement_send_error_stops_immediately():
    calls = []
    err = "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1003</ErrorCode><ErrorMessage>Bad query</ErrorMessage></FlexStatementResponse>"

    def http_get(url):
        calls.append(url)
        return err

    with pytest.raises(flex.FlexError):
        flex.fetch_statement(TOKEN, "1", 2025, http_get=http_get, sleep=lambda s: None)
    assert len(calls) == 1


def test_network_error_message_does_not_contain_token():
    def http_get(url):
        raise ConnectionError(f"connection failed for url {url}")

    with pytest.raises(flex.FlexError) as ei:
        flex.fetch_statement(TOKEN, "1", 2025, http_get=http_get, sleep=lambda s: None)
    assert TOKEN not in str(ei.value)
    assert "Netzwerkfehler" in str(ei.value)


def test_logs_do_not_contain_token():
    logs = []
    responses = [SUCCESS, STATEMENT]

    flex.fetch_statement(TOKEN, "1", 2025, log=logs.append,
                         http_get=lambda url: responses.pop(0), sleep=lambda s: None)
    assert logs and not any(TOKEN in l for l in logs)


def test_traceback_of_network_error_does_not_contain_token():
    def http_get(url):
        raise ConnectionError(f"connection failed for url {url}")

    with pytest.raises(flex.FlexError) as ei:
        flex.fetch_statement(TOKEN, "1", 2025, http_get=http_get, sleep=lambda s: None)
    rendered = "".join(traceback.format_exception(ei.value))
    assert TOKEN not in rendered
