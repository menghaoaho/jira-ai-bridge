param([ValidateSet('Check','Repair','Uninstall')][string]$Mode = 'Check')
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'common.ps1')
    if (!(Test-RelayAdmin)) {
        $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Mode ' + $Mode)
        exit $process.ExitCode
    }
    if ($Mode -eq 'Uninstall') {
        foreach ($dir in @($script:RelayRoot, $script:RelayData)) {
            $marker = Join-Path $dir 'ownership.txt'
            if (!(Test-Path -LiteralPath $marker) -or [IO.File]::ReadAllText($marker) -ne 'JiraRelay/1' -or
                ((Get-Item -LiteralPath $dir).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '无法确认目录归属，已停止卸载。' }
        }
        Write-Host '即将卸载本机 Jira 转发工具。不会卸载其他软件。'
        if ((Read-Host '输入 YES 确认卸载') -cne 'YES') { exit 0 }
        Stop-RelayTask
        Unregister-ScheduledTask -TaskName $script:RelayTask -Confirm:$false -ErrorAction SilentlyContinue
        foreach ($name in @('Jira转发-检查状态.lnk','Jira转发-修复.lnk','Jira转发-卸载.lnk')) {
            $link = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) $name
            if (Test-Path -LiteralPath $link) {
                $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
                if ($shortcut.Arguments -like ('*' + $script:RelayRoot + '*')) { Remove-Item -LiteralPath $link -Force }
            }
        }
        # Server identity remains in the owner's audit history. The local
        # credential is erased; reinstallation needs a new invitation.
        Remove-Item -LiteralPath $script:RelayData -Recurse -Force
        Remove-Item -LiteralPath $script:RelayRoot -Recurse -Force
        Write-Host '已卸载。管理员仍可在设备列表中停用此设备记录。' -ForegroundColor Green
    } else {
        $identity = Read-RelayIdentity
        if ($null -eq $identity) { throw '没有找到本机安装记录，请向管理员获取安装命令。' }
        if ($Mode -eq 'Repair') {
            $config = Invoke-RelayApi $identity '/relay-api/v1/config'
            Stop-RelayTask
            Write-RelayConfiguration $identity $config (Get-RelayLocalPort)
            Register-RelayTask
            Write-Host '已重新配置并启动后台任务，正在确认状态。'
        }
        $status = Invoke-RelayApi $identity '/relay-api/v1/status'
        Write-Host ('云端确认：' + $status.status) -ForegroundColor Cyan
        Write-Host $status.health_reason
        $task = Get-ScheduledTask -TaskName $script:RelayTask -ErrorAction SilentlyContinue
        Write-Host ('后台任务：' + $(if ($null -ne $task) { $task.State } else { '未安装' }))
        $report = @{ generated = (Get-Date).ToString('o'); version = $script:RelayVersion; device_id = $identity.id; cloud_status = $status.status; detail = $status.health_reason; task = $(if ($null -ne $task) { [string]$task.State } else { 'missing' }); os_version = [Environment]::OSVersion.VersionString; is_64_bit = [Environment]::Is64BitOperatingSystem }
        $desktop = [Environment]::GetFolderPath('Desktop')
        $reportPath = Join-Path $desktop 'Jira转发-诊断报告.json'
        Write-RelayJson $reportPath $report
        Write-Host ('脱敏诊断报告已保存：' + $reportPath)
    }
} catch {
    $statusCode = $_.Exception.Data['RelayStatus']
    if ($statusCode -in @(401,403)) { Write-Host '这台电脑的授权已失效，请联系管理员获取新安装命令。' -ForegroundColor Yellow }
    else { Write-Host '本次操作未完成。请检查公司网络；仍有问题时联系管理员。' -ForegroundColor Red }
} finally { Read-Host '按回车关闭' | Out-Null }
