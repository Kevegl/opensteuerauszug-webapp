"""IBKR Flex Web Service. Parsen und URL-Aufbau sind netzwerkfrei testbar."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

SEND_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
GET_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
IN_PROGRESS_CODES = {"1019"}  # Statement-Erzeugung läuft noch


class FlexError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class SendResult:
    reference_code: str
    url: str | None = None


def build_send_params(token: str, query_id: str, year: int | None = None) -> dict[str, str]:
    params = {"t": token, "q": str(query_id), "v": "3"}
    if year is not None:
        params["fd"] = f"{int(year)}0101"
        params["td"] = f"{int(year)}1231"
    return params


def build_send_url(token: str, query_id: str, year: int | None = None, base: str = SEND_URL) -> str:
    return f"{base}?{urlencode(build_send_params(token, query_id, year))}"


def build_get_url(token: str, reference_code: str, base: str = GET_URL) -> str:
    return f"{base}?{urlencode({'q': reference_code, 't': token, 'v': '3'})}"


def _text(root: ET.Element, tag: str) -> str | None:
    el = root.find(tag)
    return el.text.strip() if el is not None and el.text else None


def parse_send_response(body: str) -> SendResult:
    try:
        root = ET.fromstring(body.strip())
    except ET.ParseError as exc:
        raise FlexError(f"Ungültige Antwort von IBKR (kein XML): {exc}") from None
    status = (_text(root, "Status") or "").lower()
    if status == "success":
        ref = _text(root, "ReferenceCode")
        if not ref:
            raise FlexError("IBKR-Antwort ohne ReferenceCode.")
        return SendResult(ref, _text(root, "Url"))
    code = _text(root, "ErrorCode")
    msg = _text(root, "ErrorMessage") or "Unbekannter Fehler"
    raise FlexError(f"IBKR-Fehler{' ' + code if code else ''}: {msg}", code)


def parse_statement_response(body: str) -> tuple[str, str | None]:
    """Gibt ('ready', body) oder ('pending', None) zurück bzw. wirft FlexError."""
    stripped = body.strip()
    if not stripped:
        raise FlexError("Leere Antwort von IBKR.")
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        raise FlexError(f"Ungültige Antwort von IBKR (kein XML): {exc}") from None
    if root.tag == "FlexStatementResponse":
        status = (_text(root, "Status") or "").lower()
        code = _text(root, "ErrorCode")
        if status == "warn" or code in IN_PROGRESS_CODES:
            return "pending", None
        if status == "success":
            raise FlexError("Statement noch nicht bereit (Success ohne Daten).", code)
        msg = _text(root, "ErrorMessage") or "Unbekannter Fehler"
        raise FlexError(f"IBKR-Fehler{' ' + code if code else ''}: {msg}", code)
    if root.tag == "FlexQueryResponse" or root.find(".//FlexStatement") is not None:
        return "ready", stripped
    raise FlexError(f"Unerwartete Antwort von IBKR (Wurzelelement {root.tag}).")


def _default_http_get(url: str) -> str:
    import requests
    resp = requests.get(url, timeout=60, headers={"User-Agent": "opensteuerauszug-ui"})
    resp.raise_for_status()
    return resp.text


def fetch_statement(
    token: str, query_id: str, year: int,
    log: Callable[[str], None] = lambda s: None,
    http_get: Callable[[str], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 20, delay: float = 5.0,
) -> str:
    """Holt ein Flex-Statement für das ganze Jahr. Wirft FlexError. Token wird nie geloggt."""
    http_get = http_get or _default_http_get

    def safe_get(url: str) -> str:
        try:
            return http_get(url)
        except FlexError:
            raise
        except Exception as exc:  # Netzwerkfehler, Token aus Meldung entfernen
            raise FlexError("Netzwerkfehler beim IBKR-Abruf: " + str(exc).replace(token, "***")) from None

    log(f"Fordere Flex-Query {query_id} für {year} an ...")
    send = parse_send_response(safe_get(build_send_url(token, query_id, year)))
    log("Referenzcode erhalten, warte auf Statement ...")
    for attempt in range(1, max_attempts + 1):
        state, body = parse_statement_response(safe_get(build_get_url(token, send.reference_code)))
        if state == "ready":
            log(f"Statement erhalten ({len(body or '')} Zeichen).")
            return body or ""
        log(f"Statement noch in Arbeit (Versuch {attempt}/{max_attempts}) ...")
        sleep(delay)
    raise FlexError("Zeitüberschreitung: IBKR hat das Statement nicht rechtzeitig bereitgestellt.")
