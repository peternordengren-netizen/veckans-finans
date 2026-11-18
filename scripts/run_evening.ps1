# Kvällens bygge (scripts/morning.py --kvall): nästa handelsdags kurser och kurssignaler, swinglistan och
# utvärderingens uppföljning, med dagens stängning. Morgonen tar sedan bara nyheter, insyn, rapportutfall, konsensus
# och det som saknas från kvällen.
#
# Anropas av run_update.ps1 efter eftermiddagskörningen (17:45) och av den schemalagda uppgiften VeckansFinansKvall
# 21:00 (nytt försök). Gör ingenting
# - på börsens stängda dagar eller före 17:45 (en uppgift som tas igen nästa morgon: morgonflödet tar det då),
# - när kvällens bygge redan är klart (data\morning\kvall\<nästa handelsdag>.json med "complete": true).
# Exit 2 från morning.py (dagens kurser saknas än hos Yahoo) loggas; 21:00 försöker igen, därefter morgonen.
# Bygger sidan när något skrevs (-Build). Logg i logs\evening\<datum>.log.
#
# Manuellt: powershell -NoProfile -File scripts\run_evening.ps1 [-Build]

param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Py = ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs\evening"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Today = Get-Date -Format "yyyy-MM-dd"
$RunLog = Join-Path $LogDir "$Today.log"
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

# Redan klart i kväll? (nästa handelsdags fil från kvällen med complete = true)
$check = & cmd.exe /c "$Py scripts\morning.py --kvall-klar 2>nul"
if ($LASTEXITCODE -eq 0) { Write-Output "Kvällens bygge är redan klart ($check) - inget att göra."; exit 0 }

Log "Kvällsbygge startar"
& cmd.exe /c "$Py scripts\morning.py --kvall 2>&1" | ForEach-Object { Log "  $_" }
$code = $LASTEXITCODE
Log ("morning.py --kvall: exit $code" + $(switch ($code) {
    2 { " - dagens kurser saknas än; nytt försök 21:00, annars tar morgonen det" }
    3 { " - ingen handelsdag eller före 17:45" }
    default { "" }
}))
if ($Build -and ($code -eq 0 -or $code -eq 2)) {
    $BuildLog = Join-Path $LogDir "$Today-build.log"
    & cmd.exe /c "npm run build > `"$BuildLog`" 2>&1"
    Log "npm run build: exit $LASTEXITCODE (logg: $BuildLog)"
}
Log "Klart"
exit 0
