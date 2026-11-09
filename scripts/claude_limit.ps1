# Claudes användningsgräns i de schemalagda körningarna. Dot-sourcas av run_weekly.ps1,
# run_results.ps1, run_morning.ps1 och run_update.ps1.
#
# - Test-ClaudeLimit: läser Claudes JSON-svar med scripts/claude_status.py. Vid "usage_limit_reached"
#   returneras texten att logga och återställningstiden; statusfilen data/status/claude-limit.json
#   (som sidan visar) skrivs av claude_status.py.
# - Register-ClaudeRetry: registrerar (eller ersätter) en engångsuppgift fem minuter efter
#   återställningen som kör samma skript igen, så att "försöker igen efter <tid>" stämmer även om
#   ingen inloggning eller upplåsning sker. Samma inställningar som de andra uppgifterna: dold,
#   bara när användaren är inloggad, utan förhöjd behörighet, väcker datorn på nätström.

$LimitMarkerSuffix = "räknas inte som försök (Claudes användningsgräns)"

function Test-ClaudeLimit([string]$JsonOut, [string]$Source) {
    $lines = @(& cmd.exe /c ".venv\Scripts\python.exe scripts\claude_status.py check `"$JsonOut`" --kalla `"$Source`" 2>&1")
    $limit = $LASTEXITCODE -eq 3
    $reset = $null
    $text = @()
    foreach ($l in $lines) {
        if ($l -match '^RESET_AT (.+)$') { $reset = [DateTimeOffset]::Parse($Matches[1]) } else { $text += $l }
    }
    [pscustomobject]@{ Limit = $limit; Lines = $text; ResetAt = $reset }
}

function Register-ClaudeRetry([string]$TaskName, [string]$Script, [string]$ScriptArgs, $ResetAt) {
    if ($null -eq $ResetAt) { return "Ingen återställningstid i Claudes svar - nästa inloggning, upplåsning eller schemalagda körning försöker igen." }
    $at = $ResetAt.LocalDateTime.AddMinutes(5)
    $root = Split-Path -Parent $PSScriptRoot
    $conhost = Join-Path $env:WINDIR "System32\conhost.exe"
    $ps = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    # Via driftkopian (scripts\drift.ps1), som de övriga schemalagda uppgifterna; körs detta i arbetskopian
    # (för hand) pekar omförsöket ändå på driftkopian.
    $driftRoot = if ($root -match '-drift$') { $root } else { "$root-drift" }
    $action = New-ScheduledTaskAction -Execute $conhost -WorkingDirectory $driftRoot `
        -Argument "--headless `"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $driftRoot 'scripts\drift.ps1')`" -Script $Script $ScriptArgs"
    $trigger = New-ScheduledTaskTrigger -Once -At $at
    $trigger.StartBoundary = $at.ToString("yyyy-MM-dd'T'HH:mm:ss")  # lokal tid utan förskjutning
    $trigger.EndBoundary = $at.AddDays(2).ToString("yyyy-MM-dd'T'HH:mm:ss")
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    # Tas bort av Schemaläggaren en dag efter att utlösaren gått ut, så att engångsuppgifterna inte samlas på hög.
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -DeleteExpiredTaskAfter (New-TimeSpan -Days 1)
    try {
        Register-ScheduledTask -TaskName $TaskName -Force -Action $action -Trigger $trigger -Principal $principal `
            -Settings $settings -Description "Veckans finans: nytt försök efter att Claudes användningsgräns återställts." | Out-Null
        return ("Nytt försök schemalagt {0:yyyy-MM-dd HH:mm} ({1})." -f $at, $TaskName)
    } catch {
        return "Kunde inte schemalägga ett nytt försök ($TaskName): $_"
    }
}
