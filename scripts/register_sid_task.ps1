# Registriert die Scheduled Task `Sid_KeyAccountMin`.
# EINMALIG als Administrator ausfuehren (Rechtsklick -> "Mit PowerShell als Administrator ausfuehren").
# Idempotent: vorhandene Task wird mit -Force ueberschrieben.
#
# Was die Task macht:
# - Startet bei Bootup den Sid-Service (FastAPI, Port 5003) — vor User-Logon, weil
#   der Task unter dem ServiceAccount-Principal SYSTEM laeuft.
# - Restart bei Failure: 3x in 1-Min-Abstaenden.
# - LogonType ServiceAccount + AtStartup-Trigger ist die kanonische Kombination
#   fuer "Boot-Service ohne User-Login" (NT-546 Lisbeth-Finding 2).

$ErrorActionPreference = "Stop"

$TaskName = "Sid_KeyAccountMin"
$Python   = "C:\Users\netmin_m\AppData\Local\Programs\Python\Python312\python.exe"
$App      = "D:\Claude\Serverin\KeyAccountMin\app.py"
$Cwd      = "D:\Claude\Serverin\KeyAccountMin"

if (-not (Test-Path $Python)) {
    Write-Error "Python nicht gefunden: $Python"
    exit 1
}
if (-not (Test-Path $App)) {
    Write-Error "App nicht gefunden: $App"
    exit 1
}

$action = New-ScheduledTaskAction -Execute $Python -Argument $App -WorkingDirectory $Cwd
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

# ServiceAccount + SYSTEM = Boot-faehig, kein Login noetig. RunLevel Highest, weil
# Sid Port 5003 bindet und ggf. Scheduled-Task-Kontrollrechte braucht
# (Watchdog-Restart per Start-ScheduledTask).
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Sid / KeyAccountMin Service (Port 5003) - Listings und Lokalisierung Werkbank" `
    -Force | Out-Null

Write-Host ("OK - Task " + $TaskName + " registriert.")
Write-Host "Sid wird beim naechsten Reboot automatisch starten (auch ohne User-Login)."
Write-Host ""
Write-Host "Sofortstart (manuell):"
Write-Host ("  Start-ScheduledTask -TaskName " + $TaskName)
