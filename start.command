#!/usr/bin/env bash
# Starter fuer macOS (Doppelklick im Finder) und Linux. Entspricht start.ps1 unter Windows.
# Hinweis: Datei bewusst nur mit ASCII-Zeichen.
#
# Optionen: --dry-run (nur anzeigen), --yes (Fragen mit J beantworten), --no (Fragen mit N beantworten)
# Umgebung: STEUERAUSZUG_PY_CANDIDATES (durch ':' getrennte Python-Kandidaten, ersetzt die Liste),
#           STEUERAUSZUG_NO_BREW=1 (kein Homebrew nutzen), STEUERAUSZUG_NO_BROWSER=1 (Browser nicht oeffnen)
set -u

DRY_RUN=0
ASSUME_YES=0
ASSUME_NO=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --yes) ASSUME_YES=1 ;;
        --no) ASSUME_NO=1 ;;
        *) echo "Unbekannte Option: $arg (erlaubt: --dry-run, --yes, --no)"; exit 2 ;;
    esac
done

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"

MIN_MAJOR=3
MIN_MINOR=11
VENV_PY="$ROOT/.venv/bin/python"
PORT=8765
BASE_URL="http://127.0.0.1:$PORT"
RESTART_CODE=3

info() { echo "$*"; }
dry_info() { echo "[DryRun] $*"; }
stop_script() { echo "$*"; exit 1; }

ask_yes_no() {
    if [ "$ASSUME_YES" = 1 ]; then echo "$1 J (automatisch)"; return 0; fi
    if [ "$ASSUME_NO" = 1 ]; then echo "$1 N (automatisch)"; return 1; fi
    local answer=""
    printf '%s ' "$1"
    read -r answer || answer=""
    case "$answer" in
        [JjYy]*) return 0 ;;
        *) return 1 ;;
    esac
}

# run_timeout SEKUNDEN BEFEHL ... : gibt stdout aus, Exit 124 bei Zeitueberschreitung (macOS hat kein timeout).
run_timeout() {
    local secs=$1; shift
    local out pid i=0 rc
    out="$(mktemp)"
    "$@" >"$out" 2>/dev/null &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$i" -ge $((secs * 10)) ]; then
            kill -9 "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null
            rm -f "$out"
            return 124
        fi
        sleep 0.1
        i=$((i + 1))
    done
    wait "$pid"
    rc=$?
    cat "$out"
    rm -f "$out"
    return $rc
}

# Gibt "MAJOR.MINOR" aus; Rueckgabe 0 = ok, 1 = Fehler, 124 = Zeitueberschreitung.
python_version() {
    local out rc
    out="$(run_timeout 15 "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    rc=$?
    [ "$rc" -ne 0 ] && return "$rc"
    case "$out" in
        [0-9]*.[0-9]*) echo "$out"; return 0 ;;
    esac
    return 1
}

version_ok() {
    local major="${1%%.*}" minor="${1##*.}"
    [ "$major" -gt "$MIN_MAJOR" ] || { [ "$major" -eq "$MIN_MAJOR" ] && [ "$minor" -ge "$MIN_MINOR" ]; }
}

have_xcode_clt() {
    # Ohne Xcode Command Line Tools oeffnen /usr/bin/python3 und /usr/bin/git einen Installationsdialog.
    command -v xcode-select >/dev/null 2>&1 || return 0   # kein macOS: kein Stub
    xcode-select -p >/dev/null 2>&1
}

candidate_list() {
    if [ -n "${STEUERAUSZUG_PY_CANDIDATES:-}" ]; then
        printf '%s\n' "$STEUERAUSZUG_PY_CANDIDATES" | tr ':' '\n'
        return
    fi
    printf '%s\n' python3.13 python3.12 python3.11 python3 \
        /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12 \
        /usr/local/bin/python3 /usr/local/bin/python3.12 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3
}

