param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]{43}$')][string]$EnrollmentToken,
    [Parameter(Mandatory=$true)][ValidatePattern('^https://[a-zA-Z0-9.-]+(:[0-9]{1,5})?$')][string]$Origin,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedScriptHash,
    [string]$BootstrapPath = ''
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
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
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
        try { $child = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + $encoded) }
        catch { Write-Host '系统授权未完成，尚未安装。可以重新运行原命令。' -ForegroundColor Yellow; exit 1 }
        exit $child.ExitCode
    }
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
        $body = @{code=$EnrollmentToken;device_id=$identity.id;device_secret=$identity.secret;hostname=$env:COMPUTERNAME;version='1.0.0'} | ConvertTo-Json -Compress
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
    Read-Host '按回车关闭' | Out-Null
    if (!$verified) { exit 2 }
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
    Read-Host '按回车关闭' | Out-Null
    exit 1
} finally {
    if ($null -ne $stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    foreach ($dir in $protectedDirectories) { Set-RelayTreeOwner $dir }
    $EnrollmentToken = $null
}
