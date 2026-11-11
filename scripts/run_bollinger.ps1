# Bollingerkontrollen under handelsdagen (scripts/bollinger.py, utan Claude), en Windows-notis och bygget av sidan
# (fliken Swing: Bollinger, status i dag och RSI för strategikandidaterna).
#
# Körs av den schemalagda uppgiften VeckansFinansBollinger vardagar 09:20 och 13:20 (-Scheduled).
# - Ingen börsdag (helg eller svensk börshelgdag): gör ingenting.
# - -Scheduled: bara mellan 09:00 och 17:30, så att en körning som tas igen efter att datorn varit
#   avstängd inte visar en kontroll utanför handelstid.
# - Notisen gäller bara kandidaterna i strategierna Pullback i trend och Rekyl efter Bollingerutbrott (fliken
#   Swing): deras ändrade "Status i dag" sedan föregående kontroll (väntar på trigger, trigger bruten, sen,
#   ogiltig) och de som ligger vid eller över övre bandet, med avståndet dit i R. Den säger att kurserna är
#   fördröjda ca 15 minuter. Ett klick öppnar sidan. (Listorna över alla bolag vid bandet finns kvar i
#   kontrollfilen men visas inte sedan 2026-11-11.)
# - Bygget uppdaterar kolumnerna "Bollinger" och "Status i dag" i fliken Swing med kontrollens kurser och tid.
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

function Esc([string]$s) { [Security.SecurityElement]::Escape($s) }

# En strategis kandidater vid eller över övre bandet, med avståndet dit i R ("Hoist (0,6 R över)").
function UpperList($section) {
    $sv = [Globalization.CultureInfo]::GetCultureInfo("sv-SE")
    $names = @($section.rows | Where-Object { $_.at_upper } | ForEach-Object {
        $overR = -[double]$_.upper_r  # vid eller över bandet: upper_r <= 0
        $where = if ($null -eq $_.upper_r) { "" } elseif ([math]::Round($overR, 1) -eq 0) { " (vid bandet)" } else { " ({0} R över)" -f $overR.ToString("0.0", $sv) }
        "$($_.name)$where"
    })
    if ($names.Count -eq 0) { return "inga" }
    return $names -join ", "
}

$time = ([datetime]$data.checked_at).ToString("HH:mm")
$title = "Swing kl. $time - strategikandidaterna"
# Bara kandidaterna i de tre strategierna: status i dag (ändringar sedan föregående kontroll) och övre bandet.
$strategies = @(
    @{ Name = "Pullback i trend"; Section = $data.pullback },
    @{ Name = "Rekyl efter Bollingerutbrott"; Section = $data.bbrekyl },
    @{ Name = "Momentum-utbrott (VCP)"; Section = $data.vcp }
)
$statusParts = @()
$upperParts = @()
foreach ($s in $strategies) {
    if ($null -eq $s.Section -or @($s.Section.rows).Count -eq 0) { continue }
    $statusParts += "$($s.Name): $($s.Section.notice)"
    $upperParts += "$($s.Name) vid övre bandet: $(UpperList $s.Section) (av $($s.Section.candidates))."
}
$statusText = if ($statusParts.Count -gt 0) { $statusParts -join " " } elseif ($null -eq $data.pullback) {
    "Ingen swingfil för i dag (inga kandidater)."
} else { "Inga kandidater med kurs i dag." }
$upperText = $upperParts -join " "
# Larm ur Swing-utvärderingen (liknar nyligen lyckade fall, marknadstecken; scripts/swing_eval.py).
if ($data.eval_alerts -and $data.eval_alerts.notice) { $statusText = "$statusText $($data.eval_alerts.notice)" }
$foot = $data.delay_note
Log "Notis: $statusText | $upperText"

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $xml = @"
<toast activationType="protocol" launch="http://localhost:4321/veckans-finans/">
  <visual>
    <binding template="ToastGeneric">
      <text>$(Esc $title)</text>
      <text>$(Esc $statusText)</text>
      <text>$(Esc "$upperText $foot".Trim())</text>
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

# Handelsbanken-rapporter som laddats ner för hand (run_hb.ps1): en ny fil sammanfattas, bygget nedan visar den.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_hb.ps1") | ForEach-Object { Log "  $_" }

$BuildLog = Join-Path $LogDir ("{0:yyyy-MM-dd}-build.log" -f (Get-Date))
& cmd.exe /c "npm run build > `"$BuildLog`" 2>&1"
Log "npm run build: exit $LASTEXITCODE (logg: $BuildLog)"
Log "Klart"
