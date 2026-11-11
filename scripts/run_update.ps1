# Eftermiddagsuppdateringen: lokal hämtning, rapportutfall sedan förra körningens fönster och dagens
# kursreaktion för de rapporterande bolagen, sedan bygget av sidan ("Eftermiddag" överst på startsidan).
#
# Körs av den schemalagda uppgiften VeckansFinansEftermiddag vardagar 17:45 (-Scheduled) och av
# skrivbordsgenvägen "Uppdatera nu" (-Open: öppnar sidan när den är klar).
#   1. scripts/fetch.py till data/morning/raw/ (MFN med 500 poster, ett anrop per källa)
#   2. run_results.ps1 <datum>T<hhmm>: eget tidsfönster från där förra körningen (morgon eller
#      uppdatering) slutade, så att ingen rapport räknas två gånger; Claude bara om fönstret har
#      rapporter. Fel här får aldrig stoppa resten.
#   3. scripts/afternoon.py: dagens kursreaktion -> data/afternoon/<datum>.json
#   3b. Handelsbanken-rapporter i data\hb_manual\ (run_hb.ps1), en ny fil sammanfattas en gång
#   4. npm run build
# - -Scheduled: gör ingenting på börsens stängda dagar, och inte heller före 17:45 (en körning
#   som tas igen nästa morgon efter att datorn varit avstängd – morgonflödet tar då hand om det).
#   Uppgiften startas också vid inloggning och upplåsning; då körs den bara om dagens kursreaktion
#   saknas, togs fram före stängning eller är ofullständig, högst fyra schemalagda gånger per dag.
# - Saknas nätverket (fetch.py: ingen källa svarade) avbryts körningen med ett FEL i loggen, utan
#   rapportutfall och kursreaktion. Saknas kurser skriver afternoon.py filen med "retry" (exit 2).
#   I båda fallen försöker nästa inloggning eller upplåsning igen (och nästa morgon morgonflödet).
# - Logg i logs/updates/<datum>.log, bygget i logs/updates/<datum>-build.log.
#
# Manuellt: powershell -NoProfile -File scripts\run_update.ps1 [-Open]
# -Now "2026-10-22 07:00" anger klockslaget för kontrollen mot 17:45 (för test).

param(
    [switch]$Scheduled,
    [switch]$Open,
    [datetime]$Now = (Get-Date)
)

$ErrorActionPreference = "Stop"
$EarliestScheduled = [timespan]"17:44"  # en minuts marginal, så att utlösaren 17:45 aldrig stoppas
if ($Scheduled -and $Now.TimeOfDay -lt $EarliestScheduled) {
    Write-Output ("{0:HH:mm} är före 17:45 - ingen eftermiddagsuppdatering (morgonflödet tar hand om rapporterna)." -f $Now)
    exit 0
}

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs\updates"
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Py = ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force $LogDir | Out-Null
$Today = Get-Date -Format "yyyy-MM-dd"
$Scope = "{0}T{1}" -f $Today, (Get-Date -Format "HHmm")
$RunLog = Join-Path $LogDir "$Today.log"
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

if ($Scheduled) {
    & $Py scripts\afternoon.py --handelsdag
    if ($LASTEXITCODE -eq 3) { Write-Output "Börsen är stängd i dag - ingen eftermiddagsuppdatering."; exit 0 }
    # Samma uppgift startas också vid inloggning och upplåsning: bara om dagens kursreaktion saknas,
    # togs fram före stängning eller saknade kurser/nätverk, och högst MaxScheduled gånger per dag.
    & $Py scripts\afternoon.py --klar
    if ($LASTEXITCODE -eq 0) { Write-Output "Dagens eftermiddagsuppdatering är klar - inget att göra."; exit 0 }
    $MaxScheduled = 4
    $done = if (Test-Path $RunLog) { @(Select-String -Path $RunLog -SimpleMatch "startar (schemalagd)").Count } else { 0 }
    if ($done -ge $MaxScheduled) { Write-Output "Redan $MaxScheduled schemalagda försök i dag - se $RunLog."; exit 0 }
}

Log "Uppdatering $Scope startar$(if ($Scheduled) { ' (schemalagd)' } else { '' })"

# 1. Lokal hämtning. Via cmd så att stderr inte blir PowerShell-fel.
& cmd.exe /c "$Py scripts\fetch.py --raw-dir data\morning\raw --mfn-limit 500 2>&1" | ForEach-Object { Log "  $_" }
$fetchCode = $LASTEXITCODE
Log "fetch.py: exit $fetchCode"
if ($fetchCode -ne 0) {
    # Ingen källa gick att nå. Rapportutfallen väntar (annars låses ett tidsfönster över rapporter som
    # inte hämtats), och kursreaktionen behöver också nätverket. Dagens fil lämnas som den är.
    Log "FEL: nätverket saknas (ingen källa svarade) - nytt försök vid nästa inloggning eller upplåsning."
    Log "Klart (avbrutet)"
    exit 0
}

# 2. Rapportutfall i uppdateringens fönster.
try {
    & powershell.exe -NoProfile -File (Join-Path $PSScriptRoot "run_results.ps1") -Week $Scope |
        ForEach-Object { Log "  $_" }
} catch {
    Log "  rapportutfall fallerade: $_"
}

# 3. Dagens kursreaktion.
& cmd.exe /c "$Py scripts\afternoon.py 2>&1" | ForEach-Object { Log "  $_" }
$code = $LASTEXITCODE
Log ("afternoon.py: exit $code" + $(if ($code -eq 2) { " - FEL: kurser saknas, nytt försök vid nästa inloggning eller upplåsning" } else { "" }))

# 3b. Handelsbanken-rapporter som laddats ner för hand (run_hb.ps1): en ny fil sammanfattas, bygget nedan visar den.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_hb.ps1") | ForEach-Object { Log "  $_" }

# 4. Bygget.
$BuildLog = Join-Path $LogDir "$Today-build.log"
& cmd.exe /c "npm run build > `"$BuildLog`" 2>&1"
Log "npm run build: exit $LASTEXITCODE (logg: $BuildLog)"
Log "Klart"

if ($Open) {
    & powershell.exe -NoProfile -File (Join-Path $PSScriptRoot "open_site.ps1")
}
