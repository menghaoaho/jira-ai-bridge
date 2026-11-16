param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]{43}$')][string]$EnrollmentToken,
    [Parameter(Mandatory=$true)][ValidatePattern('^https://[a-zA-Z0-9.-]+(:[0-9]{1,5})?$')][string]$Origin,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedScriptHash,
    [string]$BootstrapPath = '',
    [switch]$Unattended
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-RelayInstallPresentation([int]$Step, [int]$ExitCode = -1) {
    $titles = @('准备安装', '检查电脑和网络', '确认安装邀请', '准备安装组件', '设置自动连接', '确认 Jira 连接')
    $details = @('即将自动检查这台电脑。', '正在确认公司网络和云端入口，请保持网络连接。', '正在为这台电脑领取专属授权，无需填写参数。', '正在下载并核对所需组件，通常需要几分钟。', '正在设置后台运行，之后锁屏也能保持连接。', '安装已就绪，正在等待云端确认这台电脑可以连接 Jira。')
    if ($ExitCode -eq 0) { return [pscustomobject]@{title='连接成功，可以使用了';detail='云端已确认这台电脑可以连接 Jira。关闭安装窗口后会继续在后台运行。';progress=100;success=$true} }
    if ($ExitCode -eq 2) { return [pscustomobject]@{title='已安装，连接还需要检查';detail='暂未通过云端确认。请保持公司网络连接，使用桌面的“Jira转发-检查状态”，把报告发给管理员。';progress=90;success=$false} }
    if ($ExitCode -ge 0) { return [pscustomobject]@{title='安装暂未完成';detail='请检查公司网络后重新打开原安装文件。邀请失效时，请联系管理员获取新邀请。';progress=0;success=$false} }
    $index = [Math]::Max(0, [Math]::Min(5, $Step))
    return [pscustomobject]@{title=$titles[$index];detail=$details[$index];progress=($index*17);success=$false}
}

