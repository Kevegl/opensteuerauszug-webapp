param(
    [switch]$DryRun,
    [switch]$AssumeYes,
    [switch]$AssumeNo
)

# Hinweis: Datei bewusst nur mit ASCII-Zeichen (PowerShell 5.1 liest BOM-lose Dateien als ANSI).
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$MinVersion = [version]"3.11"
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

function Write-Info($msg) { Write-Host $msg }
function Write-DryInfo($msg) { Write-Host "[DryRun] $msg" }

function Stop-Script($msg) {
    Write-Host $msg
    exit 1
}

function Ask-YesNo($question) {
    if ($AssumeYes) { Write-Host "$question J (automatisch)"; return $true }
    if ($AssumeNo) { Write-Host "$question N (automatisch)"; return $false }
    $answer = Read-Host $question
    return ($answer -match '^\s*[JjYy]')
}

# Fuehrt Python aus und liefert die Version (System.Version) oder $null bei Fehler/Timeout.
function Get-PythonVersion([string]$Exe, [string[]]$PreArgs = @()) {
    $script:LastTimeout = $false
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Exe
        $argList = @($PreArgs) + @('-c', '"import sys; print(''%d.%d'' % sys.version_info[:2])"')
        $psi.Arguments = ($argList -join ' ')
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        if (-not $proc.WaitForExit(15000)) {
            try { $proc.Kill() } catch { }
            $script:LastTimeout = $true
            return $null
        }
        $out = $proc.StandardOutput.ReadToEnd().Trim()
        if ($proc.ExitCode -ne 0) { return $null }
        if ($out -match '^(\d+)\.(\d+)$') { return [version]("{0}.{1}" -f $Matches[1], $Matches[2]) }
        return $null
    } catch {
        return $null
    }
}

# Liefert eine Liste von Kandidaten: @{ Exe = ...; Args = @(...); Label = ... }
function Get-Candidates {
    $list = @()
    if ($env:STEUERAUSZUG_PY_CANDIDATES) {
        foreach ($p in ($env:STEUERAUSZUG_PY_CANDIDATES -split ';')) {
            $p = $p.Trim()
            if ($p) { $list += @{ Exe = $p; Args = @(); Label = $p } }
        }
        return $list
    }
    foreach ($v in @('-3.13', '-3.12', '-3.11', '-3')) {
        $list += @{ Exe = 'py'; Args = @($v); Label = "py $v" }
    }
    $list += @{ Exe = 'python'; Args = @(); Label = 'python' }
    $list += @{ Exe = 'python3'; Args = @(); Label = 'python3' }
    return $list
}

# Sucht Python >= 3.11. Liefert @{ Exe; Args; Version; Label } oder $null.
function Find-Python {
    foreach ($c in (Get-Candidates)) {
        $exe = $c.Exe
        $cmds = @(Get-Command $exe -All -ErrorAction SilentlyContinue)
        if ($cmds.Count -eq 0) { continue }
        $seen = @{}
        foreach ($cmd in $cmds) {
            $resolved = $cmd.Source
            if (-not $resolved) { $resolved = $cmd.Path }
            if (-not $resolved) { continue }
            if ($seen.ContainsKey($resolved.ToLower())) { continue }
            $seen[$resolved.ToLower()] = $true
            # Microsoft-Store-Platzhalter ueberspringen (weitere PATH-Treffer werden trotzdem geprueft)
            if (($resolved -like '*\WindowsApps\*') -and ($exe -ne 'py')) {
                Write-Info "  Uebersprungen (Microsoft-Store-Platzhalter): $resolved"
                continue
            }
            $ver = Get-PythonVersion $resolved $c.Args
            if (-not $ver) {
                if ($script:LastTimeout) { Write-Info "  Ueberspringe $($c.Label): keine Antwort innerhalb von 15 s" }
                continue
            }
            if ($ver -lt $MinVersion) {
                Write-Info "  Zu alt: $($c.Label) ist Python $ver (benoetigt wird 3.11 oder neuer)"
                continue
            }
            return @{ Exe = $resolved; Args = $c.Args; Version = $ver; Label = $c.Label }
        }
    }
    return $null
}
function Test-Winget {
    if ($env:STEUERAUSZUG_NO_WINGET -eq '1') {
        Write-Info "winget-Nutzung ist per STEUERAUSZUG_NO_WINGET deaktiviert."
        return $false
    }
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Show-ManualHint {
    Write-Info "Bitte Python 3.12 manuell von https://www.python.org/downloads/ installieren."
    Write-Info "Dabei den Haken 'Add python.exe to PATH' setzen (bzw. den py-Launcher mitinstallieren)."
    Write-Info "Danach start.bat erneut starten."
}

# Installiert Python 3.12 per winget (Benutzerinstallation). Gibt $true bei Erfolg zurueck.
function Install-Python {
    if (-not (Test-Winget)) {
        Write-Info "Eine automatische Installation ist nicht moeglich."
        Show-ManualHint
        return $false
    }
    if ($DryRun) {
        Write-DryInfo "Wuerde installieren: winget install --id Python.Python.3.12 -e --scope user --silent --accept-source-agreements --accept-package-agreements"
        return $true
    }
    Write-Info "Installiere Python 3.12 per winget (Benutzerinstallation) ..."
    & winget install --id Python.Python.3.12 -e --scope user --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Die Installation von Python 3.12 ist fehlgeschlagen (winget Exit-Code $LASTEXITCODE)."
        Show-ManualHint
        return $false
    }
    return $true
}

