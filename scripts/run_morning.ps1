# Morgonkörningen: morgonrapport (scripts/morning.py, utan Claude), Yahoos konsensus, rapportutfall
# för rapporter sedan förra morgonen (/rapportutfall med Sonnet) och bygget av sidan.
#
# Körs av den schemalagda uppgiften VeckansFinansMorgonrapport vardagar 08:10, samt fem
# minuter efter inloggning och upplåsning för att ta igen en missad morgon.
# - Före 08:10 gör skriptet ingenting: en inloggning eller upplåsning tidigt på morgonen skulle
#   annars låsa dagens rapportfönster innan morgonens rapporter (07:00-08:00) publicerats.
#   Körningen 08:10 (eller nästa inloggning/upplåsning efter 08:10) tar hand om dagen.
# Stegen körs bara om deras fil saknas, så skriptet kan köras om och tar då bara det som fattas:
#   1. data/morning/<datum>.json        morning.py (ingen handelsdag: avslutar utan fil)
#   2. data/consensus/<datum>.json      consensus.py --backup (märker bolag som redan rapporterat)
#   3. data/results/morning/<datum>.json run_results.ps1 <datum>; fallerar Claude byggs sidan ändå.
#      Saknar filen en omgång (underkänd två gånger i run_results.ps1) görs steget om vid nästa
#      utlösare, inom gränsen för försök nedan.
#   3b. Handelsbanken-rapporter i data\hb_manual\ (run_hb.ps1): en ny fil sammanfattas en gång; körs vid varje
#      utlösare, också när morgonkörningen redan är klar
#   3c. Veckans text om Swing-utvärderingen (run_swing_note.ps1, Claude): veckans första handelsdag när texten
#      saknas (normalt måndag), högst två försök; också när morgonkörningen redan är klar. Själva
#      utvärderingen (ögonblicksbild och uppföljning) görs av morning.py utan Claude.
#   4. npm run build                    om något steg kördes
# - Saknas förra handelsdagens kurser eller nätverket skrivs morgonrapporten ändå, men med "retry"
#   (morning.py exit 2), felet loggas och nästa utlösare gör om den. Samma för förra dagens
#   kursreaktion (afternoon.py --forra-om-saknas). Utan nätverk väntar konsensus och rapportutfall.
# - Saknas förra handelsdagens kurser (Yahoo har dem ofta inte 08:10) hämtas bara kurserna igen
#   (morning.py --bara-kurser: kursdelen och Swing) var tionde minut till 09:10, med bygge efter varje
#   försök. Därefter står sidan kvar med dagsdatans eget datum och en varning. En senare utlösare med
#   bara "kurser" kvar gör också bara --bara-kurser (inom gränsen för försök).
# - Högst två försök per steg och dag (räknas i loggen), fyra för morgonrapporten och kursreaktionen
#   (ingen Claude), så att ett fel inte ger nya hämtningar eller Claude-körningar vid varje upplåsning.
# - Logg i logs/morning/<datum>.log, bygget i logs/morning/<datum>-build.log.
#
# Manuellt: powershell -NoProfile -File scripts\run_morning.ps1
# -Now "2026-10-21 07:00" anger klockslaget för kontrollen mot 08:10 (för test).

param(
    [datetime]$Now = (Get-Date)
)

$ErrorActionPreference = "Stop"
$EarliestStart = [timespan]"08:09"  # en minuts marginal, så att utlösaren 08:10 aldrig stoppas
if ($Now.TimeOfDay -lt $EarliestStart) {
    Write-Output ("{0:HH:mm} är före 08:10 - morgonflödet körs inte än (körningen 08:10 tar hand om dagen)." -f $Now)
    exit 0
}
$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs\morning"
New-Item -ItemType Directory -Force $LogDir | Out-Null
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$Today = Get-Date -Format "yyyy-MM-dd"
$Report = Join-Path $Root "data\morning\$Today.json"
$Consensus = Join-Path $Root "data\consensus\$Today.json"
$Results = Join-Path $Root "data\results\morning\$Today.json"
$RunLog = Join-Path $LogDir "$Today.log"
$MaxAttempts = 2
# Morgonrapporten och kursreaktionen använder inte Claude (en hämtning per körning), så de får fler
# försök när kurser eller nätverket saknades.
$MaxFetchAttempts = 4

