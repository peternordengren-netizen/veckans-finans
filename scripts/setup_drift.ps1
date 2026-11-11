# Skapar (eller kontrollerar) driftkopian som de schemalagda uppgifterna kör från (scripts\drift.ps1).
#
# - Driftkopian: <arbetskopian>-drift, en git worktree med origin/main (detached). Redigeras aldrig för hand.
# - Katalogkopplingar (junctions) från driftkopian till arbetskopian för data som inte ligger i git, så att sidan
#   och körningarna ser samma data: datamapparna nedan, logs, .venv och node_modules. Spårad data
#   (instruments.csv, sectors.json m.fl.) kommer från main och ändras för körningarna först när den pushats.
# - Omvänt för mapparna som Claude skriver i (data\results av rapportutfallen, data\weeks av veckosammanfattningen,
#   data\hb av Handelsbanken-sammanfattningarna):
#   de riktiga mapparna ligger i driftkopian och arbetskopian har kopplingar dit. Claude Code nekar skrivningar
#   genom en katalogkoppling ut ur arbetsmappen, även med en tillåtande regel (2026-11-10: rapportutfallen
#   underkändes med "Write nekades" i läget dontAsk), så de schemalagda körningarna måste skriva i riktiga mappar.
#   En befintlig uppsättning med kopplingen åt andra hållet flyttas (mappen flyttas till driftkopian).
# - dist\ är driftkopians egen: servern (VeckansFinansServer) serverar den, och byggen i arbetskopian påverkar
#   inte sidan.
# Går att köra om: befintliga kopplingar som pekar rätt lämnas.
#
# Körs från arbetskopian: powershell -NoProfile -File scripts\setup_drift.ps1
# Registrera sedan uppgifterna: powershell -NoProfile -File scripts\install_tasks.ps1

$ErrorActionPreference = "Stop"
$Main = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Main ".git") -PathType Container)) {
    throw "setup_drift.ps1 körs från arbetskopian (där .git är en mapp), inte från $Main."
}
$Drift = "$Main-drift"
$Shared = @(
    "data\raw", "data\prices", "data\analysis", "data\calendar", "data\signals", "data\morning",
    "data\consensus", "data\valuation", "data\afternoon", "data\status", "data\swing",
    "data\bollinger", "logs", ".venv", "node_modules"
)
# Mappar som Claude skriver i: riktiga i driftkopian, kopplingar i arbetskopian (se ovan).
$ClaudeWrites = @("data\results", "data\weeks", "data\hb")

& git -C $Main fetch -q origin main
if (-not (Test-Path $Drift)) {
    & git -C $Main worktree add --detach $Drift origin/main
    if ($LASTEXITCODE -ne 0) { throw "git worktree add misslyckades" }
    Write-Output "Driftkopian skapad: $Drift"
} else {
    & git -C $Drift checkout -q -f --detach origin/main
    Write-Output "Driftkopian finns: $Drift (uppdaterad till origin/main)"
}

function Link([string]$link, [string]$target, [string]$rel) {
    # Koppling $link -> $target. En befintlig rätt koppling lämnas, en tom mapp ersätts, annat stoppar.
    if (-not (Test-Path $target)) { New-Item -ItemType Directory -Force $target | Out-Null }
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.LinkType -eq "Junction" -and (@($item.Target)[0]).TrimEnd('\') -eq $target.TrimEnd('\')) { return }
        if ($item.PSIsContainer -and -not $item.LinkType -and -not (Get-ChildItem $link -Force | Select-Object -First 1)) {
            Remove-Item $link -Force
        } else {
            throw "$link finns redan och är ingen koppling till $target - ta bort den för hand och kör om."
        }
    }
    New-Item -ItemType Directory -Force (Split-Path -Parent $link) | Out-Null
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    Write-Output "  koppling: $link -> $target"
}

foreach ($rel in $Shared) { Link (Join-Path $Drift $rel) (Join-Path $Main $rel) $rel }

foreach ($rel in $ClaudeWrites) {
    $inMain = Join-Path $Main $rel
    $inDrift = Join-Path $Drift $rel
    $d = if (Test-Path $inDrift) { Get-Item $inDrift -Force } else { $null }
    $m = if (Test-Path $inMain) { Get-Item $inMain -Force } else { $null }
    # Gammal uppsättning: riktig mapp i arbetskopian, koppling i driftkopian. Kopplingen tas bort (bara länken,
    # Directory.Delete utan rekursion) och mappen flyttas till driftkopian.
    if ($d -and $d.LinkType -eq "Junction" -and $m -and -not $m.LinkType) {
        $n = @(Get-ChildItem $inMain -Recurse -File -Force).Count
        [System.IO.Directory]::Delete($inDrift, $false)
        if (@(Get-ChildItem $inMain -Recurse -File -Force).Count -ne $n) { throw "filer saknas i $inMain efter att kopplingen togs bort" }
        Move-Item $inMain $inDrift
        Write-Output "  flyttad: $rel till driftkopian"
    }
    Link $inMain $inDrift $rel
}

Write-Output "Bygger sidan i driftkopian (dist\)..."
Push-Location $Drift
try { & cmd.exe /c "npm run build > nul 2>&1"; Write-Output "npm run build: exit $LASTEXITCODE" } finally { Pop-Location }
Write-Output ("Driftkopian kör: " + (& git -C $Drift log -1 --format="%h %s"))
