# Skapar (eller ersätter) de schemalagda uppgifterna och skrivbordsgenvägen för Veckans finans.
#
# - VeckansFinansServer: vid inloggning, dolt (conhost --headless), kör scripts\serve.ps1.
#   Ingen tidsgräns. Omstart vid krasch sker i serve.ps1 (conhost --headless returnerar alltid 0,
#   så Schemaläggarens egen omstart nedan slår bara till om uppgiften inte går att starta).
# - VeckansFinansVeckosammanfattning: måndagar 08:30, kör scripts\run_weekly.ps1 (Opus).
#   Väcker datorn (kräver nätström – väckningstimers är avstängda på batteri), körs i
#   efterhand om datorn var avstängd, högst 2 timmar. Startas också fem minuter efter inloggning
#   och upplåsning, så att en missad måndag tas igen (skriptet gör inget om veckan redan finns).
# - VeckansFinansMorgonrapport: vardagar 08:10, kör scripts\run_morning.ps1 (utan Claude).
#   Samma väckning och inloggnings-/upplåsningsutlösare som veckouppgiften, högst 30 minuter.
#   Utlöses den före 08:10 (inloggning eller upplåsning tidigt) gör run_morning.ps1 ingenting.
# - VeckansFinansEftermiddag: vardagar 17:45, kör scripts\run_update.ps1 -Scheduled. Samma väckning,
#   högst en timme. Också vid inloggning och upplåsning (gör då bara något om dagens kursreaktion
#   saknas eller är ofullständig).
# - VeckansFinansBollinger: vardagar 09:20 och 13:20, kör scripts\run_bollinger.ps1 -Scheduled (utan Claude):
#   Bollingerkontroll, Windows-notis och bygget. Inget på börsens stängda dagar eller utanför 09:00-17:30.
# - Skrivbordsgenvägen "Uppdatera nu" kör scripts\run_update.ps1 -Open i ett synligt fönster.
# Alla körs som den inloggade användaren, bara när hen är inloggad, utan förhöjd behörighet.
# - Skrivbordsgenvägen "Veckans finans" kör scripts\open_site.ps1 dolt.
#
# Körs manuellt: powershell -NoProfile -File scripts\install_tasks.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Conhost = Join-Path $env:WINDIR "System32\conhost.exe"
$Ps = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$User = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

function HiddenAction([string]$Script) {
    New-ScheduledTaskAction -Execute $Conhost -WorkingDirectory $Root `
        -Argument "--headless `"$Ps`" -NoProfile -File `"$(Join-Path $Root $Script)`""
}

$serverSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "VeckansFinansServer" -Force `
    -Description "Veckans finans: lokal sida på http://localhost:4321/veckans-finans/ (astro preview)." `
    -Action (HiddenAction "scripts\serve.ps1") `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $User) `
    -Principal $Principal -Settings $serverSettings | Out-Null

# Måndag 08:30 lokal tid: efter att Claudes veckokvot återställts 08:00 (W43 stoppades 07:30 av
# användningsgränsen) och efter morgonrapporten 08:10. New-ScheduledTaskTrigger sparar starttiden med
# dagens UTC-förskjutning (t.ex. +02:00 sommartid), och då flyttas körningen en timme när vintertiden
# börjar. Utan förskjutning tolkas tiden alltid som lokal tid.
$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "08:30"
$weekly.StartBoundary = (Get-Date "08:30").ToString("yyyy-MM-dd'T'HH:mm:ss")

# Inloggning och upplåsning, fem minuter fördröjt: tar igen en missad måndagskörning (t.ex. när
# datorn låg i viloläge till disk). run_weekly.ps1 avslutar direkt om veckan redan finns.
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $User
$atLogon.Delay = "PT5M"
$unlockClass = Get-CimClass -Namespace "Root/Microsoft/Windows/TaskScheduler" -ClassName "MSFT_TaskSessionStateChangeTrigger"
$atUnlock = New-CimInstance -CimClass $unlockClass -ClientOnly
$atUnlock.StateChange = 8  # TASK_SESSION_UNLOCK
$atUnlock.UserId = $User
$atUnlock.Delay = "PT5M"
$atUnlock.Enabled = $true

$weeklySettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "VeckansFinansVeckosammanfattning" -Force `
    -Description "Veckans finans: /sammanfatta-veckan för förra veckan med Claude Code (dontAsk), bygger sedan om sidan. Loggar i logs\weekly\." `
    -Action (HiddenAction "scripts\run_weekly.ps1") `
    -Trigger @($weekly, $atLogon, $atUnlock) `
    -Principal $Principal -Settings $weeklySettings | Out-Null

