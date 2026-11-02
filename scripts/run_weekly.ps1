# Kör /sammanfatta-veckan för förra ISO-veckan med Claude Code utan interaktion och bygger om sidan.
#
# Körs av den schemalagda uppgiften VeckansFinansVeckosammanfattning varje måndag 08:30 (efter att
# Claudes kvot återställts 08:00 och efter morgonrapporten 08:10), samt fem minuter efter inloggning
# och upplåsning för att ta igen en missad körning.
# - Claude-modell: Opus (-Model för annan). W43 med Sonnet utelämnade stora rapporter och hela
#   telekomsektorn; rapportutfallen (run_results.ps1) körs med Sonnet.
# - Före Claude kompletteras veckans rådata med morgonflödets lokala hämtning
#   (scripts/merge_morning_raw.py), eftersom Actions-hämtningen har luckor. Därför väntar körningen
#   först på dagens morgonhämtning (högst 90 min), vilken utlösare som än startade den.
# - Högst två automatiska försök per vecka (räknas i <vecka>-run.log); -Week kringgår gränsen.
#   Ett stopp på grund av Claudes användningsgräns räknas inte: återställningstiden loggas, sidan
#   visar "Claudes användningsgräns nådd – försöker igen efter <tid>" och en engångsuppgift
#   (VeckansFinansOmforsokVecka) kör skriptet igen fem minuter efter återställningen.
# - Veckan räknas fram i Europe/Stockholm (förra veckan) om -Week inte anges.
# - Finns data/weeks/<vecka>.json redan avbryts körningen: en färdig vecka skrivs aldrig över.
# - Claude körs i läget dontAsk med en uttömmande lista över tillåtna verktyg (se $Allowed);
#   allt annat nekas automatiskt. PowerShell, webb och MCP-servrar är avstängda.
# - Loggar i logs/weekly/: <vecka>-run.log (förlopp), <vecka>.json (Claudes svar i JSON),
#   <vecka>.err (stderr), <vecka>-slutrapport.md (slutrapporten) och <vecka>-build.log.
# - Efter Claude skrivs signalöversikten (scripts/signals.py, säkerhetskopieras med
#   backup_week.py), rapportkalendern för veckan efter (scripts/report_calendar.py) och
#   swinghistoriken för fliken Swing (scripts/swing_history.py).
#
# Manuellt: powershell -NoProfile -File scripts\run_weekly.ps1 [-Week 2026-W42] [-DryRun]
# -DryRun kör allt utom själva sammanfattningen (Claude får ett ofarligt testprompt i stället).

param(
    [string]$Week = "",
    [switch]$DryRun,
    [string]$Model = "opus"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "claude_limit.ps1")
$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs\weekly"
New-Item -ItemType Directory -Force $LogDir | Out-Null
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not $Week) {
    $Week = & $Python -c "from datetime import datetime, timedelta; from zoneinfo import ZoneInfo; y, w, _ = (datetime.now(ZoneInfo('Europe/Stockholm')) - timedelta(days=7)).isocalendar(); print(f'{y}-W{w:02d}')"
}
if ($Week -notmatch '^\d{4}-W\d{2}$') { throw "Ogiltig vecka: '$Week'" }

# Testkörningar (-DryRun) skriver till egna filer så att en riktig körnings loggar och
# slutrapport aldrig skrivs över.
$LogName = if ($DryRun) { "$Week-dryrun" } else { $Week }
$RunLog = Join-Path $LogDir "$LogName-run.log"
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

# Uppgiften startas även vid inloggning och upplåsning (för att ta igen en missad måndag).
# Då är veckan oftast redan klar, så de här kontrollerna skriver bara till konsolen, inte
# till loggfilen, för att inte fylla den med en rad per upplåsning.
$WeekFile = Join-Path $Root "data\weeks\$Week.json"
if ((Test-Path $WeekFile) -and -not $DryRun) {
    Write-Output "data\weeks\$Week.json finns redan - inget att göra, en färdig vecka skrivs aldrig över."
    exit 0
}
# Högst MaxAttempts riktiga försök per vecka, så att ett återkommande fel (t.ex. utloggad CLI)
# inte startar en ny Claude-körning vid varje upplåsning. Fler försök: kör skriptet manuellt med -Week.
$MaxAttempts = 2
$LimitMarker = "Veckosammanfattning: $LimitMarkerSuffix"
$attempts = if (Test-Path $RunLog) {
    @(Select-String -Path $RunLog -SimpleMatch "Startar Claude Code: /sammanfatta-veckan").Count -
    @(Select-String -Path $RunLog -SimpleMatch $LimitMarker).Count
} else { 0 }
if ($attempts -ge $MaxAttempts -and -not $DryRun -and -not $PSBoundParameters.ContainsKey("Week")) {
    Write-Output "Redan $attempts försök för $Week - avbryter. Se $RunLog."
    exit 0
}

