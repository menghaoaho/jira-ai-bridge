# Runs under SYSTEM. All executable files and state are administrator protected.
. (Join-Path $PSScriptRoot 'common.ps1')
$lockFile = $null
try {
    $lockFile = [IO.File]::Open((Join-Path $script:RelayData 'supervisor.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch { exit 0 }
$identity = Read-RelayIdentity
if ($null -eq $identity) { $lockFile.Dispose(); exit 1 }
$nginx = Join-Path $script:RelayRoot 'nginx\nginx.exe'
$frpc = Join-Path $script:RelayRoot 'frpc.exe'
$frpProcess = $null
$lastCloud = [DateTime]::MinValue
$allowed = $true
try {
    while ($true) {
        if (((Get-Date) - $lastCloud).TotalSeconds -ge 15) {
            $lastCloud = Get-Date
            try {
                $status = Invoke-RelayApi $identity '/relay-api/v1/status'
                $allowed = [bool]$status.enabled
                Write-RelayJson (Join-Path $script:RelayData 'status.json') @{ checked = (Get-Date).ToString('o'); status = $status.status; detail = $status.health_reason }
            } catch {
                # A network outage does not erase the identity or repeatedly
                # redeem the invitation. FRP handles its own reconnects.
                if ($_.Exception.Data['RelayStatus'] -in @(401, 403)) { $allowed = $false }
                Write-RelayJson (Join-Path $script:RelayData 'status.json') @{ checked = (Get-Date).ToString('o'); status = '等待云端连接'; detail = '请检查公司网络与云端连接；恢复后自动重试。' }
            }
        }
        if (!$allowed) {
            Stop-RelayOwnedProcesses
            Write-RelayJson (Join-Path $script:RelayData 'status.json') @{ checked = (Get-Date).ToString('o'); status = '已停用'; detail = '请联系管理员重新发放安装命令。' }
            Start-Sleep -Seconds 15
            continue
        }
        $master = Get-CimInstance Win32_Process -Filter "Name='nginx.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -eq $nginx }
        if ($null -eq $master) {
            Start-Process -FilePath $nginx -ArgumentList @('-p', ('"' + (Join-Path $script:RelayRoot 'nginx').Replace('\', '/') + '/"'), '-c', ('"' + (Join-Path $script:RelayData 'nginx.conf').Replace('\', '/') + '"')) -WorkingDirectory (Join-Path $script:RelayRoot 'nginx') -WindowStyle Hidden | Out-Null
            Start-Sleep -Seconds 1
        }
        if ($null -eq $frpProcess -or $frpProcess.HasExited) {
            # FRP is a fixed, checksum verified binary. Do not redirect its raw
            # console output to diagnostics, which could contain auth errors.
            $frpProcess = Start-Process -FilePath $frpc -ArgumentList @('-c', ('"' + (Join-Path $script:RelayData 'frpc.toml') + '"')) -WorkingDirectory $script:RelayData -WindowStyle Hidden -PassThru
        }
        Start-Sleep -Seconds 5
    }
} finally { Stop-RelayOwnedProcesses; if ($null -ne $lockFile) { $lockFile.Dispose() } }