# Sucht Python >= 3.11. Setzt PY_EXE und PY_VERSION; Rueckgabe 0 bei Fund.
find_python() {
    local cand resolved ver rc seen=" "
    PY_EXE=""
    PY_VERSION=""
    while IFS= read -r cand; do
        [ -n "$cand" ] || continue
        case "$cand" in
            /*) [ -x "$cand" ] && resolved="$cand" || continue ;;
            *) resolved="$(command -v "$cand" 2>/dev/null)" || continue ;;
        esac
        [ -n "$resolved" ] || continue
        case "$seen" in *" $resolved "*) continue ;; esac
        seen="$seen$resolved "
        if [ "$resolved" = "/usr/bin/python3" ] && ! have_xcode_clt; then
            info "  Uebersprungen (Apple-Platzhalter ohne Command Line Tools): $resolved"
            continue
        fi
        ver="$(python_version "$resolved")"
        rc=$?
        if [ "$rc" -ne 0 ]; then
            [ "$rc" -eq 124 ] && info "  Ueberspringe $cand: keine Antwort innerhalb von 15 s"
            continue
        fi
        if ! version_ok "$ver"; then
            info "  Zu alt: $cand ist Python $ver (benoetigt wird 3.11 oder neuer)"
            continue
        fi
        PY_EXE="$resolved"
        PY_VERSION="$ver"
        return 0
    done < <(candidate_list)
    return 1
}

find_brew() {
    if [ "${STEUERAUSZUG_NO_BREW:-}" = "1" ]; then return 1; fi
    command -v brew 2>/dev/null && return 0
    local b
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$b" ] && { echo "$b"; return 0; }
    done
    return 1
}

show_manual_hint() {
    info "Bitte Python 3.12 manuell von https://www.python.org/downloads/ installieren (macOS-Installer 3.12)."
    info "Danach start.command erneut starten."
}

# Findet Python, bietet bei Bedarf die Installation per Homebrew an. Beendet mit exit 1, wenn nichts gefunden wird.
resolve_python() {
    info "Suche Python 3.11 oder neuer ..."
    if find_python; then return 0; fi

    info "Python 3.11 oder neuer wurde nicht gefunden."
    local brew
    if ! brew="$(find_brew)"; then
        if [ "${STEUERAUSZUG_NO_BREW:-}" = "1" ]; then
            info "Homebrew-Nutzung ist per STEUERAUSZUG_NO_BREW deaktiviert."
        else
            info "Homebrew (brew) ist nicht verfuegbar."
        fi
        show_manual_hint
        [ "$DRY_RUN" = 1 ] && dry_info "Das Skript wuerde hier mit Fehler (exit 1) enden."
        exit 1
    fi
    if [ "$DRY_RUN" = 1 ]; then
        dry_info "Wuerde fragen: Python 3.12 jetzt automatisch installieren? (J/N)"
        dry_info "Wuerde installieren: $brew install python@3.12"
        dry_info "Danach wuerde erneut gesucht."
        exit 1
    fi
    if ! ask_yes_no "Python 3.12 jetzt per Homebrew installieren? (J/N)"; then
        show_manual_hint
        exit 1
    fi
    info "Installiere Python 3.12 per Homebrew ..."
    if ! "$brew" install python@3.12; then
        info "Die Installation von Python 3.12 ist fehlgeschlagen."
        show_manual_hint
        exit 1
    fi
    if ! find_python; then
        info "Python wurde installiert, ist aber in dieser Sitzung noch nicht auffindbar."
        info "Bitte das Fenster schliessen und start.command erneut starten."
        exit 1
    fi
}

# Setzt VENV_STATE (ok | missing | broken | old) und VENV_VERSION.
test_venv() {
    VENV_VERSION=""
    if [ ! -e "$VENV_PY" ]; then VENV_STATE=missing; return; fi
    local ver
    if ! ver="$(python_version "$VENV_PY")"; then VENV_STATE=broken; return; fi
    VENV_VERSION="$ver"
    if version_ok "$ver"; then VENV_STATE=ok; else VENV_STATE=old; fi
}

port_in_use() {
    (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null
}

# Gibt den Inhalt von /api/status aus (leer bei Fehler).
app_status_json() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS -m 3 "$BASE_URL/api/status" 2>/dev/null
    elif [ -x "$VENV_PY" ]; then
        "$VENV_PY" -c "import urllib.request,sys; sys.stdout.write(urllib.request.urlopen('$BASE_URL/api/status', timeout=3).read().decode())" 2>/dev/null
    fi
}

is_our_app() {
    local body
    body="$(app_status_json)" || return 1
    [ -n "$body" ] || return 1
    case "$body" in
        *'"app":"steuerauszug-assistent"'*|*'"app": "steuerauszug-assistent"'*) return 0 ;;
    esac
    # aeltere Versionen: typische Felder von /api/status
    case "$body" in
        *'"default_year"'*'"running_job"'*|*'"running_job"'*'"default_year"'*) return 0 ;;
    esac
    return 1
}

open_browser() {
    if [ "${STEUERAUSZUG_NO_BROWSER:-}" = "1" ]; then
        info "Browser-Start uebersprungen (STEUERAUSZUG_NO_BROWSER=1). Adresse: $1"
    elif command -v open >/dev/null 2>&1; then
        open "$1" >/dev/null 2>&1 || info "Bitte $1 im Browser oeffnen."
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1" >/dev/null 2>&1 || info "Bitte $1 im Browser oeffnen."
    else
        info "Bitte $1 im Browser oeffnen."
    fi
}

install_requirements() {
    "$VENV_PY" -m pip install --quiet -r requirements.txt
}

# ---------------- Hauptablauf ----------------

[ "$DRY_RUN" = 1 ] && dry_info "Trockenlauf: es wird nichts installiert oder gestartet."

test_venv
case "$VENV_STATE" in
    ok)
        info "Vorhandene virtuelle Umgebung (.venv) ist in Ordnung (Python $VENV_VERSION)."
        if [ "$DRY_RUN" = 1 ]; then
            if find_python; then
                dry_info "System-Python gefunden: Python $PY_VERSION ($PY_EXE)"
            else
                dry_info "Kein System-Python >= 3.11 gefunden (wird fuer das vorhandene .venv nicht benoetigt)."
            fi
        fi
        ;;
    missing) info "Keine virtuelle Umgebung (.venv) vorhanden." ;;
    broken) info "Die virtuelle Umgebung (.venv) ist defekt (python startet nicht)." ;;
    old) info "Die virtuelle Umgebung (.venv) verwendet Python $VENV_VERSION (benoetigt wird 3.11 oder neuer)." ;;
esac

if [ "$VENV_STATE" = broken ] || [ "$VENV_STATE" = old ]; then
    if [ "$DRY_RUN" = 1 ]; then
        dry_info "Wuerde fragen: .venv loeschen und neu anlegen? (J/N)"
    elif ! ask_yes_no "Soll .venv geloescht und neu angelegt werden? (J/N)"; then
        stop_script "Abgebrochen. Ohne funktionierende virtuelle Umgebung kann der Server nicht starten."
    else
        info "Loesche .venv ..."
        rm -rf "$ROOT/.venv" || stop_script "Die virtuelle Umgebung konnte nicht geloescht werden. Bitte Programme schliessen, die .venv nutzen, und erneut versuchen."
    fi
fi

if [ "$VENV_STATE" != ok ]; then
    resolve_python
    if [ "$DRY_RUN" = 1 ]; then
        dry_info "Python gefunden: Python $PY_VERSION ($PY_EXE)"
        dry_info "Wuerde virtuelle Umgebung anlegen: $PY_EXE -m venv .venv"
    else
        info "Verwende Python $PY_VERSION ($PY_EXE)"
        info "Lege virtuelle Umgebung an ..."
        "$PY_EXE" -m venv .venv || stop_script "Die virtuelle Umgebung konnte nicht angelegt werden."
    fi
fi

if [ "$DRY_RUN" = 1 ]; then
    dry_info "Wuerde ausfuehren: .venv/bin/python -m pip install --quiet -r requirements.txt"
    dry_info "Wuerde den Server auf $BASE_URL starten (STEUERAUSZUG_LAUNCHER=1) und den Browser oeffnen."
    dry_info "Endet der Server mit Exit-Code $RESTART_CODE (Neustart nach Update), wuerde das Skript die Abhaengigkeiten erneut installieren und den Server neu starten (ohne den Browser erneut zu oeffnen)."
    dry_info "Trockenlauf beendet."
    exit 0
fi

info "Installiere Abhaengigkeiten ..."
install_requirements || stop_script "Die Abhaengigkeiten konnten nicht installiert werden. Bitte die Internetverbindung oder die Paketquelle pruefen und die pip-Ausgabe oben beachten. Danach start.command erneut starten. Der Server wird nicht gestartet."

if port_in_use; then
    if is_our_app; then
        info "Die App laeuft bereits auf $BASE_URL. Oeffne den Browser ..."
        open_browser "$BASE_URL"
        exit 0
    fi
    echo "Der Port $PORT ist bereits von einem anderen Programm belegt. Der Server wird nicht gestartet."
    echo "Den belegenden Prozess finden Sie mit: lsof -nP -iTCP:$PORT -sTCP:LISTEN"
    lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null
    exit 1
fi

SERVER_PID=""

stop_server() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -TERM "$SERVER_PID" 2>/dev/null
        local i=0
        while kill -0 "$SERVER_PID" 2>/dev/null && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
        kill -KILL "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
    fi
    SERVER_PID=""
}

on_signal() {
    echo
    echo "Beende Server ..."
    stop_server
    exit 0
}
trap on_signal INT TERM

FIRST=1
while true; do
    info "Starte Server ..."
    STEUERAUSZUG_LAUNCHER=1 "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
    SERVER_PID=$!

    ready=0
    for _ in $(seq 1 40); do
        kill -0 "$SERVER_PID" 2>/dev/null || break
        if is_our_app; then ready=1; break; fi
        sleep 0.5
    done

    if [ "$ready" = 1 ]; then
        if [ "$FIRST" = 1 ]; then
            echo "Server laeuft auf $BASE_URL (Strg+C zum Beenden)"
            open_browser "$BASE_URL"
        else
            echo "Server laeuft wieder auf $BASE_URL (die Seite laedt sich selbst neu)."
        fi
    elif kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "Der Server antwortet nach 20 s noch nicht. Der Browser wird nicht geoeffnet; bitte die Ausgabe oben beachten."
    fi

    wait "$SERVER_PID"
    code=$?
    SERVER_PID=""

    if [ "$code" -eq "$RESTART_CODE" ]; then
        echo "Neustart nach Update ..."
        info "Installiere Abhaengigkeiten ..."
        install_requirements || stop_script "Die Abhaengigkeiten konnten nicht installiert werden. Der Server wird nicht neu gestartet. Bitte start.command erneut starten."
        FIRST=0
        continue
    fi
    # 0 = normal beendet; 130/143 = Strg+C bzw. SIGTERM
    if [ "$code" -ne 0 ] && [ "$code" -ne 130 ] && [ "$code" -ne 143 ]; then
        echo "Der Server wurde unerwartet beendet (Exit-Code $code). Bitte die Meldungen oben beachten."
        exit "$code"
    fi
    exit 0
done