# Vänta på dagens morgonhämtning, oavsett vilken utlösare som startade körningen: inloggning eller
# upplåsning startar både morgonflödet och veckokörningen samtidigt, och kompletteringen av rådatan
# (merge_morning_raw.py) ska se morgonens hämtning. Klar = dagens morgonlogg har "morning.py: exit"
# (morning.py hämtar först och skriver sedan). Ingen handelsdag: ingen väntan. Högst MaxWaitMinutes,
# sedan fortsätter körningen med en rad i loggen (uppgiftens tidsgräns är två timmar).
$MaxWaitMinutes = 90
if (-not $DryRun) {
    & $Python scripts\afternoon.py --handelsdag
    if ($LASTEXITCODE -eq 0) {
        $MorningLog = Join-Path $Root ("logs\morning\{0:yyyy-MM-dd}.log" -f (Get-Date))
        $deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
        $waited = $false
        while (-not ((Test-Path $MorningLog) -and (Select-String -Path $MorningLog -SimpleMatch "morning.py: exit" -Quiet))) {
            if ((Get-Date) -ge $deadline) {
                Log "Morgonhämtningen är inte klar efter $MaxWaitMinutes min - fortsätter ändå."
                break
            }
            if (-not $waited) { Log "Väntar på dagens morgonhämtning ($MorningLog)"; $waited = $true }
            Start-Sleep -Seconds 30
        }
        if ($waited) { Log "Morgonhämtningen klar - fortsätter." }
    }
}

Log "Start, vecka $Week$(if ($DryRun) { ' (DryRun)' })"

$Claude = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
if (-not (Test-Path $Claude)) {
    Log "Hittar inte den fristående Claude Code-CLI:n ($Claude) - avbryter."
    exit 1
}

# Claude Code-variabler från en eventuell värdprocess (t.ex. skrivbordsappen) får inte ärvas:
# då försöker CLI:n använda värdens inloggning i stället för den egna.
foreach ($name in @(Get-ChildItem env: | Where-Object { $_.Name -match '^(CLAUDE|ANTHROPIC)' } | ForEach-Object Name)) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}

# Via cmd så att gits stderr inte blir ett avbrytande PowerShell-fel; ett misslyckat pull loggas bara.
& cmd.exe /c "git -C data/raw pull -q 2>&1" | ForEach-Object { Log "git pull: $_" }
Log "git -C data/raw pull: exit $LASTEXITCODE"

# Komplettera med morgonflödets lokala hämtning (rapporter som Actions missade). Fel här stoppar inte.
if (-not $DryRun) {
    & cmd.exe /c ".venv\Scripts\python.exe scripts\merge_morning_raw.py $Week 2>&1" | ForEach-Object { Log "komplettering: $_" }
    Log "merge_morning_raw.py: exit $LASTEXITCODE"
}

# Uttömmande lista. Edit-regeln gäller även Write. Bash-regler med :* är prefix; kedjade
# kommandon (&&, ;, |) kontrolleras del för del och nekas om någon del saknas här.
$Allowed = @(
    "Read", "Glob", "Grep",
    "Edit(./data/weeks/**)",
    "Bash(git -C data/raw pull)",
    "Bash(.venv/Scripts/python scripts/index_overview.py:*)",
    "Bash(.venv/Scripts/python scripts/validate.py:*)",
    "Bash(.venv/Scripts/python scripts/prices.py:*)",
    "Bash(.venv/Scripts/python scripts/registers.py:*)",
    "Bash(.venv/Scripts/python scripts/backup_week.py:*)"
)
$Disallowed = @("PowerShell", "WebFetch", "WebSearch")

$Prompt = if ($DryRun) {
    "Det här är ett test av den schemalagda körningen. Kör Bash-kommandot .venv/Scripts/python scripts/index_overview.py $Week och svara med dess första rad. Skriv ingenting."
} else {
    "/sammanfatta-veckan $Week"
}

function Quote([string]$s) { '"' + ($s -replace '"', '\"') + '"' }
$ArgList = @("-p", (Quote $Prompt), "--model", $Model, "--permission-mode", "dontAsk", "--allowedTools") +
    ($Allowed | ForEach-Object { Quote $_ }) +
    @("--disallowedTools") + ($Disallowed | ForEach-Object { Quote $_ }) +
    @("--strict-mcp-config", "--no-session-persistence", "--output-format", "json")

