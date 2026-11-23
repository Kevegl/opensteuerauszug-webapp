# Steuerauszug-Assistent

Lokale Weboberfläche (Deutsch), die das Tool [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) automatisiert: Kursliste laden, IBKR-Flex-Daten holen oder hochladen, Schweizer Steuerauszug (PDF + XML) erzeugen. Das Tool selbst wird nur per Subprozess mit seinem eigenen venv aufgerufen und nie verändert.

## Einrichtung

1. Voraussetzung: Python 3.12 und das Tool-Repo mit installiertem venv, Standard `%USERPROFILE%\opensteuerauszug` (`.venv`, `config.toml`, `data\security_identifiers.csv`). Der Pfad ist in der Oberfläche (Abschnitt Flex Web Service, Feld Tool-Ordner) oder in `settings.json` änderbar; alternativ setzt die Umgebungsvariable `OPENSTEUER_TOOL_REPO` den Standard.
   Das Tool von <https://github.com/vroonhof/opensteuerauszug> muss vorher installiert sein (Repo nach `%USERPROFILE%\opensteuerauszug` klonen, eigenes venv anlegen). Bekannter Fix: in dessen `pyproject.toml` die Abhängigkeit `ibflex` durch `ibflex2` ersetzen.
2. Doppelklick auf `start.bat`. Das Skript legt `.venv` an, installiert `requirements.txt`, startet den Server auf `http://127.0.0.1:8765` und öffnet den Browser.

Der Server bindet ausschliesslich an 127.0.0.1. Persönliche Daten (`settings.json`, `work\`, `output\`) sind per `.gitignore` ausgeschlossen. Der Flex-Token liegt im Klartext in `settings.json`, wird aber nie geloggt und in der UI nur maskiert angezeigt.

## Flex-Query bei IBKR einmalig einrichten

1. Client Portal: Performance & Reports > Flex Queries > Activity Flex Query > neu erstellen.
2. Sections aktivieren (jeweils alle Felder auswählen): Account Information, Trades, Open Positions, Transfers, Corporate Actions, Cash Transactions, Cash Report, Securities Info.
3. Format: XML. Zeitraum: beliebig (wird beim automatischen Abruf durch das Steuerjahr 01.01. bis 31.12. überschrieben).
4. Speichern und die Query-ID notieren (eine Query pro Konto möglich).
5. Flex Web Service aktivieren: Flex Queries > Flex Web Service Configuration > aktivieren, Token erzeugen (Gültigkeit beachten) und kopieren.

## Bedienung

- **Status**: Das Badge oben zeigt, ob das Tool-venv gefunden wird und `opensteuerauszug` importierbar ist.
- **Kurslisten**: Tabelle der Jahre ab 2020 mit Status (nicht vorhanden / XML / XML + SQLite). "Herunterladen/Aktualisieren" startet einen Hintergrundjob mit Live-Log. Scheitert die SQLite-Konvertierung (bekannter Fehler `UNIQUE constraint failed: signs.kl_id`, z.B. 2025), wird die halbfertige `.sqlite` gelöscht und das Jahr als "XML" behandelt.
- **IBKR-Daten**: Flex-XML per Drag&Drop hochladen oder per Flex Web Service abrufen (Token und Query-IDs unter "Flex Web Service" speichern).
- **Steuerauszug erzeugen**: Jahr wählen (Standard Vorjahr), Dateien ankreuzen, "Steuerauszug erzeugen". Fehlt die Kursliste, wird sie zuerst geladen. Ergebnisse: `output\<Jahr>\Steuerauszug_<Konto>_<Jahr>.pdf/.xml`.
- **Alles automatisch**: Kursliste prüfen/laden, Flex-Abruf aller gespeicherten Queries, Steuerauszüge erzeugen.
- **Verlauf**: Alle erzeugten Dateien je Jahr mit Download-Links.

Es läuft immer nur ein Job gleichzeitig.

## Entwicklung

`.venv\Scripts\python.exe -m pytest tests`

Optionale Regressionstests mit echten Flex-XML-Dateien: Umgebungsvariable `OPENSTEUER_REAL_DATA_DIR` auf einen Ordner setzen; alle `*.xml` darin werden nur lesend getestet. Ohne Variable oder ohne Dateien werden diese Tests übersprungen.
