# Start exactly one API server.
#
# `pkill -f runserver` from Git Bash does not kill Windows Python
# processes, so backgrounded starts accumulate and a stale server keeps
# answering with old code. That has cost real time chasing phantom bugs —
# a fix looked broken when it was simply never being served.
#
# This kills by PID until the port is genuinely free, then starts one.
#
#   pwsh scripts/dev-api.ps1            # port 8020
#   pwsh scripts/dev-api.ps1 -Port 8000

param([int]$Port = 8020)

for ($i = 1; $i -le 5; $i++) {
    $pids = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $pids) { break }
    Write-Host "Stopping $($pids -join ', ') on port $Port"
    foreach ($p in $pids) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Error "Port $Port is still held. Stop it before starting."
    exit 1
}

Push-Location "$PSScriptRoot\..\backend"
try {
    & "..\.venv\Scripts\python.exe" manage.py runserver $Port --noreload
}
finally {
    Pop-Location
}