function Retry([string]$File) {
    # "retry" i filen: varför den är ofullständig ("kurser", "nätverk"); tom eller saknas = klar.
    try { return @((Get-Content $File -Raw -Encoding UTF8 | ConvertFrom-Json).retry | Where-Object { $_ }) }
    catch { return @("oläslig") }
}

function Attempts([string]$Marker) {
    if (Test-Path $RunLog) { @(Select-String -Path $RunLog -SimpleMatch $Marker).Count } else { 0 }
}
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

function ResultsComplete {
    # Resultatfilen finns och saknar inga omgångar eller rapporter (en underkänd rapport listas i
    # "rejected"; ett nytt försök gör då bara om den).
    if (-not (Test-Path $Results)) { return $false }
    try {
        $r = Get-Content $Results -Raw -Encoding UTF8 | ConvertFrom-Json
        return @($r.missing_batches).Count -eq 0 -and @($r.rejected | Where-Object { $_ }).Count -eq 0
    }
    catch { return $false }
}
# Bygget (används av steg 3b och 4).
$BuildLog = Join-Path $LogDir "$Today-build.log"
function Build {
    & cmd.exe /c "npm run build > `"$BuildLog`" 2>&1"
    Log "npm run build: exit $LASTEXITCODE (logg: $BuildLog)"
}
# 3c. Veckans text om Swing-utvärderingen (inget körs när texten finns eller underlag saknas).
function SwingNote {
    if ((Attempts "Startar swing-text") -ge $MaxAttempts) { return $false }
    & cmd.exe /c ".venv\Scripts\python.exe scripts\swing_eval.py --behover-text > nul 2>&1"
    if ($LASTEXITCODE -ne 0) { return $false }
    # Loggraderna får inte bli funktionens returvärde (de står i loggfilen).
    Log "Startar swing-text" | Out-Null
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_swing_note.ps1") |
        ForEach-Object { Log "  $_" } | Out-Null
    return ($LASTEXITCODE -eq 10)
}
# 3b. Handelsbanken-rapporter som laddats ner för hand: körs vid varje utlösare (inget händer utan nya filer).
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_hb.ps1") | ForEach-Object { Log "  $_" }
$hbAdded = $LASTEXITCODE -eq 10

$reportIncomplete = -not (Test-Path $Report) -or @(Retry $Report).Count -gt 0
$needReport = $reportIncomplete -and (Attempts "Startar morning.py") -lt $MaxFetchAttempts
# Ett stopp på grund av Claudes användningsgräns räknas inte (run_results.ps1 schemalägger då själv
# ett nytt försök efter återställningen).
$needResults = -not (ResultsComplete) -and
    ((Attempts "Startar rapportutfall") - (Attempts "Rapportutfall: räknas inte som försök")) -lt $MaxAttempts
# Förra handelsdagens kursreaktion: saknas eller ofullständig (kurser saknades) -> nytt försök.
& cmd.exe /c ".venv\Scripts\python.exe scripts\afternoon.py --forra-om-saknas --klar > nul 2>&1"
$needReaction = $LASTEXITCODE -eq 4 -and (Attempts "Startar kursreaktion") -lt $MaxFetchAttempts
if (-not $needReport -and (Test-Path $Report) -and (Test-Path $Consensus) -and -not $needResults -and -not $needReaction) {
    if (SwingNote) { $hbAdded = $true }
    if ($hbAdded) { Build }
    Write-Output "Dagens morgonkörning är klar (eller har gjort sina försök) - inget att göra$(if ($hbAdded) { ' utöver Handelsbanken' })."
    exit 0
}
if (-not (Test-Path $Report) -and -not $needReport) {
    Write-Output "Redan $MaxFetchAttempts försök med morgonrapporten i dag - avbryter. Se $RunLog."
    exit 0
}
$ran = $hbAdded

# 1. Morgonrapporten. Via cmd så att stderr (t.ex. yfinance-varningar) inte blir PowerShell-fel.
#    Exit 2: skriven men ofullständig (kurser eller nätverket saknades) - görs om vid nästa utlösare.
# Saknas bara kurserna räcker det att hämta dem igen (ingen ny hämtning från MFN, Cision och FI).
$onlyPrices = (Test-Path $Report) -and ((@(Retry $Report) -join ",") -eq "kurser")
if ($needReport) {
    Log "Startar morning.py$(if ($onlyPrices) { ' --bara-kurser' })"
    $mArgs = if ($onlyPrices) { "--bara-kurser" } else { "" }
    & cmd.exe /c ".venv\Scripts\python.exe scripts\morning.py $mArgs 2>&1" | ForEach-Object { Log "  $_" }
    $code = $LASTEXITCODE
    Log ("morning.py: exit $code" + $(if ($code -eq 2) { " - FEL: ofullständig ($((Retry $Report) -join ', ')), nytt försök vid nästa inloggning eller upplåsning" } else { "" }))
    $ran = $true
}
if (-not (Test-Path $Report)) {
    Log "Ingen morgonrapport i dag (ingen handelsdag eller fel) - resten hoppas över."
    exit 0
}
# Utan nätverk hämtades inga rapporter: rapportutfallen väntar, annars låses dagens tidsfönster
# över rapporter som inte hämtats.
$noNetwork = @(Retry $Report) -contains "nätverk"
if ($noNetwork -and $needResults) {
    Log "FEL: nätverket saknas - konsensus och rapportutfall väntar till nästa inloggning eller upplåsning."
    $needResults = $false
}

# 2. Yahoos konsensus, en bild per handelsdag; bolag som redan rapporterat märks och används inte.
if (-not (Test-Path $Consensus) -and -not $noNetwork) {
    & cmd.exe /c ".venv\Scripts\python.exe scripts\consensus.py --backup 2>&1" |
        Where-Object { $_ -match '^(Konsensus|Säkerhetskopia|Ingen|Konsensusfilen)' } | ForEach-Object { Log "  $_" }
    Log "consensus.py: exit $LASTEXITCODE"
    $ran = $true
}

# 2b. Förra handelsdagens kursreaktion ("Kursreaktion" i morgonrapporten), om eftermiddagen missades
#     eller saknade kurser. Exit 2: kurser saknas fortfarande - nytt försök vid nästa utlösare.
if ($needReaction -and -not $noNetwork) {
    Log "Startar kursreaktion"
    & cmd.exe /c ".venv\Scripts\python.exe scripts\afternoon.py --forra-om-saknas 2>&1" | ForEach-Object { Log "  $_" }
    $code = $LASTEXITCODE
    Log ("kursreaktion: exit $code" + $(if ($code -eq 2) { " - FEL: kurser saknas, nytt försök vid nästa inloggning eller upplåsning" } else { "" }))
    $ran = $true
}

# 3. Rapportutfall sedan förra morgonen. Fel här får aldrig stoppa bygget.
if ($needResults) {
    Log "Startar rapportutfall"
    try {
        & powershell.exe -NoProfile -File (Join-Path $PSScriptRoot "run_results.ps1") -Week $Today |
            ForEach-Object { Log "  $_" }
    } catch {
        Log "  rapportutfall fallerade: $_"
    }
    $ran = $true
}

# 3c. Veckans text om Swing-utvärderingen (efter morning.py, som skriver underlaget).
if (SwingNote) { $ran = $true }

# 4. Bygget.
if ($ran) { Build }

# 5. Gårdagens kurser saknas: hämta bara kurserna igen var tionde minut till 09:10 (räknas inte som
#    försök med morgonrapporten), och bygg efter varje försök. Sidan visar under tiden dagsdatans datum
#    med en varning.
$PriceDeadline = (Get-Date).Date.AddHours(9).AddMinutes(10)
while ((@(Retry $Report) -contains "kurser") -and (Get-Date).AddMinutes(10) -le $PriceDeadline) {
    Log "Gårdagens kurser saknas - nytt kursförsök om 10 minuter (senast 09:10)."
    Start-Sleep -Seconds 600
    Log "Startar kursförsök (morning.py --bara-kurser)"
    & cmd.exe /c ".venv\Scripts\python.exe scripts\morning.py --bara-kurser 2>&1" | ForEach-Object { Log "  $_" }
    Log "kursförsök: exit $LASTEXITCODE"
    Build
}
if (@(Retry $Report) -contains "kurser") {
    Log "FEL: gårdagens kurser saknas fortfarande - sidan visar dagsdatans datum med en varning."
}
# Anrop till MFN:s RSS föregående dygn (Actions och lokalt), ur körningsloggarna (scripts/mfn_calls.py).
& cmd.exe /c ".venv\Scripts\python.exe scripts\mfn_calls.py --dag $((Get-Date).AddDays(-1).ToString('yyyy-MM-dd')) 2>&1" |
    ForEach-Object { Log $_ }
Log "Klart"
