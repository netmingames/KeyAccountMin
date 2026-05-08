# Registriert die Scheduled Task `AI_Mitarbeiter_KeyAccountMin`.
# EINMALIG als Administrator ausfuehren (Rechtsklick -> "Mit PowerShell als Administrator ausfuehren").
# Idempotent: vorhandene Task wird mit -Force ueberschrieben.
#
# Was die Task macht:
# - Startet bei Bootup und bei User-Logon den Sid-Service (FastAPI, Port 5003)
# - Restart bei Failure: 3x in 1-Min-Abstaenden
# - Laeuft im User-Kontext (LogonType Interactive, RunLevel Limited)

$ErrorActionPreference = "Stop"

$TaskName = "AI_Mitarbeiter_KeyAccountMin"
$Python   = "C:\Users\netmin_m\AppData\Local\Programs\Python\Python312\python.exe"
$App      = "D:\Claude\Serverin\KeyAccountMin\app.py"
$Cwd      = "D:\Claude\Serverin\KeyAccountMin"
$User     = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path $Python)) {
    Write-Error "Python nicht gefunden: $Python"
    exit 1
}
if (-not (Test-Path $App)) {
    Write-Error "App nicht gefunden: $App"
    exit 1
}

$action = New-ScheduledTaskAction -Execute $Python -Argument $App -WorkingDirectory $Cwd
$trigger1 = New-ScheduledTaskTrigger -AtLogOn -User $User
$trigger2 = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($trigger1, $trigger2) `
    -Settings $settings `
    -Principal $principal `
    -Description "Sid / KeyAccountMin Service (Port 5003) - Listings und Lokalisierung Werkbank" `
    -Force | Out-Null

Write-Host "OK — Task '$TaskName' registriert."
Write-Host "Sid wird beim naechsten Login bzw. Reboot automatisch starten."
Write-Host ""
Write-Host "Sofortstart (manuell):"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
