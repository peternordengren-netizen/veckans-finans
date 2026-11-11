# Veckans text om Swing-utvärderingen (scripts/swing_eval.py, punkt 4): Claude Code (Sonnet, /swing-utvardering)
# skriver 5-8 meningar märkta som tolkning; texten kontrolleras och flyttas till rådatarepot.
#
# Anropas av run_morning.ps1 före bygget, på veckans första handelsdag (normalt måndag) när veckans text saknas.
# - Ingen text behövs (finns redan, eller inget underlag): inget körs, exit 0.
# - Claude körs i läget dontAsk: läsa, skriva bara i data/swing_eval/, och bara swing_eval.py --kontrollera.
#   Högst två försök per körning; godkänd -> swing_eval.py --klar (rådatarepot), exit 10 (anroparen bygger).
# - Nås Claudes användningsgräns stannar skriptet; nästa morgonkörning försöker igen.
# - Logg i logs\swing_eval\<datum>.log.
#
# Manuellt: powershell -NoProfile -File scripts\run_swing_note.ps1

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "claude_limit.ps1")
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Py = ".venv\Scripts\python.exe"

$LogDir = Join-Path $Root "logs\swing_eval"
New-Item -ItemType Directory -Force $LogDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Root "data\swing_eval") | Out-Null
$Today = Get-Date -Format "yyyy-MM-dd"
$RunLog = Join-Path $LogDir "$Today.log"
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

& cmd.exe /c "$Py scripts\swing_eval.py --behover-text > nul 2>&1"
if ($LASTEXITCODE -ne 0) { exit 0 }
$Week = (& $Py scripts\swing_eval.py --vecka | Select-Object -First 1).Trim()

$Claude = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
if (-not (Test-Path $Claude)) { Log "Hittar inte Claude Code-CLI:n ($Claude) - avbryter."; exit 0 }
foreach ($name in @(Get-ChildItem env: | Where-Object { $_.Name -match '^(CLAUDE|ANTHROPIC)' } | ForEach-Object Name)) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
$Allowed = @("Read", "Glob", "Grep", "Edit(./data/swing_eval/**)", "Bash(.venv/Scripts/python scripts/swing_eval.py --kontrollera:*)")
$Disallowed = @("PowerShell", "WebFetch", "WebSearch")
function Quote([string]$s) { '"' + ($s -replace '"', '\"') + '"' }

Log "Swing-utvärdering: text för $Week"
for ($attempt = 1; $attempt -le 2; $attempt++) {
    $ArgList = @("-p", (Quote "/swing-utvardering $Week"), "--model", "sonnet", "--permission-mode", "dontAsk",
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
    $limit = Test-ClaudeLimit $JsonOut "Swing-utvärdering $Week"
    if ($limit.Limit) {
        $limit.Lines | ForEach-Object { Log $_ }
        Log "Claudes användningsgräns - nästa morgonkörning försöker igen."
        exit 0
    }
    $out = @(& cmd.exe /c "$Py scripts\swing_eval.py --klar $Week 2>&1")
    $out | ForEach-Object { Log "  $_" }
    if ($LASTEXITCODE -eq 0) { Log "Texten för $Week är godkänd och sparad"; exit 10 }
}
Log "Ingen godkänd text för $Week i den här körningen"
exit 0
