# PHILO-TECH - Fix student phone access (Windows Firewall).
#
# Root cause: on first run Windows silently creates a BLOCK rule for python.exe
# that overrides the port ALLOW rule, so phones are blocked even though the
# server runs and the PC can reach it. This script removes that block, adds the
# correct allow rules, cleans duplicates, and marks the router network Private.
#
# ASCII-only on purpose: avoids the encoding breakage seen when Windows
# PowerShell 5.1 reads a UTF-8 script with non-ASCII comments.
#
# Run once, in an ADMIN PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\fix_firewall.ps1
#
#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"
$Port = 8000

# Project root = parent of the scripts folder; venv python path.
$root = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "==== PHILO-TECH firewall fix ====" -ForegroundColor Cyan
if (-not (Test-Path $py)) {
    Write-Host "WARN: venv python not found at: $py" -ForegroundColor Yellow
    Write-Host "      Continuing with port rule only." -ForegroundColor Yellow
    $py = $null
}

# (1) Cleanup: remove all previous PHILO-TECH rules (the 14 duplicates) and rebuild.
Get-NetFirewallRule -DisplayName "PHILO-TECH*" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
Write-Host "OK: removed old PHILO-TECH rules (if any)."

# (2) Key fix: delete any inbound BLOCK rule for this project's python.exe.
if ($py) {
    $removed = 0
    foreach ($rule in (Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue)) {
        $prog = ($rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program
        if ($prog -and ($prog -ieq $py -or $prog -like "*\takwim\.venv\Scripts\python*")) {
            $rule | Remove-NetFirewallRule -ErrorAction SilentlyContinue
            $removed++
        }
    }
    Write-Host "OK: removed conflicting python BLOCK rules: $removed"
}

# (3) Allow: one rule for the port, one for the program, all profiles.
New-NetFirewallRule -DisplayName "PHILO-TECH $Port" -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
Write-Host "OK: allowed inbound TCP $Port (all profiles)."
if ($py) {
    New-NetFirewallRule -DisplayName "PHILO-TECH python" -Direction Inbound -Action Allow `
        -Program $py -Profile Any | Out-Null
    Write-Host "OK: allowed program python: $py"
}

# (4) Mark the router network Private (Public blocks inbound by default).
$lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } |
       Select-Object -First 1
if ($lan) {
    Set-NetConnectionProfile -InterfaceIndex $lan.InterfaceIndex -NetworkCategory Private -ErrorAction SilentlyContinue
    Write-Host "OK: set network $($lan.IPAddress) to Private."
}

# (5) Confirm the port is open and listening.
Write-Host ""
if ($lan) {
    $ip = $lan.IPAddress
    Write-Host "Student URL:  http://$ip`:$Port/student" -ForegroundColor Green
    $test = Test-NetConnection -ComputerName $ip -Port $Port -WarningAction SilentlyContinue
    if ($test.TcpTestSucceeded) {
        Write-Host "OK: port open and server listening. Try the URL from a phone on the same Wi-Fi." -ForegroundColor Green
    } else {
        Write-Host "WARN: port not answering yet - start the app (python run.py), then try from the phone." -ForegroundColor Yellow
    }
} else {
    Write-Host "WARN: no LAN address detected - make sure the PC is connected to the router." -ForegroundColor Yellow
}
Write-Host "==== done ====" -ForegroundColor Cyan
