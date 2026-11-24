# Changelog

Alle nennenswerten Änderungen am Steuerauszug-Assistenten. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/).

## [v1.5.0] – <offen> – Programm für Windows und Mac

Datum: unveröffentlicht.

### Neu
- **Eigenständiges Programm** für Windows (x64) und macOS (Apple-Chip): herunterladen, entpacken, starten. Weder Python noch git noch eine Tool-Installation nötig.
  - Das Tool opensteuerauszug ist samt Abhängigkeiten enthalten (genau der Commit aus `app/tool_version.json`). Die Karte „Tool“ zeigt „mitgeliefert, Version <Commit>“; Installieren und Aktualisieren des Tools sind ausgeblendet, `/api/tool/install` und `/api/tool/update` antworten mit 409.
  - Das Programm ruft sich für das Tool selbst auf (`--run-tool <modul>`, feste Liste erlaubter Module; `--tool-kursliste-dir` statt beliebigem Code).
  - Startprogramm `app/launcher.py`: erkennt eine bereits laufende App (öffnet nur den Browser), meldet einen fremd belegten Port, startet den Server im selben Prozess und öffnet den Browser. Kleines Statusfenster mit „Im Browser öffnen“, „Datenordner öffnen“ und „Beenden“ (ohne tkinter nur Konsole). Optionen `--no-browser` und `--no-window`. Ohne Konsole landet die Ausgabe in `<Datenordner>/logs/app.log`.
  - Dasselbe Startprogramm läuft auch in der Python-Variante: `python -m app.launcher`.
  - Die Karte „App-Version“ zeigt im Programm die Version und einen Link zu den Releases. Das automatische Programm-Update folgt in einer späteren Version.
- **Gemeinsamer Datenordner** für beide Varianten: Windows `%LOCALAPPDATA%\Steuerauszug-Assistent`, macOS `~/Library/Application Support/Steuerauszug-Assistent`, Linux `~/.local/share/steuerauszug-assistent` (überschreibbar mit `STEUERAUSZUG_DATA_DIR`). Darin `settings.json`, `work/uploads`, `output`.
  - Einmalige Migration: Liegen im Projektordner noch `settings.json`, `work/` oder `output/` und hat der Datenordner keine `settings.json`, werden sie dorthin **kopiert** (nichts verschoben, nichts überschrieben, Marker `.migriert-aus-projektordner`).
  - Neuer Bereich „Daten“ mit dem Datenordner, „Ordner öffnen“, „Ergebnisse öffnen“ (`POST /api/open-folder`, nur die festen Ziele `data` und `output`) und „App beenden“ (`POST /api/app/quit`).
- `/api/status` liefert zusätzlich `app_version_number` (aus der neuen Datei `app/VERSION`), `bundled`, `data_dir` und `output_dir`; neu: `/api/health`.

### Für Entwickler
- Build mit PyInstaller: `packaging/build_app.py` (Abhängigkeiten in `packaging/requirements-build.txt`), Kurztest `tests/smoke_app.py`.
- Neuer Workflow `.github/workflows/release.yml`: prüft Tag gegen `app/VERSION`, legt bei einem gepushten Tag das Release mit den Notizen aus diesem Changelog an und hängt die ZIP-Dateien für Windows und macOS an.
- Die Start-Skripte (`start.bat`, `start.ps1`, `start.command`) bleiben unverändert, inklusive Neustart nach einem Update (Exit-Code 3).
- Tests nutzen immer einen temporären Datenordner und berühren das echte Benutzerprofil nie.
- `STEUERAUSZUG_BUNDLED_TOOL=1` erzwingt den Bundled-Modus auch in der Python-Variante.

### Update von v1.4.0
Die Python-Variante aktualisiert sich wie bisher per Knopf. Beim ersten Start der neuen Version werden `settings.json`, `work/` und `output/` aus dem Projektordner in den Datenordner kopiert; die alten Dateien können danach von Hand gelöscht werden.

## [v1.4.0] – 2026-11-24 – macOS-Version und App-Update

### Neu
- **macOS-Version:** `start.command` lässt sich im Finder per Doppelklick starten.
  - Sucht Python ab Version 3.11 (auch Homebrew und python.org-Installer). Fehlt es, bietet das Skript die Installation von Python 3.12 per Homebrew an; ohne Homebrew erscheint ein Hinweis auf python.org.
  - Ein defektes oder zu altes `.venv` wird erkannt und nach Rückfrage neu angelegt.
  - Läuft die App bereits, wird nur der Browser geöffnet; ist der Port anderweitig belegt, erscheint eine klare Meldung.
  - Für Linux gibt es `start.sh`.
