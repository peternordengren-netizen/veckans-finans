# Händelser i sektorn (scripts/events.py, punkt b): Claude Code (Sonnet, /handelser) bedömer varje händelse som
# "bolagsspecifik" eller "branschgemensam" med en menings motivering, märkt som tolkning; bedömningen kontrolleras och
# förs in i data/events/<datum>.json.
#
# Anropas av run_evening.ps1 efter kvällsbygget och av run_morning.ps1 (om kvällen missades), före bygget.
# - Ingen bedömning behövs (inga händelser, eller redan bedömda): inget körs, exit 0.
# - Claude körs i läget dontAsk: läsa, skriva bara i data/events/, och bara events.py --kontrollera.
#   Högst två försök per körning; godkänd -> events.py --klar, exit 10 (anroparen bygger).
# - Nås Claudes användningsgräns stannar skriptet; nästa körning försöker igen. Utan bedömning visar sidan
#   händelserna ändå, utan klass.
# - Logg i logs\events\<datum>.log.
#
# Manuellt: powershell -NoProfile -File scripts\run_events.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "claude_limit.ps1")
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Py = ".venv\Scripts\python.exe"

$LogDir = Join-Path $Root "logs\events"
New-Item -ItemType Directory -Force $LogDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Root "data\events") | Out-Null
$Today = Get-Date -Format "yyyy-MM-dd"
$RunLog = Join-Path $LogDir "$Today.log"
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

$Day = (& cmd.exe /c "$Py scripts\events.py --senaste 2>nul" | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or -not $Day) { exit 0 }
$Day = $Day.Trim()
& cmd.exe /c "$Py scripts\events.py --behover-bedomning $Day > nul 2>&1"
if ($LASTEXITCODE -ne 0) { exit 0 }

$Claude = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
if (-not (Test-Path $Claude)) { Log "Hittar inte Claude Code-CLI:n ($Claude) - avbryter."; exit 0 }
foreach ($name in @(Get-ChildItem env: | Where-Object { $_.Name -match '^(CLAUDE|ANTHROPIC)' } | ForEach-Object Name)) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
$Allowed = @("Read", "Glob", "Grep", "Edit(./data/events/**)", "Bash(.venv/Scripts/python scripts/events.py --kontrollera:*)")
$Disallowed = @("PowerShell", "WebFetch", "WebSearch")
function Quote([string]$s) { '"' + ($s -replace '"', '\"') + '"' }

Log "Händelser i sektorn: bedömning för $Day"
for ($attempt = 1; $attempt -le 2; $attempt++) {
    $ArgList = @("-p", (Quote "/handelser $Day"), "--model", "sonnet", "--permission-mode", "dontAsk",
        "--allowedTools") + ($Allowed | ForEach-Object { Quote $_ }) +
        @("--disallowedTools") + ($Disallowed | ForEach-Object { Quote $_ }) +
        @("--strict-mcp-config", "--no-session-persistence", "--output-format", "json")
    $JsonOut = Join-Path $LogDir "$Today-$attempt.json"
    $ErrOut = Join-Path $LogDir "$Today-$attempt.err"
    Log "Startar Claude Code (Sonnet), försök $attempt"
    try {
        $proc = Start-Process -FilePath $Claude -ArgumentList $ArgList -NoNewWindow -PassThru -Wait `
            -RedirectStandardOutput $JsonOut -RedirectStandardError $ErrOut
        $r = Get-Content $JsonOut -Raw -Encoding UTF8 | ConvertFrom-Json
        Log ("exit $($proc.ExitCode), turer $($r.num_turns), " +
            "$([math]::Round($r.duration_ms / 60000, 1)) min, nekade anrop $(@($r.permission_denials).Count)")
    } catch {
        Log "Claude-steget fallerade: $_"
    }
    $limit = Test-ClaudeLimit $JsonOut "Händelser $Day"
    if ($limit.Limit) {
        $limit.Lines | ForEach-Object { Log $_ }
        Log "Claudes användningsgräns - nästa körning försöker igen."
        exit 0
    }
    $out = @(& cmd.exe /c "$Py scripts\events.py --klar $Day 2>&1")
    $out | ForEach-Object { Log "  $_" }
    if ($LASTEXITCODE -eq 0) { Log "Bedömningen för $Day är godkänd och sparad"; exit 10 }
}
Log "Ingen godkänd bedömning för $Day i den här körningen"
exit 0