# Vardagar 08:10 lokal tid (morning.py hoppar själv över börsens stängda dagar), samma
# väckning, inloggnings- och upplåsningsutlösare som veckouppgiften.
$morning = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "08:10"
$morning.StartBoundary = (Get-Date "08:10").ToString("yyyy-MM-dd'T'HH:mm:ss")
$morningSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "VeckansFinansMorgonrapport" -Force `
    -Description "Veckans finans: morgonrapport utan Claude (scripts\morning.py), bygger sedan om sidan. Loggar i logs\morning\." `
    -Action (HiddenAction "scripts\run_morning.ps1") `
    -Trigger @($morning, $atLogon, $atUnlock) `
    -Principal $Principal -Settings $morningSettings | Out-Null

# Vardagar 17:45 lokal tid: eftermiddagsuppdateringen. Väcker datorn och tas igen samma kväll om
# datorn var avstängd; run_update.ps1 -Scheduled gör ingenting före 17:45 (t.ex. nästa morgon)
# eller när börsen är stängd. Inloggning och upplåsning startar den också: run_update.ps1 kör då
# bara om dagens kursreaktion saknas eller är ofullständig (kurser eller nätverket saknades).
# Det som fortfarande saknas nästa morgon tar morgonflödet.
$afternoon = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "17:45"
$afternoon.StartBoundary = (Get-Date "17:45").ToString("yyyy-MM-dd'T'HH:mm:ss")
# En timme: på en tung rapportdag kan det bli flera Claude-omgångar, och låset i run_results.ps1
# kan behöva vänta på en pågående körning.
$afternoonSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$afternoonAction = New-ScheduledTaskAction -Execute $Conhost -WorkingDirectory $Root `
    -Argument "--headless `"$Ps`" -NoProfile -File `"$(Join-Path $Root 'scripts\run_update.ps1')`" -Scheduled"
Register-ScheduledTask -TaskName "VeckansFinansEftermiddag" -Force `
    -Description "Veckans finans: eftermiddagsuppdatering (rapportutfall sedan morgonen med Claude Code om det finns nya rapporter, dagens kursreaktion), bygger sedan om sidan. Loggar i logs\updates\." `
    -Action $afternoonAction -Trigger @($afternoon, $atLogon, $atUnlock) `
    -Principal $Principal -Settings $afternoonSettings | Out-Null

# Vardagar 09:20 och 13:20 lokal tid: Bollingerkontrollen under handelsdagen (run_bollinger.ps1 -Scheduled, utan
# Claude). Samma väckning; skriptet gör ingenting på börsens stängda dagar eller utanför 09:00-17:30. Ingen
# inloggnings-/upplåsningsutlösare: en missad kontroll tas inte igen senare under dagen. Högst 15 minuter.
$bbMorning = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "09:20"
$bbMorning.StartBoundary = (Get-Date "09:20").ToString("yyyy-MM-dd'T'HH:mm:ss")
$bbMidday = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "13:20"
$bbMidday.StartBoundary = (Get-Date "13:20").ToString("yyyy-MM-dd'T'HH:mm:ss")
$bollingerSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$bollingerAction = New-ScheduledTaskAction -Execute $Conhost -WorkingDirectory $Root `
    -Argument "--headless `"$Ps`" -NoProfile -File `"$(Join-Path $Root 'scripts\run_bollinger.ps1')`" -Scheduled"
Register-ScheduledTask -TaskName "VeckansFinansBollinger" -Force `
    -Description "Veckans finans: Bollingerkontroll under handelsdagen (bolag vid eller nära övre bandet), Windows-notis och bygget av sidan. Utan Claude. Loggar i logs\bollinger\." `
    -Action $bollingerAction -Trigger @($bbMorning, $bbMidday) `
    -Principal $Principal -Settings $bollingerSettings | Out-Null

$shell = New-Object -ComObject WScript.Shell
# "Uppdatera nu": samma flöde direkt, i ett synligt fönster så att förloppet syns; öppnar sidan när den är klar.
$upd = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Uppdatera nu.lnk"))
$upd.TargetPath = $Ps
$upd.Arguments = "-NoProfile -File `"$(Join-Path $Root 'scripts\run_update.ps1')`" -Open"
$upd.WorkingDirectory = $Root
$upd.IconLocation = "$env:WINDIR\System32\shell32.dll,238"
$upd.Description = "Veckans finans: hämta, ta fram nya rapportutfall och dagens kursreaktion, och öppna sidan"
$upd.Save()

$lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Veckans finans.lnk"))
$lnk.TargetPath = $Conhost
$lnk.Arguments = "--headless `"$Ps`" -NoProfile -File `"$(Join-Path $Root 'scripts\open_site.ps1')`""
$lnk.WorkingDirectory = $Root
$lnk.IconLocation = "$env:WINDIR\System32\shell32.dll,13"
$lnk.Description = "Öppna Veckans finans (startar den lokala servern vid behov)"
$lnk.Save()

Get-ScheduledTask -TaskName "VeckansFinans*" | Select-Object TaskName, State
