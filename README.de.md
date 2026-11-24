# Steuerauszug-Assistent

[English](README.md) | **Deutsch**

Lokale Weboberfläche (Deutsch), die das Tool [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) automatisiert: Kursliste laden, IBKR-Flex-Daten holen oder hochladen, Schweizer Steuerauszug (PDF + XML) erzeugen. Das Tool selbst wird nur als Subprozess aufgerufen (Python-Variante: mit seinem eigenen venv; Programm: mitgeliefert) und nie verändert.

> **Haftungsausschluss:** Dieses Projekt ist ein privates Hilfswerkzeug und steht in keiner Verbindung zur Eidgenössischen Steuerverwaltung (ESTV), zu Interactive Brokers oder zu den Autoren von opensteuerauszug. Es ist keine Steuerberatung. Die Nutzung erfolgt ohne Gewähr und auf eigene Verantwortung: Die erzeugten Steuerauszüge können fehlerhaft oder unvollständig sein. Prüfe sie vor der Einreichung immer selbst, insbesondere gegen dein IBKR Activity Statement und bei Kapitalmassnahmen. Massgeblich sind allein deine eigenen Angaben gegenüber der Steuerbehörde.

## Welche Variante passt?

| Variante | Für wen | Voraussetzungen |
|---|---|---|
| **Programm herunterladen** (empfohlen) | Windows (x64) und Mac mit Apple-Chip (M1 und neuer) | keine: Python und das Tool opensteuerauszug sind enthalten |
| **Python-Variante** | Entwickler, Intel-Macs, Linux | Python ab 3.11 (git optional) |