# Findet Python, bietet bei Bedarf die Installation an. Beendet das Skript mit exit 1, wenn nichts gefunden wird.
function Resolve-Python {
    Write-Info "Suche Python 3.11 oder neuer ..."
    $py = Find-Python
    if ($py) { return $py }

    Write-Info "Python 3.11 oder neuer wurde nicht gefunden."
    $wingetOk = Test-Winget
    if (-not $wingetOk) {
        if ($env:STEUERAUSZUG_NO_WINGET -ne '1') { Write-Info "winget ist nicht verfuegbar." }
        Show-ManualHint
        if ($DryRun) { Write-DryInfo "Das Skript wuerde hier mit Fehler (exit 1) enden."; exit 1 }
        exit 1
    }
    if ($DryRun) {
        Write-DryInfo "Wuerde fragen: Python 3.12 jetzt automatisch installieren? (J/N)"
        [void](Install-Python)
        Write-DryInfo "Danach wuerde PATH neu geladen und erneut gesucht."
        exit 1
    }
    if (-not (Ask-YesNo "Python 3.12 jetzt automatisch installieren? (J/N)")) {
        Show-ManualHint
        exit 1
    }
    if (-not (Install-Python)) { exit 1 }

    Update-SessionPath
    $py = Find-Python
    if (-not $py -and $env:LOCALAPPDATA) {
        $direct = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        if (Test-Path $direct) {
            $ver = Get-PythonVersion $direct
            if ($ver -and $ver -ge $MinVersion) {
                $py = @{ Exe = $direct; Args = @(); Version = $ver; Label = $direct }
            }
        }
    }
    if (-not $py) {
        Write-Info "Python wurde installiert, ist aber in dieser Sitzung noch nicht auffindbar."
        Write-Info "Bitte das Fenster schliessen und start.bat erneut starten."
        exit 1
    }
    return $py
}

# Liefert 'ok', 'missing', 'broken' oder 'old'; setzt $script:VenvVersion.
function Test-Venv {
    $script:VenvVersion = $null
    if (-not (Test-Path $VenvPython)) { return 'missing' }
    $ver = Get-PythonVersion $VenvPython
    if (-not $ver) { return 'broken' }
    $script:VenvVersion = $ver
    if ($ver -lt $MinVersion) { return 'old' }
    return 'ok'
}

$Port = 8765
$BaseUrl = "http://127.0.0.1:$Port"

function Test-PortInUse {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(1000, $false) -and $client.Connected) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# Liefert das geparste JSON von /api/status oder $null.
function Get-AppStatus {
    try {
        $r = Invoke-WebRequest -Uri "$BaseUrl/api/status" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return ($r.Content | ConvertFrom-Json)
    } catch {
        return $null
    }
}

# Erkennt diese App (neue Versionen: Feld "app"; aeltere: typische Felder von /api/status).
function Test-IsOurApp($status) {
    if (-not $status) { return $false }
    $names = @($status.PSObject.Properties.Name)
    if ($status.app -eq 'steuerauszug-assistent') { return $true }
    return (($names -contains 'default_year') -and ($names -contains 'running_job') -and ($names -contains 'tool_repo'))
}

# ---------------- Hauptablauf ----------------

if ($DryRun) { Write-DryInfo "Trockenlauf: es wird nichts installiert oder gestartet." }

$state = Test-Venv
$py = $null

switch ($state) {
    'ok' {
        Write-Info "Vorhandene virtuelle Umgebung (.venv) ist in Ordnung (Python $script:VenvVersion)."
        if ($DryRun) {
            $py = Find-Python
            if ($py) { Write-DryInfo "System-Python gefunden: $($py.Label) = Python $($py.Version) ($($py.Exe))" }
            else { Write-DryInfo "Kein System-Python >= 3.11 gefunden (wird fuer das vorhandene .venv nicht benoetigt)." }
        }
    }
    'missing' {
        Write-Info "Keine virtuelle Umgebung (.venv) vorhanden."
    }
    'broken' {
        Write-Info "Die virtuelle Umgebung (.venv) ist defekt (python.exe startet nicht)."
    }
    'old' {
        Write-Info "Die virtuelle Umgebung (.venv) verwendet Python $script:VenvVersion (benoetigt wird 3.11 oder neuer)."
    }
}

