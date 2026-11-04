# Bollingerkontrollen under handelsdagen (scripts/bollinger.py, utan Claude), en Windows-notis och bygget av sidan
# ("Bollinger" överst på startsidan).
#
# Körs av den schemalagda uppgiften VeckansFinansBollinger vardagar 09:20 och 13:20 (-Scheduled).
# - Ingen börsdag (helg eller svensk börshelgdag): gör ingenting.
# - -Scheduled: bara mellan 09:00 och 17:30, så att en körning som tas igen efter att datorn varit
#   avstängd inte visar en kontroll utanför handelstid.
# - Notisen listar bolagen vid eller över det övre bandet och, separat, de inom 1 % under det, och säger att
#   kurserna är fördröjda ca 15 minuter. Ett klick öppnar sidan.
# - Logg i logs/bollinger/<datum>.log.
#
# Manuellt: powershell -NoProfile -File scripts\run_bollinger.ps1
# -Now "2026-11-04 08:00" anger klockslaget för kontrollen av handelstid (för test).

param(
    [switch]$Scheduled,
    [datetime]$Now = (Get-Date)
)

$ErrorActionPreference = "Stop"
if ($Scheduled -and ($Now.TimeOfDay -lt [timespan]"09:00" -or $Now.TimeOfDay -gt [timespan]"17:30")) {
    Write-Output ("{0:HH:mm} är utanför handelstid - ingen Bollingerkontroll." -f $Now)
    exit 0
}
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Py = ".venv\Scripts\python.exe"

& $Py scripts\bollinger.py --handelsdag
if ($LASTEXITCODE -eq 3) { Write-Output "Börsen är stängd i dag - ingen Bollingerkontroll."; exit 0 }

$LogDir = Join-Path $Root "logs\bollinger"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$RunLog = Join-Path $LogDir ("{0:yyyy-MM-dd}.log" -f (Get-Date))
function Log([string]$Text) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text
    Add-Content -Path $RunLog -Value $line -Encoding UTF8
    Write-Output $line
}

Log "Bollingerkontroll startar$(if ($Scheduled) { ' (schemalagd)' } else { '' })"
& cmd.exe /c "$Py scripts\bollinger.py 2>&1" | ForEach-Object { Log "  $_" }
$code = $LASTEXITCODE
Log "bollinger.py: exit $code"
if ($code -ne 0) {
    Log "FEL: ingen kontroll skrevs (se raderna ovan) - ingen notis."
    exit 0
}

# Senaste kontrollen (filen som just skrevs).
$latest = Get-ChildItem (Join-Path $Root "data\bollinger") -Filter "*.json" | Sort-Object Name | Select-Object -Last 1
$data = Get-Content $latest.FullName -Raw -Encoding UTF8 | ConvertFrom-Json

function NameList($rows, [int]$max = 8) {
    $names = @($rows | ForEach-Object { "{0} ({1:+0.0;-0.0;0.0} %)" -f $_.name, $_.vs_upper_pct })
    if ($names.Count -eq 0) { return "inga" }
    $more = if ($names.Count -gt $max) { " och $($names.Count - $max) till" } else { "" }
    return (($names | Select-Object -First $max) -join ", ") + $more
}
function Esc([string]$s) { [Security.SecurityElement]::Escape($s) }

$time = ([datetime]$data.checked_at).ToString("HH:mm")
$title = "Bollinger kl. $time - övre bandet (20 d, 2 std)"
$over = "Vid eller över: " + (NameList $data.over)
$near = "Inom 1 % under: " + (NameList $data.near)
$foot = $data.delay_note
Log "Notis: $over | $near"

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xml = @"
<toast activationType="protocol" launch="http://localhost:4321/veckans-finans/">
  <visual>
    <binding template="ToastGeneric">
      <text>$(Esc $title)</text>
      <text>$(Esc $over)</text>
      <text>$(Esc "$near. $foot")</text>
    </binding>
  </visual>
</toast>
"@
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($xml)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
    # Windows PowerShells eget app-id, så att notisen visas utan att en egen app registreras.
    $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    Log "Notis visad."
} catch {
    Log "FEL: notisen kunde inte visas: $_"
}

$BuildLog = Join-Path $LogDir ("{0:yyyy-MM-dd}-build.log" -f (Get-Date))
& cmd.exe /c "npm run build > `"$BuildLog`" 2>&1"
Log "npm run build: exit $LASTEXITCODE (logg: $BuildLog)"
Log "Klart"
