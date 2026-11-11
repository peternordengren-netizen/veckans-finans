# Handelsbanken-rapporter som laddats ner för hand (data\hb_manual\): en ny fil sammanfattas en gång med Claude Code
# (Sonnet, /hb-sammanfatta), med egna ord och utan citat; bara sammanfattningen går till rådatarepot
# (scripts/hb_manual.py).
#
# Anropas av run_morning.ps1, run_bollinger.ps1 (09:20 och 13:20) och run_update.ps1 ("Uppdatera nu"), före bygget.
# - Inga nya filer: inget körs (ingen Claude), exit 0.
# - Varje ny fil: texten tas ut, Claude skriver data\hb\<id>.json och kör kontrollen, högst två försök; godkänd ->
#   rådatarepot och arbetsfilerna tas bort. Högst tre körningar per fil (hb_manual.MAX_ATTEMPTS), sedan hoppas den över.
# - Claude körs i läget dontAsk: läsa, skriva bara i data/hb/, och bara hb_manual.py --kontrollera.
# - Nås Claudes användningsgräns stannar skriptet; nästa utlösare (kontroll, morgon, Uppdatera nu) försöker igen.
# - Logg i logs\hb\<datum>.log. Exit 10 när minst en ny sammanfattning lagts till (anroparen bygger då sidan).
#
# Manuellt: powershell -NoProfile -File scripts\run_hb.ps1

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "claude_limit.ps1")
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Py = ".venv\Scripts\python.exe"

$LogDir = Join-Path $Root "logs\hb"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Today = Get-Date -Format "yyyy-MM-dd"
$RunLog = Join-Path $LogDir "$Today.log"
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

# Nya filer (id:n på stdout, förklaringar på stderr till loggen).
$ErrFile = Join-Path $LogDir "$Today-nya.err"
$ids = @(& cmd.exe /c "$Py scripts\hb_manual.py --nya 2>`"$ErrFile`"" | Where-Object { $_ -match '^[0-9a-f]{16}$' })
if (Test-Path $ErrFile) { Get-Content $ErrFile -Encoding UTF8 | ForEach-Object { Log "  $_" }; Remove-Item $ErrFile }
if ($ids.Count -eq 0) { exit 0 }
Log "Handelsbanken: $($ids.Count) nya filer att sammanfatta"

$Claude = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
if (-not (Test-Path $Claude)) { Log "Hittar inte Claude Code-CLI:n ($Claude) - avbryter."; exit 0 }
foreach ($name in @(Get-ChildItem env: | Where-Object { $_.Name -match '^(CLAUDE|ANTHROPIC)' } | ForEach-Object Name)) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
# Ett lås: två utlösare samtidigt (t.ex. Bollinger 09:20 och en sen morgonkörning) sammanfattar inte samma fil två gånger.
$Lock = New-Object System.Threading.Mutex($false, "Local\VeckansFinansHandelsbanken")
try {
    if (-not $Lock.WaitOne([TimeSpan]::FromMinutes(15))) { Log "En annan sammanfattning pågår fortfarande - avbryter."; exit 0 }
} catch [System.Threading.AbandonedMutexException] { }

$Allowed = @("Read", "Glob", "Grep", "Edit(./data/hb/**)", "Bash(.venv/Scripts/python scripts/hb_manual.py --kontrollera:*)")
$Disallowed = @("PowerShell", "WebFetch", "WebSearch")
function Quote([string]$s) { '"' + ($s -replace '"', '\"') + '"' }

$added = 0
foreach ($id in $ids) {
    $n = & $Py scripts\hb_manual.py --forsok $id
    Log "${id}: körning $n"
    $done = $false
    for ($attempt = 1; $attempt -le 2 -and -not $done; $attempt++) {
        $ArgList = @("-p", (Quote "/hb-sammanfatta $id"), "--model", "sonnet", "--permission-mode", "dontAsk",
            "--allowedTools") + ($Allowed | ForEach-Object { Quote $_ }) +
            @("--disallowedTools") + ($Disallowed | ForEach-Object { Quote $_ }) +
            @("--strict-mcp-config", "--no-session-persistence", "--output-format", "json")
        $JsonOut = Join-Path $LogDir "$Today-$id-$attempt.json"
        $ErrOut = Join-Path $LogDir "$Today-$id-$attempt.err"
        Log "${id}: startar Claude Code (Sonnet), försök $attempt"
        try {
            $proc = Start-Process -FilePath $Claude -ArgumentList $ArgList -NoNewWindow -PassThru -Wait `
                -RedirectStandardOutput $JsonOut -RedirectStandardError $ErrOut
            $r = Get-Content $JsonOut -Raw -Encoding UTF8 | ConvertFrom-Json
            Log ("${id}: exit $($proc.ExitCode), turer $($r.num_turns), " +
                "$([math]::Round($r.duration_ms / 60000, 1)) min, nekade anrop $(@($r.permission_denials).Count)")
        } catch {
            Log "${id}: Claude-steget fallerade: $_"
        }
        $limit = Test-ClaudeLimit $JsonOut "Handelsbanken $id"
        if ($limit.Limit) {
            $limit.Lines | ForEach-Object { Log "${id}: $_" }
            Log "Claudes användningsgräns - nästa utlösare försöker igen."
            exit $(if ($added -gt 0) { 10 } else { 0 })
        }
        $out = @(& cmd.exe /c "$Py scripts\hb_manual.py --klar $id 2>&1")
        $out | ForEach-Object { Log "  $_" }
        if ($LASTEXITCODE -eq 0) { $done = $true; $added++ }
    }
    if (-not $done) { Log "${id}: ingen godkänd sammanfattning i den här körningen" }
}
Log "Klart: $added nya sammanfattningar"
exit $(if ($added -gt 0) { 10 } else { 0 })
