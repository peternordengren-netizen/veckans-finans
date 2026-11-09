# Kör en schemalagd körning från driftkopian, efter att den uppdaterats till senaste main från GitHub.
#
# Driftkopian (veckans-finans-drift bredvid arbetskopian, en git worktree, se scripts\setup_drift.ps1) används av
# alla schemalagda uppgifter och genvägen "Uppdatera nu". Arbetskopian används aldrig av dem: bara det som är
# committat och pushat till main körs. Datamapparna, logs, .venv och node_modules är katalogkopplingar till
# arbetskopian, så sidan och körningarna ser samma data.
#
# Före körningen: git fetch origin main och checkout av origin/main (detached, -f: driftkopian redigeras aldrig).
# - Ett lås gör att två uppgifter inte uppdaterar kopian samtidigt.
# - Går GitHub inte att nå körs den befintliga versionen, med en varning i loggen.
# - En körning som pågår när en annan uppdaterar kopian (t.ex. morgonens kursförsök till 09:10 och Bollinger
#   09:20) kan få den nya koden för sina återstående steg. Det händer bara efter en push.
# - FI-filer som laddas ner för hand sparas som förut i arbetskopians data\fi_manual (VF_FI_MANUAL_DIR).
# - Logg i logs\drift.log: tid, skript och vilken commit som kördes.
#
# Användning (av Schemaläggaren och genvägarna, se install_tasks.ps1):
#   powershell -NoProfile -File <drift>\scripts\drift.ps1 -Script scripts\run_morning.ps1
#   ... -Script scripts\run_update.ps1 -Scheduled | -Open
#   ... -Script scripts\run_results.ps1 -Week 2026-11-05 -Build

param(
    [Parameter(Mandatory = $true)][string]$Script,
    [switch]$Scheduled,
    [switch]$Open,
    [switch]$Build,
    [string]$Week = ""
)

$ErrorActionPreference = "Stop"
# Git skriver commitrubrikerna (å, ä, ö) i UTF-8; utan detta blir de fel i logs\drift.log.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
# Bara i driftkopian: där är .git en fil (worktree), i arbetskopian en mapp.
if (-not (Test-Path (Join-Path $Root ".git") -PathType Leaf)) {
    throw "drift.ps1 körs bara från driftkopian ($Root är inte en worktree). Se scripts\setup_drift.ps1."
}
$Main = $Root -replace '-drift$', ''
$LogFile = Join-Path $Root "logs\drift.log"
function Log([string]$Text) {
    Add-Content -Path $LogFile -Encoding UTF8 -Value ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text)
}

$Lock = New-Object System.Threading.Mutex($false, "Local\VeckansFinansDriftUppdatering")
try {
    try { [void]$Lock.WaitOne([TimeSpan]::FromMinutes(10)) } catch [System.Threading.AbandonedMutexException] { }
    $fetch = & cmd.exe /c "git -C `"$Root`" fetch -q origin main 2>&1"
    if ($LASTEXITCODE -eq 0) {
        $co = & cmd.exe /c "git -C `"$Root`" checkout -q -f --detach origin/main 2>&1"
        if ($LASTEXITCODE -ne 0) { Log "VARNING: checkout av origin/main misslyckades ($co) - kör befintlig version" }
    } else {
        Log "VARNING: git fetch misslyckades ($fetch) - kör befintlig version"
    }
    $head = (& git -C $Root log -1 --format="%h %s").Trim()
} finally {
    $Lock.ReleaseMutex()
}

$ScriptPath = Join-Path $Root $Script
$argList = @()
if ($Scheduled) { $argList += "-Scheduled" }
if ($Open) { $argList += "-Open" }
if ($Week) { $argList += @("-Week", $Week) }
if ($Build) { $argList += "-Build" }
Log "$Script $($argList -join ' ') från $head"
$env:VF_FI_MANUAL_DIR = Join-Path $Main "data\fi_manual"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @argList
$code = $LASTEXITCODE
Log "$Script klar (exit $code)"
exit $code