function Start-RelayGuidedInstall {
    $presentation = ${function:Get-RelayInstallPresentation}
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object Windows.Forms.Form
    $form.Text = 'Jira 连接助手 · 一键安装'
    $form.ClientSize = New-Object Drawing.Size(580, 390)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10)
    $form.BackColor = [Drawing.Color]::FromArgb(247,249,253)
    $brand = New-Object Windows.Forms.Label
    $brand.Text = 'Jira 连接助手'
    $brand.Font = New-Object Drawing.Font('Microsoft YaHei UI', 12, [Drawing.FontStyle]::Bold)
    $brand.SetBounds(32, 24, 510, 32)
    $title = New-Object Windows.Forms.Label
    $title.Font = New-Object Drawing.Font('Microsoft YaHei UI', 19, [Drawing.FontStyle]::Bold)
    $title.SetBounds(30, 82, 520, 46)
    $detail = New-Object Windows.Forms.Label
    $detail.SetBounds(32, 143, 510, 66)
    $detail.ForeColor = [Drawing.Color]::FromArgb(90,105,130)
    $progress = New-Object Windows.Forms.ProgressBar
    $progress.SetBounds(32, 225, 510, 14)
    $stepLabel = New-Object Windows.Forms.Label
    $stepLabel.SetBounds(32, 252, 510, 30)
    $stepLabel.Text = '保持电脑开机并连接公司网络，其他操作交给助手。'
    $stepLabel.ForeColor = $detail.ForeColor
    $done = New-Object Windows.Forms.Button
    $done.SetBounds(372, 316, 170, 42)
    $done.Text = '正在安装…'
    $done.Enabled = $false
    $report = New-Object Windows.Forms.Button
    $report.SetBounds(32, 316, 170, 42)
    $report.Text = '复制检查结果'
    $report.Visible = $false
    $form.Controls.AddRange(@($brand,$title,$detail,$progress,$stepLabel,$done,$report))
    $view = Get-RelayInstallPresentation 0
    $title.Text = $view.title; $detail.Text = $view.detail
    $q = { param([string]$s) "'" + $s.Replace("'", "''") + "'" }
    $worker = '$ErrorActionPreference="Stop";[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);$b=[IO.File]::ReadAllBytes(' + (& $q $BootstrapPath) + ');$h=([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))).Replace("-","").ToLowerInvariant();if($h -ne ' + (& $q $ExpectedScriptHash) + '){exit 1};& ([scriptblock]::Create([Text.Encoding]::UTF8.GetString($b))) -Unattended -EnrollmentToken ' + (& $q $EnrollmentToken) + ' -Origin ' + (& $q $Origin) + ' -ExpectedScriptHash ' + (& $q $ExpectedScriptHash) + ' -BootstrapPath ' + (& $q $BootstrapPath)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $info.Arguments = '-NoProfile -NonInteractive -Sta -ExecutionPolicy Bypass -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($worker))
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $info.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $ui = [pscustomobject]@{running=$false;exitCode=1;line=$null;eof=$false;errorTask=$null;lastError='';timer=(New-Object Windows.Forms.Timer)}
    $update = {
        param($value)
        $title.Text=$value.title; $detail.Text=$value.detail; $progress.Value=$value.progress
    }.GetNewClosure()
    $complete = {
        param([int]$code)
        $ui.exitCode=$code; $ui.running=$false; $ui.timer.Stop()
        & $update (& $presentation 0 $code)
        if ($code -eq 0) { $title.ForeColor=[Drawing.Color]::FromArgb(20,145,95); $done.Text='完成并打开 Jira' }
        else { $title.ForeColor=[Drawing.Color]::FromArgb(174,106,36);$done.Text='关闭，稍后重试' }
        $done.Enabled=$true; $report.Visible=$true
        $stepLabel.Text=if($code -eq 0){'锁屏不影响运行；睡眠、关机或离开公司网络时会暂停连接。'}else{'原邀请和本机安装进度会保留，可恢复网络后重试。'}
        if ($code -ne 0 -and $ui.lastError) { $detail.Text=$ui.lastError }
    }.GetNewClosure()
    $ui.timer.Interval=200
    $ui.timer.Add_Tick({
        try {
            for($i=0; $i -lt 40 -and !$ui.eof -and $ui.line.IsCompleted; $i++) {
                $line=$ui.line.GetAwaiter().GetResult()
                if($null -eq $line){$ui.eof=$true;break}
                if($line -match '\[([1-5])/5\]'){& $update (& $presentation ([int]$Matches[1]))}
                if($line -match '(请先连接|无法连接|已过期|已被使用|已撤销|校验失败|安装未完成|需要64位|需要 Windows)' -and $line.Length -le 180 -and $line -notmatch '(https?://|Bearer|client_secret|EnrollmentToken|eyJ)'){$ui.lastError=$line}
                $ui.line=$process.StandardOutput.ReadLineAsync()
            }
            if($process.HasExited -and $ui.eof -and $ui.errorTask.IsCompleted){& $complete $process.ExitCode}
        } catch { & $complete 1 }
    }.GetNewClosure())
    $form.Add_Shown({
        try{
            [void]$process.Start();$ui.running=$true
            $ui.line=$process.StandardOutput.ReadLineAsync();$ui.errorTask=$process.StandardError.ReadToEndAsync()
            $ui.timer.Start()
        }catch{& $complete 1}
    }.GetNewClosure())
    $form.Add_FormClosing({param($sender,$event)if($ui.running){$event.Cancel=$true;[void][Windows.Forms.MessageBox]::Show('安装正在进行，请等待检查完成后再关闭。','Jira 连接助手')}}.GetNewClosure())
    $done.Add_Click({if($ui.exitCode -eq 0){Start-Process $Origin};$form.Close()}.GetNewClosure())
    $report.Add_Click({
        $text='Jira连接助手安装检查'+[Environment]::NewLine+'结果：'+$title.Text+[Environment]::NewLine+'说明：'+$detail.Text+[Environment]::NewLine+'时间：'+(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
        try{[Windows.Forms.Clipboard]::SetText($text);$report.Text='已复制，可发给管理员'}catch{$report.Text='请截图发给管理员'}
    }.GetNewClosure())
    try{[void]$form.ShowDialog();return $ui.exitCode}finally{$ui.timer.Dispose();$process.Dispose();$form.Dispose()}
}
$stage = $null
$backup = $null
$changed = $false
$protectedDirectories = @()
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or ![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw '请使用64位 Windows 10/11 的 Intel 或 AMD 电脑运行。'
    }
    if ($PSVersionTable.PSVersion.Major -lt 5 -or [Environment]::OSVersion.Version.Major -lt 10) { throw '需要 Windows 10/11 和 PowerShell 5.1。' }
    if (!$BootstrapPath) { $BootstrapPath = $PSCommandPath }
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (!$admin) {
        Write-Host '首次安装需要系统授权，请在接下来的弹窗中确认。' -ForegroundColor Cyan
        $q = { param([string]$s) "'" + $s.Replace("'", "''") + "'" }
        # Read, verify, and execute the SAME bytes inside the elevated process;
        # a file replacement between download and elevation fails the hash check.
        $code = '$ErrorActionPreference="Stop";$b=[IO.File]::ReadAllBytes(' + (& $q $BootstrapPath) + ');$s=[Security.Cryptography.SHA256]::Create();$h=([BitConverter]::ToString($s.ComputeHash($b))).Replace("-", "").ToLowerInvariant();if($h -ne ' + (& $q $ExpectedScriptHash) + '){throw "Installation file verification failed"}; & ([scriptblock]::Create([Text.Encoding]::UTF8.GetString($b))) -EnrollmentToken ' + (& $q $EnrollmentToken) + ' -Origin ' + (& $q $Origin) + ' -ExpectedScriptHash ' + (& $q $ExpectedScriptHash) + ' -BootstrapPath ' + (& $q $BootstrapPath)
        if ($Unattended) { $code += ' -Unattended' }
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
        try {
            $start = @{FilePath='powershell.exe';Verb='RunAs';PassThru=$true;ArgumentList=('-NoProfile -Sta -ExecutionPolicy Bypass -EncodedCommand ' + $encoded)}
            if (!$Unattended) { $start.WindowStyle='Hidden' }
            $child = Start-Process @start
            # Retain the handle for Windows PowerShell 5.1 exit-code access.
            # Wait only for the installer, not the browser opened on completion.
            $null = $child.Handle
            $child.WaitForExit()
        }
        catch { Write-Host '系统授权未完成，尚未安装。可以重新运行原命令。' -ForegroundColor Yellow; exit 1 }
        exit $child.ExitCode
    }
    if (!$Unattended) { exit (Start-RelayGuidedInstall) }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host 'Jira 转发 · 自动安装' -ForegroundColor Cyan
    Write-Host '[1/5] 检查公司网络与云端连接...'
    try {
        $probe = Invoke-WebRequest -Uri 'https://jira.example.internal/login.jsp' -UseBasicParsing -TimeoutSec 15 -MaximumRedirection 2
        if ($probe.StatusCode -ne 200 -or $probe.Content -notmatch '(?i)(ajs-version-number|ajs-build-number|name="application-name"\s+content="JIRA|id="jira)') { throw 'not_jira' }
        Invoke-WebRequest -Uri ($Origin + '/relay-oidc/jwks') -UseBasicParsing -TimeoutSec 15 -MaximumRedirection 0 | Out-Null
    } catch { throw '请先连接能访问公司 Jira 的网络，并确认外网连接和证书正常，再重新运行命令。' }
    $root = Join-Path $env:ProgramFiles 'JiraRelay'
    $data = Join-Path $env:ProgramData 'JiraRelay'
    function Protect-RelayDirectory([string]$Path) {
        $trusted = @('S-1-5-18', 'S-1-5-32-544')
        if (Test-Path -LiteralPath $Path) {
            $directory = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
            if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '安装目录指向其他位置，已停止安装。' }
            if (@(Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop).Count -gt 0) {
                # A marker alone is not proof of ownership. Accept an existing tree
                # only if no ordinary user can control its files or directories.
                $pending = New-Object Collections.Queue
                $pending.Enqueue($directory)
                $writeMask = 0x500D0156 # GenericAll/Write, Write, Delete, DeleteChild, WriteDac, WriteOwner.
                while ($pending.Count -gt 0) {
                    $item = $pending.Dequeue()
                    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '已有安装包含指向其他位置的项目，已停止安装。' }
                    $security = Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
                    if ($trusted -notcontains $security.GetOwner([Security.Principal.SecurityIdentifier]).Value) { throw '已有目录或文件不属于系统或管理员，已停止安装。' }
                    foreach ($rule in $security.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
                        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
                            ($rule.FileSystemRights -band $writeMask) -ne 0 -and $trusted -notcontains $rule.IdentityReference.Value) {
                            throw '已有目录或文件允许普通用户修改，已停止安装。'
                        }
                    }
                    if ($item.PSIsContainer) {
                        foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force -ErrorAction Stop) { $pending.Enqueue($child) }
                    }
                }
            }
        }
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        # Replace both pre-existing ownership and the complete DACL.
        & icacls.exe $Path /setowner '*S-1-5-18' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw '无法保护安装目录，请联系管理员。' }
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetSecurityDescriptorSddlForm('D:P(A;OICI;FA;;;S-1-5-18)(A;OICI;FA;;;S-1-5-32-544)', [Security.AccessControl.AccessControlSections]::Access)
        Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop
    }
    function Set-RelayTreeOwner([string]$Path) {
        # Called only after the tree has been protected and its contents verified.
        # Normalize files created by an elevated administrator before SYSTEM runs.
        & icacls.exe $Path /setowner '*S-1-5-18' /T /L | Out-Null
        if ($LASTEXITCODE -ne 0) { throw '无法保护安装文件，请联系管理员。' }
    }
    foreach ($dir in @($root, $data)) {
        if ((Test-Path -LiteralPath $dir) -and ((Get-Item -LiteralPath $dir).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '安装目录指向其他位置，已停止安装。' }
        if ((Test-Path -LiteralPath $dir) -and @(Get-ChildItem -LiteralPath $dir -Force).Count -gt 0 -and
            !(Test-Path -LiteralPath (Join-Path $dir 'ownership.txt'))) { throw '同名目录已有其他文件，已停止安装以保护原文件。' }
        Protect-RelayDirectory $dir
        $protectedDirectories += $dir
        [IO.File]::WriteAllText((Join-Path $dir 'ownership.txt'), 'JiraRelay/1')
        Set-RelayTreeOwner $dir
    }
    # Download a bootstrap helper only as part of a verified bundle. Initial
    # identity creation uses built-in DPAPI, with no external runtime required.
    Add-Type -AssemblyName System.Security
    $identityFile = Join-Path $data 'identity.bin'
    $identity = $null
    if (Test-Path -LiteralPath $identityFile) {
        $bytes = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($identityFile), $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
        $identity = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
        [Array]::Clear($bytes,0,$bytes.Length)
        if ($identity.origin -ne $Origin) { throw '本机已连接其他管理入口，已停止安装。' }
    }
    function Save-InitialIdentity($value) {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Compress))
        $sealed = [Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        [IO.File]::WriteAllBytes($identityFile,$sealed)
        Set-RelayTreeOwner $data
        [Array]::Clear($bytes,0,$bytes.Length)
    }
    $config = $null
    if ($null -ne $identity) {
        try {
            $config = Invoke-RestMethod -Uri ($Origin + '/relay-api/v1/config') -Headers @{Authorization=('Bearer '+$identity.id+'.'+$identity.secret)} -TimeoutSec 15 -MaximumRedirection 0
        } catch {
            $status = 0
            if ($null -ne $_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -notin @(401,403)) { throw '暂时无法读取本机授权，未更换设备记录；请恢复网络后重试。' }
            $pending = $identity.PSObject.Properties.Match('pending').Count -gt 0 -and $identity.pending
            # A redemption response may have been lost. Keep the identity until
            # enrollment is acknowledged, so retry redeems for the same device.
            if (!($status -eq 401 -and $pending)) { $identity = $null }
        }
    }
    if ($null -eq $identity) {
        $random = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($random) } finally { $rng.Dispose() }
        $identity = [pscustomobject]@{id=[guid]::NewGuid().ToString('N');secret=([BitConverter]::ToString($random)).Replace('-','').ToLowerInvariant();origin=$Origin;pending=$true}
        # Persist before redemption so a failed response can resume on this PC.
        Save-InitialIdentity $identity
    }
    Write-Host '[2/5] 领取这台电脑的安装授权...'
    if ($null -eq $config) {
        $body = @{code=$EnrollmentToken;device_id=$identity.id;device_secret=$identity.secret;hostname=$env:COMPUTERNAME;version='1.2.0'} | ConvertTo-Json -Compress
        try { $config = Invoke-RestMethod -Uri ($Origin + '/relay-api/v1/enroll') -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 20 -MaximumRedirection 0 }
        catch {
            $status = 0
            if ($null -ne $_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -in @(400,403,409)) { throw '安装命令已过期、已被使用或已撤销，请向管理员获取新命令。' }
            throw '领取授权暂未完成。本机进度已保存，请恢复网络后重试原命令。'
        } finally { $body = $null; $EnrollmentToken = $null }
    }
    $identity | Add-Member -MemberType NoteProperty -Name pending -Value $false -Force
    Save-InitialIdentity $identity
    if ($config.origin -ne $Origin -or $config.bundle -ne ($Origin + '/relay-install/bundle.zip') -or $config.bundle_sha256 -notmatch '^[0-9a-f]{64}$') { throw '安装包信息不符合预期。' }
    Write-Host '[3/5] 下载并校验安装组件...'
    $stage = Join-Path $root ('.staging-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $archive = Join-Path $stage 'bundle.zip'
    Invoke-WebRequest -Uri $config.bundle -UseBasicParsing -OutFile $archive -TimeoutSec 180 -MaximumRedirection 0
    if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $config.bundle_sha256) { throw '安装包校验失败，未启动下载的程序；请重试。' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try { foreach ($entry in $zip.Entries) { if ($entry.FullName -match '(^[/\\]|(^|[/\\])\.\.([/\\]|$)|:)') { throw '安装包包含无效路径。' } } }
    finally { $zip.Dispose() }
    $unpack = Join-Path $stage 'unpacked'
    Expand-Archive -LiteralPath $archive -DestinationPath $unpack
    foreach ($required in @('common.ps1','supervisor.ps1','manage.ps1','frpc.exe','nginx\nginx.exe','nginx.conf.template')) {
        if (!(Test-Path -LiteralPath (Join-Path $unpack $required))) { throw '安装包不完整，请联系管理员。' }
    }
    # Reuse only verified code from this release. It recognizes this tool's
    # task and processes; legacy installations are left intact for migration.
    . (Join-Path $unpack 'common.ps1')
    if (Test-Path -LiteralPath (Join-Path $root 'common.ps1')) {
        $backup = Join-Path $data ('backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $backup | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $backup 'files') | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $backup 'state') | Out-Null
        foreach ($name in @('common.ps1','supervisor.ps1','manage.ps1','nginx.conf.template','frpc.exe','nginx','manifest.json')) {
            if (Test-Path -LiteralPath (Join-Path $root $name)) { Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $backup 'files') -Recurse -Force }
        }
        foreach ($name in @('nginx.conf','frpc.toml','settings.json','relay-ca.pem','windows-roots.pem')) {
            if (Test-Path -LiteralPath (Join-Path $data $name)) { Copy-Item -LiteralPath (Join-Path $data $name) -Destination (Join-Path $backup 'state') -Force }
        }
    }
    Stop-RelayTask
    $changed = $true
    Copy-Item -Path (Join-Path $unpack '*') -Destination $root -Recurse -Force
    . (Join-Path $root 'common.ps1')
    Write-Host '[4/5] 配置反代、加密连接与开机后台运行...'
    if (!(Test-RelayTcp $config.server_host ([int]$config.server_port))) { throw '无法连接云端转发入口，请联系管理员检查7001端口。' }
    Write-RelayConfiguration $identity $config (Get-RelayLocalPort)
    $frpc = Join-Path $root 'frpc.exe'
    & $frpc verify -c (Join-Path $data 'frpc.toml') *> $null
    if ($LASTEXITCODE -ne 0) { throw '转发配置检查未通过，请联系管理员。' }
    $nginx = Join-Path $root 'nginx\nginx.exe'
    & $nginx -p ((Join-Path $root 'nginx').Replace('\','/')+'/') -c (Join-Path $data 'nginx.conf') -t *> $null
    if ($LASTEXITCODE -ne 0) { throw '本地反代配置检查未通过，请联系管理员。' }
    foreach ($dir in @($root, $data)) { Set-RelayTreeOwner $dir }
    Register-RelayTask
    $shell = New-Object -ComObject WScript.Shell
    foreach ($entry in @(@('检查状态','Check'),@('修复','Repair'),@('卸载','Uninstall'))) {
        $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) ('Jira转发-'+$entry[0]+'.lnk')))
        $link.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $link.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $root 'manage.ps1') + '" -Mode ' + $entry[1]
        $link.WorkingDirectory = $root
        $link.Save()
    }
    Write-Host '[5/5] 等待云端单独确认这台电脑的 Jira 回源...'
    $verified = $false
    for ($attempt = 0; $attempt -lt 18; $attempt++) {
        try {
            $status = Invoke-RelayApi $identity '/relay-api/v1/status'
            if ($status.status -eq '正常转发') { $verified = $true; break }
            if (!$status.enabled) { break }
        } catch { }
        Start-Sleep -Seconds 5
    }
    if ($verified) {
        Write-Host '安装成功：云端已确认这台电脑正在正常转发。关闭本窗口不影响后台运行。' -ForegroundColor Green
    } else {
        Write-Host '安装已完成，但尚未通过云端回源确认，不能视为接入成功。' -ForegroundColor Yellow
        Write-Host '请运行桌面的“Jira转发-检查状态”，将诊断报告发给管理员。'
    }
    Write-Host '电脑需要保持开机、处于唤醒状态，并连接能访问公司 Jira 的网络。'
    if (!$Unattended) { Read-Host '按回车关闭' | Out-Null }
    if (!$verified) { exit 2 }
    exit 0
} catch {
    $installError = $_
    if ($changed -and $null -ne $backup) {
        try {
            Stop-RelayTask
            Copy-Item -Path (Join-Path $backup 'files\*') -Destination $root -Recurse -Force
            Copy-Item -Path (Join-Path $backup 'state\*') -Destination $data -Force
            . (Join-Path $root 'common.ps1')
            Register-RelayTask
            Write-Host '本次安装未完成，已恢复原程序和后台任务。' -ForegroundColor Yellow
        } catch { Write-Host '旧版备份已保留，请联系管理员协助恢复。' -ForegroundColor Yellow }
    }
    # Installer-created errors contain no network response bodies or tokens.
    $message = $installError.Exception.Message
    if ($message -match '(https?://|Bearer|client_secret|EnrollmentToken|eyJ)') { $message = '安装未完成，请检查网络或联系管理员。' }
    Write-Host $message -ForegroundColor Red
    Write-Host '尚未确认安装成功。可保留本机进度，恢复条件后重试原命令。'
    if (!$Unattended) { Read-Host '按回车关闭' | Out-Null }
    exit 1
} finally {
    if ($null -ne $stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    foreach ($dir in $protectedDirectories) { Set-RelayTreeOwner $dir }
    $EnrollmentToken = $null
}
