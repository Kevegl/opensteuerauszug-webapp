"""Update der eigenständigen Programmversion (gebautes Programm).

Schritt 1: nur Anzeige. Die Karte zeigt Version und Link zu den Releases; eigentliche Updates
(Prüfen, Herunterladen, Austauschen) folgen in Schritt 2. Dieses Modul ist die Andockstelle dafür:
``main.py`` ruft im Programm-Modus ausschließlich ``status``, ``check`` und ``job_apply`` hier auf.
"""
from __future__ import annotations

from . import paths


class NotSupported(Exception):
    """Funktion gibt es in dieser Programmversion noch nicht (deutsche Meldung)."""


def status() -> dict:
    """Lokaler Zustand ohne Netzwerk, gleiche Schlüssel wie selfupdate.status plus mode/releases_url."""
    return {
        "mode": "program", "is_git": False,
        "reason": "Programmversion: neue Versionen gibt es auf der Release-Seite.",
        "branch": None, "commit": None, "version": paths.version(), "dirty": False, "dirty_files": [],
        "upstream": None, "blocker": None, "restart_supported": False, "restart_required": False,
        "last_check": None, "releases_url": paths.RELEASES_URL,
    }


def check() -> dict:
    raise NotSupported("Die automatische Update-Suche gibt es in dieser Programmversion noch nicht. "
                       f"Neue Versionen: {paths.RELEASES_URL}")


def job_apply():
    raise NotSupported("Das automatische Update gibt es in dieser Programmversion noch nicht. "
                       f"Bitte die neue Version von {paths.RELEASES_URL} herunterladen.")
