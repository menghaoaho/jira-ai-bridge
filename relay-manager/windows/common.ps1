# Windows PowerShell 5.1 compatible. Never write credentials to diagnostics.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$script:RelayRoot = Join-Path $env:ProgramFiles 'JiraRelay'
$script:RelayData = Join-Path $env:ProgramData 'JiraRelay'
$script:RelayTask = 'JiraRelaySupervisor'
$script:RelayVersion = '1.0.0'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Test-RelayAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
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
function Write-RelayJson([string]$Path, $Data) {
    $temp = $Path + '.new'
    [IO.File]::WriteAllText($temp, ($Data | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}
function Save-RelayIdentity($Identity) {
    Add-Type -AssemblyName System.Security
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Identity | ConvertTo-Json -Compress))
    $sealed = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    [IO.File]::WriteAllBytes((Join-Path $script:RelayData 'identity.bin'), $sealed)
    [Array]::Clear($bytes, 0, $bytes.Length)
}
function Read-RelayIdentity {
    $file = Join-Path $script:RelayData 'identity.bin'
    if (!(Test-Path -LiteralPath $file)) { return $null }
    Add-Type -AssemblyName System.Security
    $plain = $null
    try {
        $plain = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($file), $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
        return ([Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json)
    } finally { if ($null -ne $plain) { [Array]::Clear($plain, 0, $plain.Length) } }
}
function New-RelayIdentity([string]$Origin) {
    $bytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    return [pscustomobject]@{ id = [guid]::NewGuid().ToString('N'); secret = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant(); origin = $Origin }
}
function Invoke-RelayApi($Identity, [string]$Path, $Body = $null) {
    $options = @{ Uri = $Identity.origin + $Path; TimeoutSec = 12; UseBasicParsing = $true; MaximumRedirection = 0 }
    $options.Headers = @{ Authorization = 'Bearer ' + $Identity.id + '.' + $Identity.secret }
    if ($null -ne $Body) { $options.Method = 'POST'; $options.ContentType = 'application/json'; $options.Body = ($Body | ConvertTo-Json -Compress) }
    try {
        $response = Invoke-WebRequest @options
        return ($response.Content | ConvertFrom-Json)
    } catch {
        $status = 0
        if ($null -ne $_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        # ErrorRecord text may contain form parameters or a request URL. Only
        # normalized status codes cross the boundary into user-visible output.
        $errorObject = New-Object Exception ('云端请求未完成（状态 ' + $status + '）')
        $errorObject.Data['RelayStatus'] = $status
        throw $errorObject
    }
}
function Test-RelayTcp([string]$Computer, [int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect($Computer, $Port, $null, $null)
        if (!$pending.AsyncWaitHandle.WaitOne(4000)) { return $false }
        $client.EndConnect($pending)
        return $true
    } catch { return $false } finally { $client.Dispose() }
}
function Get-RelayLocalPort {
    $used = @([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | ForEach-Object { $_.Port })
    foreach ($port in 18380..18399) { if ($used -notcontains $port) { return $port } }
    throw '本机可用端口不足，请联系管理员。'
}
function Export-RelayRoots([string]$Path) {
    $text = New-Object Text.StringBuilder
    foreach ($store in @('Cert:\LocalMachine\Root', 'Cert:\CurrentUser\Root')) {
        Get-ChildItem $store | ForEach-Object {
            [void]$text.AppendLine('-----BEGIN CERTIFICATE-----')
            [void]$text.AppendLine([Convert]::ToBase64String($_.RawData, [Base64FormattingOptions]::InsertLineBreaks))
            [void]$text.AppendLine('-----END CERTIFICATE-----')
        }
    }
    [IO.File]::WriteAllText($Path, $text.ToString(), (New-Object Text.UTF8Encoding($false)))
}
function Write-RelayConfiguration($Identity, $Config, [int]$LocalPort) {
    if ($Config.device_id -ne $Identity.id -or $Config.origin -ne $Identity.origin -or
        $Config.server_host -notmatch '^[a-zA-Z0-9.-]+$' -or $Config.relay_server_name -ne 'jira-relay' -or
        $Config.token_url -ne ($Identity.origin + '/relay-oidc/token') -or
        [int]$Config.remote_port -lt 18120 -or [int]$Config.remote_port -gt 18199 -or
        [int]$Config.server_port -ne 7001 -or $Config.jira_origin -ne 'https://jira.example.internal') {
        throw '云端配置与本机授权不一致，安装已停止。'
    }
    $nginxRoot = (Join-Path $script:RelayRoot 'nginx').Replace('\', '/')
    $dataRoot = $script:RelayData.Replace('\', '/')
    [IO.File]::WriteAllText((Join-Path $script:RelayData 'relay-ca.pem'), $Config.relay_ca, (New-Object Text.UTF8Encoding($false)))
    Export-RelayRoots (Join-Path $script:RelayData 'windows-roots.pem')
    $template = [IO.File]::ReadAllText((Join-Path $script:RelayRoot 'nginx.conf.template'))
    $originRegex = [regex]::Escape($Identity.origin)
    $template = $template.Replace('@LOCAL_PORT@', [string]$LocalPort).Replace('@DEVICE_ID@', $Identity.id).Replace('@DATA_ROOT@', $dataRoot).Replace('@NGINX_ROOT@', $nginxRoot).Replace('@PUBLIC_ORIGIN@', $Identity.origin).Replace('@ORIGIN_REGEX@', $originRegex)
    [IO.File]::WriteAllText((Join-Path $script:RelayData 'nginx.conf'), $template, (New-Object Text.UTF8Encoding($false)))
    $quote = { param([string]$Value) ConvertTo-Json -InputObject $Value -Compress }
    $frpc = @"
serverAddr = $(& $quote $Config.server_host)
serverPort = $($Config.server_port)
user = $(& $quote $Identity.id)
loginFailExit = false
auth.method = "oidc"
auth.additionalScopes = ["HeartBeats", "NewWorkConns"]
auth.oidc.clientID = $(& $quote $Identity.id)
auth.oidc.clientSecret = $(& $quote $Identity.secret)
auth.oidc.audience = "jira-relay"
auth.oidc.tokenEndpointURL = $(& $quote $Config.token_url)
transport.protocol = "tcp"
transport.tls.enable = true
transport.tls.trustedCaFile = $(& $quote (Join-Path $script:RelayData 'relay-ca.pem'))
transport.tls.serverName = "jira-relay"
transport.heartbeatInterval = 10
transport.heartbeatTimeout = 30
log.to = $(& $quote (Join-Path $script:RelayData 'frpc.log'))
log.level = "warn"
log.maxDays = 3

[[proxies]]
name = "jira"
type = "tcp"
localIP = "127.0.0.1"
localPort = $LocalPort
remotePort = $($Config.remote_port)
"@
    [IO.File]::WriteAllText((Join-Path $script:RelayData 'frpc.toml'), $frpc, (New-Object Text.UTF8Encoding($false)))
    Write-RelayJson (Join-Path $script:RelayData 'settings.json') @{ local_port = $LocalPort; origin = $Identity.origin; device_id = $Identity.id; version = $script:RelayVersion }
    Set-RelayTreeOwner $script:RelayData
}
function Stop-RelayOwnedProcesses {
    # Match executable paths, never kill all nginx/frpc processes on the PC.
    $expected = @((Join-Path $script:RelayRoot 'frpc.exe'), (Join-Path $script:RelayRoot 'nginx\nginx.exe'))
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $expected -contains $_.ExecutablePath } | ForEach-Object {
        $current = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.ProcessId) -ErrorAction SilentlyContinue
        if ($null -ne $current -and $expected -contains $current.ExecutablePath) { Stop-Process -Id $current.ProcessId -Force -ErrorAction SilentlyContinue }
    }
}
function Stop-RelayTask {
    $task = Get-ScheduledTask -TaskName $script:RelayTask -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        $expected = Join-Path $script:RelayRoot 'supervisor.ps1'
        if (!($task.Actions.Arguments -match [regex]::Escape($expected))) { throw '已有同名后台任务不属于本工具，已停止操作。' }
        Stop-ScheduledTask -TaskName $script:RelayTask -ErrorAction SilentlyContinue
    }
    Stop-RelayOwnedProcesses
}
function Register-RelayTask {
    $old = Get-ScheduledTask -TaskName $script:RelayTask -ErrorAction SilentlyContinue
    if ($null -ne $old -and !($old.Actions.Arguments -match [regex]::Escape((Join-Path $script:RelayRoot 'supervisor.ps1')))) { throw '后台任务名称已被其他程序使用。' }
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $script:RelayRoot 'supervisor.ps1') + '"') -WorkingDirectory $script:RelayRoot
    $boot = New-ScheduledTaskTrigger -AtStartup
    $boot.Delay = 'PT15S'
    $watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $script:RelayTask -Action $action -Trigger @($boot, $watch) -Settings $settings -Principal $principal -Description 'Jira 公司网络转发；开机后台运行，可由管理员停用。' -Force | Out-Null
    Start-ScheduledTask -TaskName $script:RelayTask
}