$JsonOut = Join-Path $LogDir "$LogName.json"
$ErrOut = Join-Path $LogDir "$LogName.err"
Log "Startar Claude Code: $Prompt (modell $Model)"
$proc = Start-Process -FilePath $Claude -ArgumentList $ArgList -NoNewWindow -PassThru -Wait `
    -RedirectStandardOutput $JsonOut -RedirectStandardError $ErrOut
Log "Claude Code klar: exit $($proc.ExitCode)"

# Användningsgränsen: räknas inte som försök; nytt försök schemaläggs efter återställningen.
if (-not $DryRun) {
    $limit = Test-ClaudeLimit $JsonOut "veckosammanfattning $Week"
    if ($limit.Limit) {
        $limit.Lines | ForEach-Object { Log $_ }
        Log $LimitMarker
        Log (Register-ClaudeRetry "VeckansFinansOmforsokVecka" "scripts\run_weekly.ps1" "" $limit.ResetAt)
    }
}

$Report = Join-Path $LogDir "$LogName-slutrapport.md"
try {
    $result = Get-Content $JsonOut -Raw -Encoding UTF8 | ConvertFrom-Json
    $denied = @($result.permission_denials | ForEach-Object {
        $in = $_.tool_input
        $what = if ($in.command) { $in.command } elseif ($in.file_path) { $in.file_path } else { ($in | ConvertTo-Json -Compress) }
        "- $($_.tool_name): $what"
    })
    $text = "# Slutrapport $Week`n`n$($result.result)`n`n---`n" +
        "Fel: $($result.is_error), turer: $($result.num_turns), tid: $([math]::Round($result.duration_ms / 60000, 1)) min`n" +
        "Nekade verktygsanrop: $(if ($denied.Count) { "`n" + ($denied -join "`n") } else { 'inga' })`n"
    [IO.File]::WriteAllText($Report, $text, (New-Object Text.UTF8Encoding $true))
    Log "Slutrapport: $Report (nekade anrop: $($denied.Count))"
} catch {
    Log "Kunde inte läsa Claudes svar ($JsonOut): $_"
}

# Signalöversikten (fliken "Signaler") bygger på veckofilen och analysfilen, och
# säkerhetskopieras sedan till det privata repot tillsammans med dem.
if (Test-Path $WeekFile) {
    # Kurs mot vinst först: signalen "P/E +30 % i år" bygger på den.
    & cmd.exe /c ".venv\Scripts\python.exe scripts\valuation.py $Week 2>&1" |
        Where-Object { $_ -match "^(Kurs mot vinst|Ingen|Filen)" } | ForEach-Object { Log "kurs mot vinst: $_" }
    Log "valuation.py ${Week}: exit $LASTEXITCODE"
    & cmd.exe /c ".venv\Scripts\python.exe scripts\signals.py $Week 2>&1" | ForEach-Object { Log "signaler: $_" }
    Log "signals.py ${Week}: exit $LASTEXITCODE"
    if (-not $DryRun) {
        & cmd.exe /c ".venv\Scripts\python.exe scripts\backup_week.py $Week 2>&1" | Select-Object -Last 1 | ForEach-Object { Log "$_" }
    }
}

# Rapportutfall (fliken "Rapporterade bolag"): /rapportutfall med Sonnet i omgångar, egna
# begränsade behörigheter, se scripts/run_results.ps1. Inte vid -DryRun.
if ((Test-Path $WeekFile) -and -not $DryRun) {
    & powershell.exe -NoProfile -File (Join-Path $PSScriptRoot "run_results.ps1") -Week $Week |
        ForEach-Object { Log "rapportutfall: $_" }
}

# Rapportkalendern för veckan efter (visas på veckans sida under "Kommande vecka").
# Deterministiskt skript utanför Claude; fel här stoppar inte bygget.
$NextWeek = & $Python -c "import sys; from datetime import date, timedelta; y, w = sys.argv[1].split('-W'); n = date.fromisocalendar(int(y), int(w), 1) + timedelta(weeks=1); c = n.isocalendar(); print(f'{c[0]}-W{c[1]:02d}')" $Week
& cmd.exe /c ".venv\Scripts\python.exe scripts\report_calendar.py $NextWeek 2>&1" | ForEach-Object { Log "kalender: $_" }
Log "report_calendar.py ${NextWeek}: exit $LASTEXITCODE"

# Swinghistoriken (fliken Swing): räknas om på det senaste årets dagsdata, ett batchanrop till Yahoo.
# Deterministiskt skript utanför Claude; fel här stoppar inte bygget.
if (-not $DryRun) {
    & cmd.exe /c ".venv\Scripts\python.exe scripts\swing_history.py 2>&1" |
        Where-Object { $_ -match '^Swinghistorik' } | ForEach-Object { Log "swing: $_" }
    Log "swing_history.py: exit $LASTEXITCODE"
}

$BuildLog = Join-Path $LogDir "$LogName-build.log"
& cmd.exe /c "npm run build > `"$BuildLog`" 2>&1"
Log "npm run build: exit $LASTEXITCODE (logg: $BuildLog)"
Log "Klart"