Beide Varianten nutzen denselben [Datenordner](#datenordner), man kann also ohne Datenverlust wechseln.

## Programm (Windows & Mac)

1. Auf der Seite [Releases](https://github.com/Kevegl/opensteuerauszug-webapp/releases/latest) die ZIP-Datei für das eigene System herunterladen: `Steuerauszug-Assistent-<Version>-windows-x64.zip` bzw. `Steuerauszug-Assistent-<Version>-macos-arm64.zip`.
2. Die ZIP-Datei entpacken (Windows: Rechtsklick, "Alle extrahieren"; Mac: Doppelklick) und den Ordner bzw. die App an einen beliebigen Ort legen, z.B. in den Ordner "Programme".
3. Starten: Windows `Steuerauszug-Assistent.exe` doppelklicken, Mac die App `Steuerauszug-Assistent` öffnen. Ein kleines Statusfenster erscheint, der Browser öffnet `http://127.0.0.1:8765`.
4. Beim ersten Start warnt das Betriebssystem, weil das Programm nicht kostenpflichtig signiert ist:
   - **Windows (SmartScreen)**: "Der Computer wurde durch Windows geschützt" > **Weitere Informationen** > **Trotzdem ausführen**.
   - **Mac (Gatekeeper)**: Rechtsklick auf die App > **Öffnen** > **Öffnen**. Alternativ: Systemeinstellungen > Datenschutz & Sicherheit > **Dennoch öffnen**.
5. Beenden: im Statusfenster "Beenden" (oder das Fenster schliessen) bzw. in der Oberfläche "App beenden".

Das Tool opensteuerauszug ist im Programm enthalten (die Karte "Tool" zeigt "mitgeliefert, Version <Commit>"); Installieren und Aktualisieren des Tools entfallen. Aktualisiert wird das Programm vorerst von Hand: neue ZIP-Datei von der Release-Seite laden und den alten Programmordner ersetzen. Die Daten bleiben im Datenordner erhalten. Ein automatisches Programm-Update folgt in einer späteren Version.

Ein Windows-Programm ohne Konsole schreibt sein Protokoll nach `<Datenordner>/logs/app.log`.

## Datenordner

Einstellungen (inkl. Flex-Token), hochgeladene Dateien und Ergebnisse liegen pro Benutzer in einem Datenordner, nicht im Programm- oder Projektordner. In der Oberfläche (Bereich "Daten") zeigt "Ordner öffnen" ihn an.

| System | Datenordner |
|---|---|
| Windows | `%LOCALAPPDATA%\Steuerauszug-Assistent` |
| macOS | `~/Library/Application Support/Steuerauszug-Assistent` |
| Linux | `~/.local/share/steuerauszug-assistent` |

Die Umgebungsvariable `STEUERAUSZUG_DATA_DIR` überschreibt den Ordner. Darin liegen `settings.json`, `work/uploads/` (hochgeladene und per Flex abgerufene XML-Dateien), `output/<Jahr>/` (Ergebnisse) und `logs/`.

**Umzug aus dem Projektordner:** Liegen im Projektordner noch `settings.json`, `work/` oder `output/` der älteren Version und hat der Datenordner noch keine `settings.json`, werden sie beim ersten Start einmalig in den Datenordner **kopiert** (nicht verschoben, nichts wird überschrieben; Hinweis im Log, danach verhindert die Datei `.migriert-aus-projektordner` eine Wiederholung). Die alten Dateien kann man danach selbst löschen.

## Python-Variante (Entwickler, Intel-Macs, Linux)

1. Voraussetzung: Python ab 3.11 (und optional git; ohne git lädt die App das Tool als ZIP). Das Tool [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) installiert die App auf Knopfdruck: Im Bereich "Tool" erscheint bei fehlendem Tool "Tool installieren", sonst "Tool aktualisieren". Eine bestehende Installation unter `~/opensteuerauszug` (Windows: `%USERPROFILE%\opensteuerauszug`) (mit `.venv`) wird automatisch genutzt; sonst installiert die App nach `tool/opensteuerauszug` im Projektordner. Ein eigener Pfad ist in der Oberfläche (Feld Tool-Ordner) oder in `settings.json` setzbar, alternativ per Umgebungsvariable `OPENSTEUER_TOOL_REPO`. Der bekannte ibflex-Fix (`ibflex` durch `ibflex2` in dessen `pyproject.toml`) wird bei Installation und Update automatisch erledigt, sofern Upstream ihn nicht schon enthält: Upstream hat den `ibflex2`-Eintrag inzwischen selbst, der automatische Fix greift nur noch bei älteren Ständen. Eine bestehende ältere Installation lässt sich in der App über "Tool aktualisieren" auf den neuesten Stand bringen. Ein lokal angewendeter ibflex-Fix wird dabei automatisch zurückgesetzt, untracked Dateien bleiben unberührt. Beim Update werden `config.toml`, `data/security_identifiers.csv` und eigene Dateien nie gelöscht (bei ZIP-Installationen entfallen nur *.py unter `src/`, die Upstream entfernt hat; bei einem Fehler wird der Ausgangszustand wiederhergestellt).
2. App starten:
   - **Windows**: Doppelklick auf `start.bat`. Das Skript sucht Python ab 3.11 (`py`-Launcher, dann `python`/`python3` im PATH; der Microsoft-Store-Platzhalter wird übersprungen). Findet es keines, bietet es nach Rückfrage (J/N) die automatische Installation von Python 3.12 per winget an (Benutzerinstallation, keine Adminrechte nötig). Ohne winget oder bei "N" erscheint ein Hinweis auf https://www.python.org/downloads/ (Version 3.12, Haken "Add python.exe to PATH" bzw. py-Launcher). Ein vorhandenes `.venv` mit Python unter 3.11 oder ein defektes `.venv` wird gemeldet und nach Rückfrage neu angelegt. Danach installiert das Skript `requirements.txt` (bei einem Fehler, z.B. ohne Internet, wird der Server nicht gestartet), startet den Server auf `http://127.0.0.1:8765` und öffnet den Browser. Mit `powershell -File start.ps1 -DryRun` wird nur angezeigt, was passieren würde.
   - **macOS**: Doppelklick auf `start.command` im Finder (empfohlen: Installation per `git clone https://github.com/kevegl/opensteuerauszug-webapp.git`, das ermöglicht auch das Update in der App). Das Skript sucht Python ab 3.11 (`python3.13`, `python3.12`, `python3.11`, `python3`, Homebrew- und python.org-Pfade; der Apple-Platzhalter `/usr/bin/python3` wird nur probiert, wenn die Xcode Command Line Tools installiert sind, damit kein Installationsdialog aufgeht). Findet es keines und ist Homebrew vorhanden, bietet es nach Rückfrage (J/N) `brew install python@3.12` an; sonst erscheint ein Hinweis auf https://www.python.org/downloads/ (macOS-Installer, Version 3.12). Mit `STEUERAUSZUG_NO_BREW=1` wird Homebrew nicht genutzt. Ein defektes oder zu altes `.venv` wird nach Rückfrage neu angelegt. Danach installiert das Skript `requirements.txt`, startet den Server und öffnet den Browser. `./start.command --dry-run` zeigt nur, was passieren würde (`--yes` / `--no` beantworten alle Fragen automatisch). Bei einem ZIP-Download kann macOS das Skript blockieren (Gatekeeper): Rechtsklick, dann "Öffnen", oder `xattr -d com.apple.quarantine start.command`. Fehlt das Ausführungsbit, einmal `chmod +x start.command` ausführen.
   - **Linux**: `./start.sh` (dünner Wrapper um `start.command`; öffnet den Browser per `xdg-open`).

   Die Starter setzen `STEUERAUSZUG_LAUNCHER=1` und starten den Server automatisch neu, wenn die App nach einem Update einen Neustart anfordert (Exit-Code 3).

   Alternativ startet `python -m app.launcher` (im `.venv`) denselben Server wie das Programm, mit Statusfenster (falls tkinter vorhanden ist; sonst nur Konsole) und Browser; Optionen `--no-browser` und `--no-window` (oder `STEUERAUSZUG_NO_BROWSER=1`).

Der Server bindet ausschliesslich an 127.0.0.1. Persönliche Daten liegen im [Datenordner](#datenordner), nicht im Projektordner. Der Flex-Token liegt im Klartext in `settings.json` (im Datenordner), wird aber nie geloggt und in der UI nur maskiert angezeigt.

## App-Update

Wurde die App per `git clone` installiert, kann sie sich selbst aktualisieren (Bereich "App-Version" in der Oberfläche):

1. "Nach Updates suchen" führt `git fetch` aus und zeigt, wie viele neue Commits es gibt, den neuesten Tag und die Commit-Liste.
2. "Update installieren" läuft als Job mit Live-Log: `git fetch`, dann `git merge --ff-only` auf den Upstream-Branch (`@{u}`, Fallback `origin/main`). Hat sich `requirements.txt` geändert, läuft `pip install -r requirements.txt`; schlägt das fehl, wird der alte Stand mit `git reset --keep` wiederhergestellt.
3. "Jetzt neu starten" startet den Server neu (nur wenn er über `start.bat` / `start.command` gestartet wurde; sonst Fenster schliessen und das Skript erneut starten). Die Seite lädt sich selbst neu.

Was die App nie tut: kein `reset --hard`, kein `clean`, kein Force, keine Merge-Commits. Das Update wird mit einer Meldung abgelehnt, wenn verfolgte Dateien lokal geändert sind (vorher `git stash`), HEAD losgelöst (detached) ist, der lokale Stand dem Upstream voraus ist bzw. divergiert oder nichts Neues vorliegt. Der Datenordner liegt ausserhalb des Projekts; `.venv` und `tool/` sind von Git ignoriert und bleiben unberührt. Ohne Git-Klon (z.B. ZIP-Download) zeigt der Bereich nur den Clone-Befehl.

## Flex-Query bei IBKR einmalig einrichten

1. Client Portal: Performance & Reports > Flex Queries > Activity Flex Query > neu erstellen.
2. Sections aktivieren (jeweils alle Felder auswählen): Account Information, Trades, Open Positions, Transfers, Corporate Actions, Cash Transactions, Cash Report, Securities Info.
3. Format: XML. Zeitraum: beliebig (wird beim automatischen Abruf durch das Steuerjahr 01.01. bis 31.12. überschrieben).
4. Speichern und die Query-ID notieren (eine Query pro Konto möglich).
5. Flex Web Service aktivieren: Flex Queries > Flex Web Service Configuration > aktivieren, Token erzeugen (Gültigkeit beachten) und kopieren.

## Bedienung

- **Status**: Das Badge oben zeigt, ob das Tool-venv gefunden wird (im Programm: ob das mitgelieferte Tool vorhanden ist) und `opensteuerauszug` importierbar ist. Der Bereich "Tool" zeigt Ordner, Herkunft, Installationsart und Stand und bietet Installieren bzw. Aktualisieren.
- **Kurslisten**: Tabelle der Jahre ab 2020 mit Status (nicht vorhanden / XML / XML + SQLite). "Herunterladen/Aktualisieren" startet einen Hintergrundjob mit Live-Log. Scheitert die SQLite-Konvertierung (bekannter Fehler `UNIQUE constraint failed: signs.kl_id`, z.B. 2025), wird die halbfertige `.sqlite` gelöscht und das Jahr als "XML" behandelt.
- **IBKR-Daten**: Flex-XML per Drag&Drop hochladen oder per Flex Web Service abrufen (Token und Query-IDs unter "Flex Web Service" speichern).
- **Steuerauszug erzeugen**: Jahr wählen (Standard Vorjahr), Dateien ankreuzen, "Steuerauszug erzeugen". Fehlt die Kursliste, wird sie zuerst geladen. Ergebnisse: `<Datenordner>/output/<Jahr>/Steuerauszug_<Konto>_<Jahr>.pdf/.xml`.
- **Alles automatisch**: Kursliste prüfen/laden, Flex-Abruf aller gespeicherten Queries, Steuerauszüge erzeugen.
- **Verlauf**: Alle erzeugten Dateien je Jahr mit Download-Links.
- **Daten**: zeigt den Datenordner; "Ordner öffnen" und "Ergebnisse öffnen" öffnen ihn im Datei-Manager, "App beenden" beendet die App.

Es läuft immer nur ein Job gleichzeitig.

## Getestete Tool-Version

Upstream (opensteuerauszug) hat keine Tags oder Releases. Die App installiert deshalb nicht den jeweils neuesten Stand von `main`, sondern genau den Commit, der in `app/tool_version.json` eingetragen und mit dieser App getestet ist (Felder `repo`, `commit`, `tested_on`, `note`).

- **Installieren**: Git-Weg mit `git init`, `git fetch --depth 1 origin <commit>` und `git checkout --detach FETCH_HEAD` (Fallback: voller Clone und Checkout des Commits). Ohne git lädt die App das ZIP dieses Commits und merkt sich den Commit im Marker `.steuerauszug-assistent.json`.
- **Tool aktualisieren** bringt das Tool auf diese getestete Version. Ein bestehender Klon (z.B. auf Branch `main`) bleibt dabei heil: untracked Dateien bleiben, es gibt kein `clean` und kein `reset --hard`. Der Klon steht danach auf einem festen Commit (detached HEAD); das Log nennt den Befehl zurück auf den Branch (z.B. `git checkout main`). Ist das Tool bereits auf der getesteten Version, passiert nichts; ist es neuer, warnt die App und stuft nicht zurück (nur per API mit `{"target": "tested", "force": true}`).
- **Status**: Der Bereich "Tool" zeigt installierten und getesteten Commit sowie den Zustand (getestete Version / älter als getestet / neuer als getestet (ungetestet) / abweichend/unbekannt). Der Vergleich läuft lokal per `git merge-base --is-ancestor`, ohne Netzwerk. Fehlt der getestete Commit lokal in einem flachen Klon, lässt sich nichts sagen und es erscheint "abweichend/unbekannt".
- **Neueste Version (ungetestet)**: Der unauffällige Button daneben (bzw. `POST /api/tool/update` mit `{"target": "latest"}`) holt `main`. Das kann die App unbrauchbar machen; "Tool aktualisieren" führt zurück auf die getestete Version.
- **Pin anheben (Entwickler)**: Neuen Commit-SHA (40 Zeichen) und `tested_on` in `app/tool_version.json` eintragen, `.venv/Scripts/python.exe -m pytest -q` (Windows) bzw. `.venv/bin/python -m pytest -q` (macOS/Linux) ausführen, einmal einen echten Lauf machen (frische Installation bzw. Update und Steuerauszug für ein Testjahr erzeugen) und danach die App neu veröffentlichen (Release).

## Entwicklung

Windows: `.venv\Scripts\python.exe -m pytest -q`

macOS/Linux: `.venv/bin/python -m pytest -q`

Optionale Regressionstests mit echten Flex-XML-Dateien: Umgebungsvariable `OPENSTEUER_REAL_DATA_DIR` auf einen Ordner setzen; alle `*.xml` darin werden nur lesend getestet. Ohne Variable oder ohne Dateien werden diese Tests übersprungen.

### Programm bauen und veröffentlichen

- Lokal bauen (PyInstaller, Python 3.12): `pip install -r requirements.txt -r packaging/requirements-build.txt`, dann `python packaging/build_app.py`. Ergebnis: `dist/Steuerauszug-Assistent-<Version>-<system>-<arch>.zip`. Der Commit des mitgelieferten Tools kommt aus `app/tool_version.json`; die Zeile in `packaging/requirements-build.txt` muss denselben Commit nennen (ein Test prüft das).
- Kurztest des gebauten Programms (kein pytest-Test): `python tests/smoke_app.py -- dist/Steuerauszug-Assistent/Steuerauszug-Assistent`.
- Release: `app/VERSION` anheben, Abschnitt `## [vX.Y.Z] – <Datum> – <Titel>` in `CHANGELOG.md` anlegen, Tag `vX.Y.Z` pushen (oder ein Release veröffentlichen). Der Workflow `release.yml` prüft Tag und `app/VERSION`, legt bei Bedarf das Release mit den Notizen aus dem Changelog an und hängt die ZIP-Dateien für Windows (x64) und macOS (arm64) an. Von Hand gestartet (workflow_dispatch) baut und testet er nur und legt die ZIP-Dateien als Artefakt ab.
- Tool-Modus des Programms: Das Programm ruft sich für das Tool selbst auf (`Steuerauszug-Assistent --run-tool <modul> ...`, nur `opensteuerauszug.steuerauszug` und `opensteuerauszug.kursliste` sind erlaubt). `STEUERAUSZUG_BUNDLED_TOOL=1` erzwingt den Bundled-Modus auch in der Python-Variante (wenn `opensteuerauszug` dort installiert ist).

## Änderungen

Siehe [CHANGELOG.md](CHANGELOG.md).

## Lizenz

[MIT](LICENSE). opensteuerauszug selbst steht unter eigener Lizenz (ebenfalls MIT, siehe dort) und ist nicht Teil dieses Repos; die App ruft es nur als separates Programm auf.
