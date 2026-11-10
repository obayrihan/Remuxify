# Run this reviewed installer from an Administrator PowerShell window.
# Config backups are saved under the application backups folder.
param(
    [string]$PythonExecutable = 'python.exe',
    [string]$CloudflaredExecutable = 'cloudflared.exe'
)
$ErrorActionPreference = 'Stop'
$forgeRoot = $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator PowerShell is required to install the Jellyfin link, boot task, and tunnel route.'
}
$forgePython = (Get-Command -Name $PythonExecutable -ErrorAction Stop).Source
$forgeCloudflared = (Get-Command -Name $CloudflaredExecutable -ErrorAction Stop).Source
$forgeRuntimeConfig = Get-Content -LiteralPath (Join-Path $forgeRoot 'config.json') -Raw | ConvertFrom-Json
$forgeTunnelConfig = $forgeRuntimeConfig.cloudflare_config
if (-not $forgeTunnelConfig -or -not [IO.Path]::IsPathRooted($forgeTunnelConfig)) { throw 'Configure an absolute cloudflare_config path before using the optional tunnel installer.' }
Set-Location -LiteralPath $forgeRoot

# Repair/set explicit DACLs first, before stopping the working server. This also
# recovers child files left inaccessible by the first installer version.
& $forgePython -B "$forgeRoot\permissions.py"
if ($LASTEXITCODE -ne 0) { throw 'Could not verify application file permissions. The existing worker was not stopped.' }

& $forgePython -m py_compile app.py core.py worker.py run.py integration.py
if ($LASTEXITCODE -ne 0) { throw 'Source compilation failed.' }
& $forgePython integration.py
if ($LASTEXITCODE -ne 0) { throw 'Could not stage integration files.' }
& $forgeCloudflared tunnel --config "$forgeRoot\staging\cloudflared-config.yml" ingress validate
if ($LASTEXITCODE -ne 0) { throw 'Cloudflare rejected the staged ingress configuration.' }

# Refuse to interrupt work that might be replacing a file.
& $forgePython -c "import sqlite3,sys; d=sqlite3.connect('data/forge.db'); n=d.execute('SELECT count(*) FROM jobs WHERE state IN (?,?,?)',('encoding','validating','committing')).fetchone()[0]; sys.exit(1 if n else 0)"
if ($LASTEXITCODE -ne 0) { throw 'A job is running. Pause the queue and wait for it to finish, then run the installer again.' }

# Stop only this application's current listener, after verifying its command line.
$forgeListeners = Get-NetTCPConnection -LocalPort 5056 -State Listen -ErrorAction SilentlyContinue
foreach ($forgeListener in $forgeListeners) {
    $forgeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($forgeListener.OwningProcess)"
    if ($forgeProcess.CommandLine.IndexOf((Join-Path $forgeRoot 'run.py'), [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw 'Port 5056 is occupied by another application. Nothing was stopped.'
    }
    Stop-Process -Id $forgeListener.OwningProcess -Force
}

& $forgePython integration.py --apply
if ($LASTEXITCODE -ne 0) { throw 'Could not apply integration. Restore files are in the backups folder.' }

$forgeRepair = New-ScheduledTaskAction -Execute $forgePython -Argument ('"{0}" --ensure-menu' -f (Join-Path $forgeRoot 'integration.py')) -WorkingDirectory $forgeRoot
$forgeRun = New-ScheduledTaskAction -Execute $forgePython -Argument ('"{0}"' -f (Join-Path $forgeRoot 'run.py')) -WorkingDirectory $forgeRoot
$forgeTrigger = New-ScheduledTaskTrigger -AtStartup
$forgeTrigger.Delay = 'PT30S'
$forgePrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$forgeSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Priority 7
Register-ScheduledTask -TaskName 'Jellyfin Media Forge' -Description 'Admin-only NVENC conversion dashboard; durable queue and validated replacement.' -Action @($forgeRepair,$forgeRun) -Trigger $forgeTrigger -Principal $forgePrincipal -Settings $forgeSettings -Force | Out-Null
Start-ScheduledTask -TaskName 'Jellyfin Media Forge'

$forgeHealthy = $false
for ($forgeTry = 0; $forgeTry -lt 30; $forgeTry++) {
    Start-Sleep -Seconds 2
    try {
        $forgeHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:5056/healthz' -TimeoutSec 2
        if ($forgeHealth.ok) { $forgeHealthy = $true; break }
    } catch { }
}
if (-not $forgeHealthy) { throw 'The boot task was installed but the server did not become healthy. Check data\server.log and Task Scheduler.' }

# Restart only the tunnel to load the new path route; Jellyfin itself stays running.
Restart-Service -Name cloudflared
Write-Host ('Installed: ' + $forgeRuntimeConfig.public_url) -ForegroundColor Green
Write-Host 'Local: http://localhost:5056/forge/'
Write-Host 'Refresh Jellyfin and choose Media Forge in its menu. Automatic queuing remains under your control.'
