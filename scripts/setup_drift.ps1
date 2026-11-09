# Skapar (eller kontrollerar) driftkopian som de schemalagda uppgifterna kör från (scripts\drift.ps1).
#
# - Driftkopian: <arbetskopian>-drift, en git worktree med origin/main (detached). Redigeras aldrig för hand.
# - Katalogkopplingar (junctions) från driftkopian till arbetskopian för data som inte ligger i git, så att sidan
#   och körningarna ser samma data: datamapparna nedan, logs, .venv och node_modules. Spårad data
#   (instruments.csv, sectors.json m.fl.) kommer från main och ändras för körningarna först när den pushats.
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
    "data\raw", "data\weeks", "data\prices", "data\analysis", "data\calendar", "data\signals", "data\morning",
    "data\consensus", "data\results", "data\valuation", "data\afternoon", "data\status", "data\swing",
    "data\bollinger", "logs", ".venv", "node_modules"
)

& git -C $Main fetch -q origin main
if (-not (Test-Path $Drift)) {
    & git -C $Main worktree add --detach $Drift origin/main
    if ($LASTEXITCODE -ne 0) { throw "git worktree add misslyckades" }
    Write-Output "Driftkopian skapad: $Drift"
} else {
    & git -C $Drift checkout -q -f --detach origin/main
    Write-Output "Driftkopian finns: $Drift (uppdaterad till origin/main)"
}

foreach ($rel in $Shared) {
    $target = Join-Path $Main $rel
    $link = Join-Path $Drift $rel
    if (-not (Test-Path $target)) { New-Item -ItemType Directory -Force $target | Out-Null }
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.LinkType -eq "Junction" -and (@($item.Target)[0]).TrimEnd('\') -eq $target.TrimEnd('\')) { continue }
        if ($item.PSIsContainer -and -not $item.LinkType -and -not (Get-ChildItem $link -Force | Select-Object -First 1)) {
            Remove-Item $link -Force
        } else {
            throw "$link finns redan och är ingen koppling till $target - ta bort den för hand och kör om."
        }
    }
    New-Item -ItemType Directory -Force (Split-Path -Parent $link) | Out-Null
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    Write-Output "  koppling: $rel -> $target"
}

Write-Output "Bygger sidan i driftkopian (dist\)..."
Push-Location $Drift
try { & cmd.exe /c "npm run build > nul 2>&1"; Write-Output "npm run build: exit $LASTEXITCODE" } finally { Pop-Location }
Write-Output ("Driftkopian kör: " + (& git -C $Drift log -1 --format="%h %s"))
