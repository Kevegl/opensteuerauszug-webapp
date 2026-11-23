# Steuerauszug-Assistent

Lokale Weboberfläche (Deutsch), die das Tool [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) automatisiert: Kursliste laden, IBKR-Flex-Daten holen oder hochladen, Schweizer Steuerauszug (PDF + XML) erzeugen. Das Tool selbst wird nur per Subprozess mit seinem eigenen venv aufgerufen und nie verändert.

> **Haftungsausschluss:** Dieses Projekt ist ein privates Hilfswerkzeug und steht in keiner Verbindung zur Eidgenössischen Steuerverwaltung (ESTV), zu Interactive Brokers oder zu den Autoren von opensteuerauszug. Es ist keine Steuerberatung. Die Nutzung erfolgt ohne Gewähr und auf eigene Verantwortung: Die erzeugten Steuerauszüge können fehlerhaft oder unvollständig sein. Prüfe sie vor der Einreichung immer selbst, insbesondere gegen dein IBKR Activity Statement und bei Kapitalmassnahmen. Massgeblich sind allein deine eigenen Angaben gegenüber der Steuerbehörde.

## Einrichtung

1. Voraussetzung: Python ab 3.11 (und optional git; ohne git lädt die App das Tool als ZIP). Das Tool [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) installiert die App auf Knopfdruck: Im Bereich "Tool" erscheint bei fehlendem Tool "Tool installieren", sonst "Tool aktualisieren". Eine bestehende Installation unter `%USERPROFILE%\opensteuerauszug` (mit `.venv`) wird automatisch genutzt; sonst installiert die App nach `tool\opensteuerauszug` im Projektordner. Ein eigener Pfad ist in der Oberfläche (Feld Tool-Ordner) oder in `settings.json` setzbar, alternativ per Umgebungsvariable `OPENSTEUER_TOOL_REPO`. Der bekannte ibflex-Fix (`ibflex` durch `ibflex2` in dessen `pyproject.toml`) wird bei Installation und Update automatisch erledigt, sofern Upstream ihn nicht schon enthält: Upstream hat den `ibflex2`-Eintrag inzwischen selbst, der automatische Fix greift nur noch bei älteren Ständen. Eine bestehende ältere Installation lässt sich in der App über "Tool aktualisieren" auf den neuesten Stand bringen. Ein lokal angewendeter ibflex-Fix wird dabei automatisch zurückgesetzt, untracked Dateien bleiben unberührt. Beim Update werden `config.toml`, `data\security_identifiers.csv` und eigene Dateien nie gelöscht (bei ZIP-Installationen entfallen nur *.py unter `src\`, die Upstream entfernt hat; bei einem Fehler wird der Ausgangszustand wiederhergestellt).
2. Doppelklick auf `start.bat`. Das Skript sucht Python ab 3.11 (`py`-Launcher, dann `python`/`python3` im PATH; der Microsoft-Store-Platzhalter wird übersprungen). Findet es keines, bietet es nach Rückfrage (J/N) die automatische Installation von Python 3.12 per winget an (Benutzerinstallation, keine Adminrechte nötig). Ohne winget oder bei "N" erscheint ein Hinweis auf https://www.python.org/downloads/ (Version 3.12, Haken "Add python.exe to PATH" bzw. py-Launcher). Ein vorhandenes `.venv` mit Python unter 3.11 oder ein defektes `.venv` wird gemeldet und nach Rückfrage neu angelegt. Danach installiert das Skript `requirements.txt` (bei einem Fehler, z.B. ohne Internet, wird der Server nicht gestartet), startet den Server auf `http://127.0.0.1:8765` und öffnet den Browser. Mit `powershell -File start.ps1 -DryRun` wird nur angezeigt, was passieren würde.

Der Server bindet ausschliesslich an 127.0.0.1. Persönliche Daten (`settings.json`, `work\`, `output\`) sind per `.gitignore` ausgeschlossen. Der Flex-Token liegt im Klartext in `settings.json`, wird aber nie geloggt und in der UI nur maskiert angezeigt.

## Flex-Query bei IBKR einmalig einrichten

1. Client Portal: Performance & Reports > Flex Queries > Activity Flex Query > neu erstellen.
2. Sections aktivieren (jeweils alle Felder auswählen): Account Information, Trades, Open Positions, Transfers, Corporate Actions, Cash Transactions, Cash Report, Securities Info.
3. Format: XML. Zeitraum: beliebig (wird beim automatischen Abruf durch das Steuerjahr 01.01. bis 31.12. überschrieben).
4. Speichern und die Query-ID notieren (eine Query pro Konto möglich).
5. Flex Web Service aktivieren: Flex Queries > Flex Web Service Configuration > aktivieren, Token erzeugen (Gültigkeit beachten) und kopieren.

## Bedienung

- **Status**: Das Badge oben zeigt, ob das Tool-venv gefunden wird und `opensteuerauszug` importierbar ist. Der Bereich "Tool" zeigt Ordner, Herkunft, Installationsart und Stand und bietet Installieren bzw. Aktualisieren.
- **Kurslisten**: Tabelle der Jahre ab 2020 mit Status (nicht vorhanden / XML / XML + SQLite). "Herunterladen/Aktualisieren" startet einen Hintergrundjob mit Live-Log. Scheitert die SQLite-Konvertierung (bekannter Fehler `UNIQUE constraint failed: signs.kl_id`, z.B. 2025), wird die halbfertige `.sqlite` gelöscht und das Jahr als "XML" behandelt.
- **IBKR-Daten**: Flex-XML per Drag&Drop hochladen oder per Flex Web Service abrufen (Token und Query-IDs unter "Flex Web Service" speichern).
- **Steuerauszug erzeugen**: Jahr wählen (Standard Vorjahr), Dateien ankreuzen, "Steuerauszug erzeugen". Fehlt die Kursliste, wird sie zuerst geladen. Ergebnisse: `output\<Jahr>\Steuerauszug_<Konto>_<Jahr>.pdf/.xml`.
- **Alles automatisch**: Kursliste prüfen/laden, Flex-Abruf aller gespeicherten Queries, Steuerauszüge erzeugen.
- **Verlauf**: Alle erzeugten Dateien je Jahr mit Download-Links.

Es läuft immer nur ein Job gleichzeitig.

## Getestete Tool-Version

Upstream (opensteuerauszug) hat keine Tags oder Releases. Die App installiert deshalb nicht den jeweils neuesten Stand von `main`, sondern genau den Commit, der in `app\tool_version.json` eingetragen und mit dieser App getestet ist (Felder `repo`, `commit`, `tested_on`, `note`).

- **Installieren**: Git-Weg mit `git init`, `git fetch --depth 1 origin <commit>` und `git checkout --detach FETCH_HEAD` (Fallback: voller Clone und Checkout des Commits). Ohne git lädt die App das ZIP dieses Commits und merkt sich den Commit im Marker `.steuerauszug-assistent.json`.
- **Tool aktualisieren** bringt das Tool auf diese getestete Version. Ein bestehender Klon (z.B. auf Branch `main`) bleibt dabei heil: untracked Dateien bleiben, es gibt kein `clean` und kein `reset --hard`. Der Klon steht danach auf einem festen Commit (detached HEAD); das Log nennt den Befehl zurück auf den Branch (z.B. `git checkout main`). Ist das Tool bereits auf der getesteten Version, passiert nichts; ist es neuer, warnt die App und stuft nicht zurück (nur per API mit `{"target": "tested", "force": true}`).
- **Status**: Der Bereich "Tool" zeigt installierten und getesteten Commit sowie den Zustand (getestete Version / älter als getestet / neuer als getestet (ungetestet) / abweichend/unbekannt). Der Vergleich läuft lokal per `git merge-base --is-ancestor`, ohne Netzwerk. Fehlt der getestete Commit lokal in einem flachen Klon, lässt sich nichts sagen und es erscheint "abweichend/unbekannt".
- **Neueste Version (ungetestet)**: Der unauffällige Button daneben (bzw. `POST /api/tool/update` mit `{"target": "latest"}`) holt `main`. Das kann die App unbrauchbar machen; "Tool aktualisieren" führt zurück auf die getestete Version.
- **Pin anheben (Entwickler)**: Neuen Commit-SHA (40 Zeichen) und `tested_on` in `app\tool_version.json` eintragen, `.venv\Scripts\python.exe -m pytest -q` ausführen, einmal einen echten Lauf machen (frische Installation bzw. Update und Steuerauszug für ein Testjahr erzeugen) und danach die App neu veröffentlichen (Release).

## Entwicklung

`.venv\Scripts\python.exe -m pytest tests`

Optionale Regressionstests mit echten Flex-XML-Dateien: Umgebungsvariable `OPENSTEUER_REAL_DATA_DIR` auf einen Ordner setzen; alle `*.xml` darin werden nur lesend getestet. Ohne Variable oder ohne Dateien werden diese Tests übersprungen.

## Lizenz

[MIT](LICENSE). opensteuerauszug selbst steht unter eigener Lizenz (ebenfalls MIT, siehe dort) und ist nicht Teil dieses Repos; die App ruft es nur als separates Programm auf.
