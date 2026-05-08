# Sid-Watchdog: prueft den FastAPI-Service auf Port 5003.
# NT-546 (Phase 0 Lisbeth-Finding 3): dedizierter Watchdog im KeyAccountMin-Repo.
#
# Was er macht:
# - HTTP-Health-Check auf http://localhost:5003/api/health, erwartet "ok".
# - Bei Fehlschlag: zaehlt Strikes; nach 2 Strikes in Folge wird der Scheduled
#   Task `Sid_KeyAccountMin` neu gestartet (sofern registriert).
# - Schreibt Logs nach D:\Claude\Serverin\KeyAccountMin\logs\watchdog.log.
# - State (Strikes + letzte Restarts) liegt unter agent-state\sid_watchdog_state.json.
#
# Aufruf:
# - Manuell: powershell -NoProfile -ExecutionPolicy Bypass -File <pfad>
# - Empfohlen via Scheduled Task `Sid_Watchdog` alle 5 Min (separates Setup-
#   Skript folgt; aktuell genuegt der Center-Watchdog mit service_check_sid).
# - Der Center-Watchdog (D:\Claude\Serverin\scripts\watchdog.ps1) hat einen
#   eigenen service_check_sid Feature-Toggle und ist der primaere Health-Check
#   im laufenden Betrieb. Dieser Sid-eigene Watchdog ist ein lokales Standalone-
#   Pendant fuer den Fall, dass das KeyAccountMin-Repo isoliert deployed wird.

$ErrorActionPreference = "Continue"

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$LogDir     = Join-Path $RepoRoot "logs"
$LogFile    = Join-Path $LogDir "watchdog.log"
$StateDir   = "D:\Claude\agent-state"
$StateFile  = Join-Path $StateDir "sid_watchdog_state.json"
$HealthUrl  = "http://localhost:5003/api/health"
$TaskName   = "Sid_KeyAccountMin"
$StrikesBeforeRestart = 2
$HttpTimeoutSec = 15

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Log {
    param([string]$Level, [string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts [$Level] $Msg" | Out-File -Append -FilePath $LogFile -Encoding utf8
}

function Read-State {
    if (-not (Test-Path $StateFile)) {
        return @{ strikes = 0; last_fail_at = $null; restarts = @() }
    }
    try {
        $raw = Get-Content $StateFile -Raw -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return @{ strikes = 0; last_fail_at = $null; restarts = @() }
        }
        $obj = $raw | ConvertFrom-Json -ErrorAction Stop
        $ht = @{
            strikes      = if ($obj.strikes) { [int]$obj.strikes } else { 0 }
            last_fail_at = $obj.last_fail_at
            restarts     = @()
        }
        if ($obj.restarts) {
            foreach ($r in @($obj.restarts)) { $ht.restarts += $r }
        }
        return $ht
    } catch {
        Log "WARN" "State-Lesen fehlgeschlagen: $($_.Exception.Message) - reset"
        return @{ strikes = 0; last_fail_at = $null; restarts = @() }
    }
}

function Write-State {
    param([hashtable]$State)
    try {
        $State | ConvertTo-Json -Depth 4 | Out-File $StateFile -Encoding utf8 -Force
    } catch {
        Log "WARN" "State-Schreiben fehlgeschlagen: $($_.Exception.Message)"
    }
}

function Test-SidHealth {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl `
            -TimeoutSec $HttpTimeoutSec -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -ne 200) { return $false }
        if ($resp.Content -notmatch "ok") { return $false }
        return $true
    } catch {
        return $false
    }
}

function Restart-SidTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Log "ERROR" "Scheduled Task '$TaskName' nicht gefunden - register_sid_task.ps1 als Admin laufen lassen"
        return $false
    }
    try {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Start-Sleep -Seconds 3
        Log "INFO" "Sid via Scheduled Task '$TaskName' neu gestartet"
        return $true
    } catch {
        Log "ERROR" "Sid-Restart fehlgeschlagen: $($_.Exception.Message)"
        return $false
    }
}

# ── Main ─────────────────────────────────────────────────────────────

Log "INFO" "=== Sid-Watchdog Start ==="

$state = Read-State
$healthy = Test-SidHealth

if ($healthy) {
    if ([int]$state.strikes -gt 0) {
        Log "INFO" "Sid OK (nach $($state.strikes) Strike(s), Reset)"
    } else {
        Log "INFO" "Sid OK"
    }
    $state.strikes = 0
    $state.last_fail_at = $null
    Write-State -State $state
    Log "INFO" "=== Sid-Watchdog Ende ==="
    return
}

# Unhealthy: Strike erhoehen
$state.strikes = [int]$state.strikes + 1
$state.last_fail_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")

if ($state.strikes -lt $StrikesBeforeRestart) {
    Log "WARN" "Sid HTTP unhealthy (Strike $($state.strikes)/$StrikesBeforeRestart) - warte"
    Write-State -State $state
    Log "INFO" "=== Sid-Watchdog Ende ==="
    return
}

Log "WARN" "Sid HTTP unhealthy nach $($state.strikes) Strikes - Restart via Scheduled Task"
if (Restart-SidTask) {
    $state.restarts = @($state.restarts) + @((Get-Date).ToString("yyyy-MM-ddTHH:mm:ss"))
    $state.strikes = 0
}
Write-State -State $state
Log "INFO" "=== Sid-Watchdog Ende ==="
