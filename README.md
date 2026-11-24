# Steuerauszug-Assistent

**English** | [Deutsch](README.de.md)

A local web UI (German only) that automates the [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) tool: download the Kursliste (ESTV price list), fetch or upload IBKR Flex data, and generate a Swiss tax statement (Steuerauszug) as PDF + XML. The tool itself is only called as a subprocess using its own venv and is never modified.

> **Disclaimer:** This project is a private helper tool and is not affiliated with the Swiss Federal Tax Administration (ESTV), Interactive Brokers, or the authors of opensteuerauszug. It is not tax advice. It is provided without warranty and used at your own risk: the generated tax statements (Steuerauszüge) may be incorrect or incomplete. Always check them yourself before filing, in particular against your IBKR Activity Statement and for corporate actions. Only your own declarations to the tax authority are authoritative.

## Setup

1. Prerequisite: Python 3.11 or newer (and optionally git; without git the app downloads the tool as a ZIP). The app installs the [opensteuerauszug](https://github.com/vroonhof/opensteuerauszug) tool at the press of a button: in the "Tool" section, "Tool installieren" (Install tool) appears if the tool is missing, otherwise "Tool aktualisieren" (Update tool). An existing installation under `~/opensteuerauszug` (Windows: `%USERPROFILE%\opensteuerauszug`) (with `.venv`) is used automatically; otherwise the app installs into `tool/opensteuerauszug` in the project folder. A custom path can be set in the UI (field "Tool-Ordner", tool folder) or in `settings.json`, or alternatively via the environment variable `OPENSTEUER_TOOL_REPO`. The known ibflex fix (replacing `ibflex` with `ibflex2` in its `pyproject.toml`) is applied automatically on install and update, unless upstream already includes it: upstream now has the `ibflex2` entry itself, so the automatic fix only applies to older revisions. An older existing installation can be brought up to date in the app via "Tool aktualisieren". A locally applied ibflex fix is reset automatically in the process; untracked files are left untouched. On update, `config.toml`, `data/security_identifiers.csv` and your own files are never deleted (for ZIP installations, only *.py files under `src/` that upstream has removed are dropped; on error, the original state is restored).
2. Start the app:
   - **Windows**: double-click `start.bat`. The script looks for Python 3.11 or newer (the `py` launcher, then `python`/`python3` on the PATH; the Microsoft Store placeholder is skipped). If it finds none, it offers, after a prompt (J/N, i.e. Y/N), to install Python 3.12 automatically via winget (per-user install, no admin rights needed). Without winget, or if you answer "N", a note points to https://www.python.org/downloads/ (version 3.12, tick "Add python.exe to PATH" or the py launcher). An existing `.venv` with Python below 3.11, or a broken `.venv`, is reported and recreated after a prompt. The script then installs `requirements.txt` (on failure, e.g. without internet, the server is not started), starts the server at `http://127.0.0.1:8765` and opens the browser. `powershell -File start.ps1 -DryRun` only shows what would happen.
   - **macOS**: double-click `start.command` in Finder (recommended: install the app via `git clone https://github.com/kevegl/opensteuerauszug-webapp.git`, which also enables the in-app update). The script looks for Python 3.11 or newer (`python3.13`, `python3.12`, `python3.11`, `python3`, Homebrew and python.org locations; the Apple placeholder `/usr/bin/python3` is only tried if the Xcode Command Line Tools are installed, so no install dialog pops up). If it finds none and Homebrew is available, it offers (J/N) `brew install python@3.12`; otherwise a note points to https://www.python.org/downloads/ (macOS installer, version 3.12). Setting `STEUERAUSZUG_NO_BREW=1` disables Homebrew. A broken or too old `.venv` is recreated after a prompt. The script then installs `requirements.txt`, starts the server and opens the browser. `./start.command --dry-run` only shows what would happen (`--yes` / `--no` answer all prompts automatically). If you downloaded a ZIP: macOS may block the script (Gatekeeper); right-click, then "Open", or run `xattr -d com.apple.quarantine start.command`. If the execute bit is missing, run `chmod +x start.command` once.
   - **Linux**: `./start.sh` (thin wrapper around `start.command`; opens the browser with `xdg-open`).

   The launchers set `STEUERAUSZUG_LAUNCHER=1` and restart the server automatically when the app asks for a restart after an update (exit code 3).

The server binds to 127.0.0.1 only. Personal data (`settings.json`, `work/`, `output/`) is excluded via `.gitignore`. The Flex token is stored in plain text in `settings.json`, but is never logged and is only shown masked in the UI.

## App update

If the app was installed with `git clone`, it can update itself (section "App-Version" in the UI):

1. "Nach Updates suchen" (Check for updates) runs `git fetch` and shows how many new commits there are, the latest tag and the commit list.
2. "Update installieren" (Install update) runs as a job with a live log: `git fetch`, then `git merge --ff-only` onto the upstream branch (`@{u}`, fallback `origin/main`). If `requirements.txt` changed, `pip install -r requirements.txt` runs; if that fails, the old state is restored with `git reset --keep`.
3. "Jetzt neu starten" (Restart now) restarts the server (only when started via `start.bat` / `start.command`; otherwise close the window and start the script again). The page reloads itself.

What it never does: no `reset --hard`, no `clean`, no force, no merge commits. The update is refused (with a message) if tracked files were changed locally (`git stash` first), if HEAD is detached, if the local branch is ahead of or has diverged from upstream, or if there is nothing new. `settings.json`, `work/`, `output/` and `.venv` are ignored by git and never touched. Without a git clone (e.g. ZIP download) the section only shows the clone command.

## Setting up the Flex query at IBKR (one time)

1. Client Portal: Performance & Reports > Flex Queries > Activity Flex Query > create new.
2. Enable these sections (select all fields in each): Account Information, Trades, Open Positions, Transfers, Corporate Actions, Cash Transactions, Cash Report, Securities Info.
3. Format: XML. Period: any (it is overridden by the tax year, 01.01. to 31.12., during the automatic fetch).
4. Save and note the query ID (one query per account is possible).
5. Enable the Flex Web Service: Flex Queries > Flex Web Service Configuration > enable, generate a token (note its validity period) and copy it.

## Usage

- **Status**: The badge at the top shows whether the tool venv is found and `opensteuerauszug` is importable. The "Tool" section shows folder, origin, installation type and revision, and offers install or update.
- **Kurslisten** (price lists): A table of the years from 2020 with status (not present / XML / XML + SQLite). "Herunterladen/Aktualisieren" (Download/Update) starts a background job with a live log. If the SQLite conversion fails (known error `UNIQUE constraint failed: signs.kl_id`, e.g. 2025), the half-finished `.sqlite` is deleted and the year is treated as "XML".
- **IBKR-Daten** (IBKR data): Upload the Flex XML by drag and drop, or fetch it via the Flex Web Service (save the token and query IDs under "Flex Web Service").
- **Steuerauszug erzeugen** (Generate tax statement): Choose the year (default: previous year), tick the files, and click "Steuerauszug erzeugen". If the Kursliste is missing, it is downloaded first. Results: `output/<year>/Steuerauszug_<account>_<year>.pdf/.xml`.
- **Alles automatisch** (Everything automatically): Check/download the Kursliste, fetch all saved Flex queries, generate the tax statements.
- **Verlauf** (History): All generated files per year with download links.

Only one job runs at a time.

## Tested tool version

Upstream (opensteuerauszug) has no tags or releases. The app therefore does not install the latest state of `main`, but exactly the commit that is recorded in `app/tool_version.json` and tested with this app (fields `repo`, `commit`, `tested_on`, `note`).

- **Install**: Git route with `git init`, `git fetch --depth 1 origin <commit>` and `git checkout --detach FETCH_HEAD` (fallback: full clone and checkout of the commit). Without git, the app downloads the ZIP of that commit and records the commit in the marker `.steuerauszug-assistent.json`.
- **Tool aktualisieren** (Update tool) brings the tool to this tested version. An existing clone (e.g. on branch `main`) stays intact: untracked files are kept, there is no `clean` and no `reset --hard`. The clone is then on a fixed commit (detached HEAD); the log shows the command to return to the branch (e.g. `git checkout main`). If the tool is already on the tested version, nothing happens; if it is newer, the app warns and does not downgrade (only via the API with `{"target": "tested", "force": true}`).
- **Status**: The "Tool" section shows the installed and the tested commit as well as the state (tested version / older than tested / newer than tested (untested) / deviating/unknown). The comparison runs locally via `git merge-base --is-ancestor`, without network access. If the tested commit is missing locally in a shallow clone, no statement is possible and "abweichend/unbekannt" (deviating/unknown) is shown.
- **Neueste Version (ungetestet)** (Latest version, untested): The unobtrusive button next to it (or `POST /api/tool/update` with `{"target": "latest"}`) fetches `main`. This can break the app; "Tool aktualisieren" takes you back to the tested version.
- **Raising the pin (developers)**: Enter the new commit SHA (40 characters) and `tested_on` in `app/tool_version.json`, run `.venv/Scripts/python.exe -m pytest -q` (Windows) or `.venv/bin/python -m pytest -q` (macOS/Linux), do one real run (fresh install or update, and generate a tax statement for a test year), and then republish the app (release).

## Development

Windows: `.venv\Scripts\python.exe -m pytest -q`

macOS/Linux: `.venv/bin/python -m pytest -q`

Optional regression tests with real Flex XML files: set the environment variable `OPENSTEUER_REAL_DATA_DIR` to a folder; all `*.xml` files in it are tested read-only. Without the variable or without files, these tests are skipped.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) (German).

## License

[MIT](LICENSE). opensteuerauszug itself is under its own license (also MIT, see there) and is not part of this repo; the app only calls it as a separate program.
