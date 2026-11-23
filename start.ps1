$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("-3.12", "-3")) {
            & py $v -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", $v) }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @("python") }
    return $null
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python wurde nicht gefunden. Bitte Python 3.12 von https://www.python.org installieren (py-Launcher oder PATH-Eintrag aktivieren) und start.bat erneut starten."
        exit 1
    }
    Write-Host "Lege virtuelle Umgebung an ..."
    if ($py.Count -gt 1) { & $py[0] $py[1] -m venv .venv } else { & $py[0] -m venv .venv }
    if ($LASTEXITCODE -ne 0) { Write-Host "Die virtuelle Umgebung konnte nicht angelegt werden."; exit 1 }
}
$vpy = ".venv\Scripts\python.exe"
& $vpy -m pip install --quiet -r requirements.txt
Start-Job -ScriptBlock { Start-Sleep -Seconds 3; Start-Process "http://127.0.0.1:8765" } | Out-Null
Write-Host "Server laeuft auf http://127.0.0.1:8765 (Strg+C zum Beenden)"
& $vpy -m uvicorn app.main:app --host 127.0.0.1 --port 8765