- **App-Update per Git:** neue Karte „App-Version“ mit „Nach Updates suchen“, „Update installieren“ und „Jetzt neu starten“.
  - Zeigt die neuen Änderungen vor der Installation an.
  - Übernimmt nur Änderungen, die direkt auf den lokalen Stand folgen (Fast-Forward); nie `reset --hard`, nie `clean`, nie Force.
  - Bricht mit einer Meldung ab, wenn Programmdateien lokal geändert wurden. `settings.json`, `work/` und `output/` sind davon nicht betroffen.
  - Geänderte Abhängigkeiten werden nachinstalliert; schlägt das fehl, kehrt die App auf den alten Stand zurück.
  - Den Neustart übernehmen `start.bat` und `start.command`; die Seite lädt sich danach von selbst neu.
  - Voraussetzung: Installation per `git clone https://github.com/kevegl/opensteuerauszug-webapp.git`.

### Behoben
- Die App ging bisher davon aus, dass Python immer unter `.venv\Scripts\python.exe` liegt (nur Windows). Jetzt funktionieren Tool-Erkennung, Installation und Aufruf auch unter macOS und Linux.
- Unter macOS ohne Xcode Command Line Tools öffnet die Tool-Installation keinen Installationsdialog mehr, sondern nimmt den ZIP-Weg.
- Upload: Dateinamen mit `\` wurden ausserhalb von Windows nicht korrekt zerlegt, dadurch fehlte dort die Duplikatprüfung.

### Für Entwickler
- GitHub Actions: Tests und Starter-Probeläufe auf Windows, macOS und Linux bei jedem Push.
- `.gitattributes` legt die Zeilenenden der Starter fest (LF für `start.command`/`start.sh`, CRLF für `start.ps1`/`start.bat`).

### Update von v1.3.0
Diese Version bringt die Update-Funktion erst mit. Einmalig von Hand aktualisieren: im App-Ordner `git pull` ausführen und `start.bat` neu starten. Danach geht es über den Knopf.

## [v1.3.0] – 2026-11-23 – Getestete Tool-Version

### Neu
- **Getestete Tool-Version:** Installation und „Tool aktualisieren“ holen opensteuerauszug nicht mehr blind vom neuesten Stand, sondern auf den getesteten Commit [`4633aab`](https://github.com/vroonhof/opensteuerauszug/commit/4633aab493e90a0f13e6701e8aa2d85cf77e4bab) (eingetragen in `app/tool_version.json`). Damit kann ein Upstream-Update die App nicht unbemerkt unbrauchbar machen.
- **Statusanzeige:** zeigt den installierten und den getesteten Stand sowie den Zustand: getestete Version, älter, neuer (ungetestet) oder unbekannt.
- **Der Button passt sich an:** „Tool prüfen“, „Tool aktualisieren“ oder „Zurück zur getesteten Version“ (mit Nachfrage).
- **„Neueste Version (ungetestet)“:** eigener Button mit Warnung für alle, die den aktuellen Upstream-Stand wollen.
- **Sichere Aktualisierung bestehender git-Installationen:**
  - Untracked Dateien wie `config.toml` oder eigene XML-Dateien bleiben unberührt.
  - Ein lokaler ibflex-Fix wird automatisch zurückgesetzt und bei Bedarf neu angewendet.
  - Das Log nennt den Rückweg zum Branch `main`.

### Für Entwickler
- Den Pin anheben: Commit in `app/tool_version.json` eintragen, Tests und einen echten Lauf durchführen, dann ein Release erstellen (siehe README).

## [v1.2.0] – 2026-11-23 – Python-Installation und robuster Start

### Neu
- **Python wird automatisch installiert:** `start.bat` sucht ein Python ab Version 3.11. Fehlt es, bietet das Skript an, Python 3.12 per winget zu installieren (Benutzerinstallation, keine Adminrechte). Danach geht es ohne Neustart weiter. Ohne winget erscheint ein Hinweis auf python.org.
- **Robustere Python-Suche:**
  - Zu alte Versionen und der Microsoft-Store-Platzhalter werden übersprungen.
  - Ein echtes Python hinter dem Platzhalter im PATH wird trotzdem gefunden.
  - Hängende Kandidaten werden nach 15 s mit Hinweis übersprungen.
- **venv-Prüfung:** Ein defektes oder zu altes `.venv` wird erkannt und nach Rückfrage neu angelegt.
- **App läuft bereits:** Statt eines Fehlers wird nur der Browser geöffnet.
- **Port belegt:** Ist der Port von einem anderen Programm belegt, erscheint eine klare Meldung mit dem Hinweis, wie man es findet.

### Behoben
- Bisher meldete das Skript „Server läuft“ und öffnete den Browser auch dann, wenn der Start fehlgeschlagen war. Jetzt öffnet sich der Browser erst, wenn der Server antwortet.
- Schlägt `pip install` fehl, bricht das Skript ab, statt den Server ohne Abhängigkeiten zu starten.

### Für Tests
- `start.ps1 -DryRun` zeigt, was passieren würde, ohne etwas zu installieren oder zu starten.

## [v1.1.0] – 2026-11-23 – Tool-Installation integriert

### Neu
- **Tool-Installation auf Knopfdruck:** Die App installiert [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) selbst, per `git clone` oder ohne git als ZIP-Download. Sie legt das venv an und führt `pip install` aus. Eine manuelle Installation ist nicht mehr nötig.
- **„Tool aktualisieren“:** holt den neuesten Stand.
  - Bei git-Installationen wird ein lokal angewendeter ibflex-Fix automatisch zurückgesetzt und bei Bedarf erneut angewendet.
  - Untracked Dateien (z.B. `config.toml`, eigene XML-Dateien) bleiben unberührt.
  - ZIP-Updates gleichen Datei für Datei ab und werden bei einem Fehler zurückgesetzt.
- **Automatische Erkennung:**
  - Eine bestehende Installation unter `%USERPROFILE%\opensteuerauszug` wird weiter genutzt.
  - Sonst installiert die App in den app-eigenen Ordner `tool\`.
  - Ein eigener Pfad lässt sich weiterhin setzen und auch wieder auf Automatik zurückstellen.
- **Erweiterter Status:** Tool-Ordner, Herkunft, Installationsart (git oder ZIP) und installierter Stand.

### Sicherheit
- Das Entpacken von ZIP-Dateien ist gegen Zip-Slip abgesichert: Absolute Pfade, `..`, Laufwerksbuchstaben und Symlinks werden abgelehnt.

### Hinweis
- Upstream deklariert `ibflex2` inzwischen selbst. Der automatische ibflex-Fix greift nur noch bei älteren Ständen.

## [v1.0.0] – 2026-11-23 – Erste Version

Erste Version der lokalen Webapp, die das Erstellen von Schweizer Steuerauszügen aus Interactive-Brokers-Daten mit [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) automatisiert.

### Funktionen
- **Kurslisten pro Jahr:** Status ab 2020 und Download per Knopfdruck. Der bekannte SQLite-Fehler `UNIQUE constraint failed` bei der Kursliste 2025 wird automatisch abgefangen.
- **IBKR-Daten:** Upload von Flex-Query-XML-Dateien (mehrere Konten) oder automatischer Abruf über den Flex Web Service (Token und Query-IDs, Zeitraum 01.01.–31.12.).
- **Steuerauszug erzeugen:** pro Konto ein PDF und ein XML in `output\<Jahr>\`.
- **„Alles automatisch“:** Kursliste prüfen und laden, Daten abrufen und Auszüge erzeugen in einem Klick.
- **Sicherheit:**
  - Der Server läuft nur auf 127.0.0.1.
  - Der Token wird maskiert angezeigt und nicht geloggt.
  - Uploads werden geprüft: nur Flex-XML, keine DTD/Entities, höchstens 50 MB pro Datei.
  - Ein Upload wird ganz oder gar nicht gespeichert.

### Voraussetzungen
- Windows und Python 3.12
- opensteuerauszug installiert unter `%USERPROFILE%\opensteuerauszug` (oder über `OPENSTEUER_TOOL_REPO`), inklusive des ibflex→ibflex2-Fixes, siehe README

### Start
Doppelklick auf `start.bat`. Das venv wird angelegt, die Abhängigkeiten installiert, und es öffnet sich http://127.0.0.1:8765.

### Bekannte Einschränkungen
- Der Abruf über den IBKR Flex Web Service ist noch nicht gegen die Live-API getestet.
- Die Ergebnisse müssen gegen das IBKR Activity Statement geprüft werden.

[v1.5.0]: https://github.com/Kevegl/opensteuerauszug-webapp/releases/tag/v1.5.0
[v1.4.0]: https://github.com/Kevegl/opensteuerauszug-webapp/releases/tag/v1.4.0
[v1.3.0]: https://github.com/Kevegl/opensteuerauszug-webapp/releases/tag/v1.3.0
[v1.2.0]: https://github.com/Kevegl/opensteuerauszug-webapp/releases/tag/v1.2.0
[v1.1.0]: https://github.com/Kevegl/opensteuerauszug-webapp/releases/tag/v1.1.0
[v1.0.0]: https://github.com/Kevegl/opensteuerauszug-webapp/releases/tag/v1.0.0