if ($state -eq 'broken' -or $state -eq 'old') {
    if ($DryRun) {
        Write-DryInfo "Wuerde fragen: .venv loeschen und neu anlegen? (J/N)"
    } elseif (-not (Ask-YesNo "Soll .venv geloescht und neu angelegt werden? (J/N)")) {
        Stop-Script "Abgebrochen. Ohne funktionierende virtuelle Umgebung kann der Server nicht starten."
    } else {
        Write-Info "Loesche .venv ..."
        try { Remove-Item -Recurse -Force (Join-Path $PSScriptRoot ".venv") -ErrorAction Stop }
        catch { Stop-Script "Die virtuelle Umgebung konnte nicht geloescht werden. Bitte Programme schliessen, die .venv nutzen, und erneut versuchen." }
    }
}

if ($state -ne 'ok') {
    $py = Resolve-Python
    if ($DryRun) {
        Write-DryInfo "Python gefunden: $($py.Label) = Python $($py.Version) ($($py.Exe))"
        Write-DryInfo "Wuerde virtuelle Umgebung anlegen: $($py.Exe) $($py.Args -join ' ') -m venv .venv"
    } else {
        Write-Info "Verwende Python $($py.Version) ($($py.Label))"
        Write-Info "Lege virtuelle Umgebung an ..."
        & $py.Exe @($py.Args) -m venv .venv
        if ($LASTEXITCODE -ne 0) { Stop-Script "Die virtuelle Umgebung konnte nicht angelegt werden." }
    }
}

if ($DryRun) {
    Write-DryInfo "Wuerde ausfuehren: .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt"
    Write-DryInfo "Wuerde den Server auf http://127.0.0.1:8765 starten (STEUERAUSZUG_LAUNCHER=1) und den Browser oeffnen."
    Write-DryInfo "Endet der Server mit Exit-Code 3 (Neustart nach Update), wuerde das Skript die Abhaengigkeiten erneut installieren und den Server neu starten (ohne den Browser erneut zu oeffnen)."
    Write-DryInfo "Trockenlauf beendet."
    exit 0
}

Write-Info "Installiere Abhaengigkeiten ..."
& $VenvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Stop-Script "Die Abhaengigkeiten konnten nicht installiert werden. Bitte die Internetverbindung oder die Paketquelle pruefen und die pip-Ausgabe oben beachten. Danach start.bat erneut starten. Der Server wird nicht gestartet."
}

if (Test-PortInUse) {
    if (Test-IsOurApp (Get-AppStatus)) {
        Write-Info "Die App laeuft bereits auf $BaseUrl. Oeffne den Browser ..."
        Start-Process $BaseUrl
        exit 0
    }
    Write-Host "Der Port $Port ist bereits von einem anderen Programm belegt. Der Server wird nicht gestartet."
    Write-Host "Den belegenden Prozess finden Sie mit: Get-NetTCPConnection -LocalPort $Port"
    Write-Host "(die Spalte OwningProcess zeigt die Prozess-ID; Name per Get-Process -Id <ID>)."
    exit 1
}

$env:STEUERAUSZUG_LAUNCHER = "1"
$first = $true
while ($true) {
    Write-Info "Starte Server ..."
    $proc = Start-Process -FilePath $VenvPython -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port") -NoNewWindow -PassThru

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        if ($proc.HasExited) { break }
        if (Test-IsOurApp (Get-AppStatus)) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }

    if ($ready) {
        if ($first) {
            Write-Host "Server laeuft auf $BaseUrl (Strg+C zum Beenden)"
            Start-Process $BaseUrl
        } else {
            Write-Host "Server laeuft wieder auf $BaseUrl (die Seite laedt sich selbst neu)."
        }
    } elseif (-not $proc.HasExited) {
        Write-Host "Der Server antwortet nach 20 s noch nicht. Der Browser wird nicht geoeffnet; bitte die Ausgabe oben beachten."
    }

    $proc.WaitForExit()
    $code = $proc.ExitCode

    # 3 = Neustart nach Update (Anforderung aus der App)
    if ($code -eq 3) {
        Write-Host "Neustart nach Update ..."
        Write-Info "Installiere Abhaengigkeiten ..."
        & $VenvPython -m pip install --quiet -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            Stop-Script "Die Abhaengigkeiten konnten nicht installiert werden. Der Server wird nicht neu gestartet. Bitte start.bat erneut starten."
        }
        $first = $false
        continue
    }

    # 0 = normal beendet; -1073741510 = Beenden per Strg+C
    if ($code -ne 0 -and $code -ne -1073741510) {
        Write-Host "Der Server wurde unerwartet beendet (Exit-Code $code). Bitte die Meldungen oben beachten."
        exit $code
    }
    exit 0
}
