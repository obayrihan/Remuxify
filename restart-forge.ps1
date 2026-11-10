#requires -RunAsAdministrator
# Restart only the existing Forge task, preserving settings and queued-job records.
param(
    [switch]$WaitForIdle,
    [string]$PythonExecutable = 'python.exe',
    [switch]$AllowPausedQueue,
    [ValidateRange(1, 360)][int]$IdleTimeoutMinutes = 90
)
$ErrorActionPreference = 'Stop'
$forgeRoot = $PSScriptRoot
$forgePython = (Get-Command -Name $PythonExecutable -ErrorAction Stop).Source
$forgeReceipt = Join-Path $forgeRoot 'data\restart-result.json'
try {
    Set-Location -LiteralPath $forgeRoot
    $forgeTask = Get-ScheduledTask -TaskName 'Jellyfin Media Forge' -ErrorAction Stop
    if (-not ($forgeTask.Actions | Where-Object { $_.Arguments.IndexOf((Join-Path $forgeRoot 'run.py'), [StringComparison]::OrdinalIgnoreCase) -ge 0 })) {
        throw 'The existing task does not launch the expected Forge worker. Nothing was stopped.'
    }
    $forgeIdleDeadline = [DateTime]::UtcNow.AddMinutes($IdleTimeoutMinutes)
    $forgePendingReceipt = Join-Path $forgeRoot 'data\restart-pending.json'
    $forgeIdlePasses = 0
    $forgeAllowPausedValue = if ($AllowPausedQueue) { '1' } else { '0' }
    while ($true) {
        & $forgePython -B -c "import json,sqlite3,sys; allow=sys.argv[1]=='1'; paused=json.load(open('config.json',encoding='utf-8-sig')).get('queue_paused') is True; sys.exit(3) if allow and not paused else None; states=('encoding','validating','committing') if allow else ('queued','waiting','encoding','validating','committing'); d=sqlite3.connect('file:data/forge.db?mode=ro',uri=True); n=d.execute('SELECT count(*) FROM jobs WHERE state IN ('+','.join('?' for _ in states)+')',states).fetchone()[0]; sys.exit(2 if n else 0)" $forgeAllowPausedValue
        if ($LASTEXITCODE -eq 0) {
            $forgeIdlePasses++
            if ((-not $WaitForIdle -and -not $AllowPausedQueue) -or $forgeIdlePasses -ge 2) { break }
        } elseif ($LASTEXITCODE -eq 2) {
            $forgeIdlePasses = 0
            if (-not $WaitForIdle) { throw 'There are pending or running jobs. Nothing was restarted; pause and inspect the queue first.' }
            @{ status = 'waiting_for_idle'; checked_at_utc = [DateTime]::UtcNow.ToString('o'); jobs_interrupted = $false } |
                ConvertTo-Json | Set-Content -LiteralPath $forgePendingReceipt -Encoding UTF8
        } elseif ($LASTEXITCODE -eq 3) {
            throw 'Pause the queue in Forge before using AllowPausedQueue. Nothing was stopped.'
        } else {
            throw 'Could not safely inspect the queue. Nothing was stopped.'
        }
        if ([DateTime]::UtcNow -ge $forgeIdleDeadline) { throw 'Timed out waiting for an idle queue. Nothing was restarted.' }
        Start-Sleep -Seconds 5
    }
    $forgeOldListeners = @(Get-NetTCPConnection -LocalPort 5056 -State Listen -ErrorAction SilentlyContinue)
    $forgeOldIds = @($forgeOldListeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $forgeHash = (Get-FileHash -LiteralPath "$forgeRoot\worker.py" -Algorithm SHA256).Hash
    $forgeSourceHashes = @{}
    foreach ($forgeSourceFile in @('worker.py', 'core.py', 'telemetry.py', 'qbittorrent.py', 'app.py', 'static\app.js', 'static\index.html')) {
        $forgeSourceHashes[$forgeSourceFile] = (Get-FileHash -LiteralPath (Join-Path $forgeRoot $forgeSourceFile) -Algorithm SHA256).Hash
    }
    Stop-ScheduledTask -TaskName 'Jellyfin Media Forge'
    for ($forgeTry = 0; $forgeTry -lt 20; $forgeTry++) {
        if (-not (Get-NetTCPConnection -LocalPort 5056 -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Get-NetTCPConnection -LocalPort 5056 -State Listen -ErrorAction SilentlyContinue) {
        throw 'The old listener did not stop. A second worker was not launched.'
    }
    Start-ScheduledTask -TaskName 'Jellyfin Media Forge'
    $forgeReady = $false
    for ($forgeTry = 0; $forgeTry -lt 30; $forgeTry++) {
        Start-Sleep -Seconds 1
        try {
            $forgeHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:5056/healthz' -TimeoutSec 2
            if ($forgeHealth.ok -and $forgeHealth.service -eq 'Media Forge') { $forgeReady = $true; break }
        } catch { }
    }
    if (-not $forgeReady) { throw 'Forge did not become healthy after restart. Check Task Scheduler and data\server.log.' }
    $forgeNewIds = @(Get-NetTCPConnection -LocalPort 5056 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($forgeNewIds | Where-Object { $forgeOldIds -contains $_ }) { throw 'The listener process did not change; restart verification failed.' }
    if ((Get-FileHash -LiteralPath "$forgeRoot\worker.py" -Algorithm SHA256).Hash -ne $forgeHash) { throw 'Worker source changed during restart; inspect the loaded version.' }
    foreach ($forgeSourceFile in $forgeSourceHashes.Keys) {
        if ((Get-FileHash -LiteralPath (Join-Path $forgeRoot $forgeSourceFile) -Algorithm SHA256).Hash -ne $forgeSourceHashes[$forgeSourceFile]) {
            throw 'Application source changed during restart; inspect the loaded version.'
        }
    }
    @{
        success = $true
        restarted_at_utc = [DateTime]::UtcNow.ToString('o')
        previous_process_ids = $forgeOldIds
        process_ids = $forgeNewIds
        worker_sha256 = $forgeHash
        source_sha256 = $forgeSourceHashes
        task_state = (Get-ScheduledTask -TaskName 'Jellyfin Media Forge').State.ToString()
        task_user = $forgeTask.Principal.UserId
        jobs_requeued = $false
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $forgeReceipt -Encoding UTF8
    @{ status = 'completed'; completed_at_utc = [DateTime]::UtcNow.ToString('o'); jobs_interrupted = $false } |
        ConvertTo-Json | Set-Content -LiteralPath $forgePendingReceipt -Encoding UTF8
} catch {
    @{ success = $false; error = $_.Exception.Message; time_utc = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $forgeReceipt -Encoding UTF8
    throw
}
