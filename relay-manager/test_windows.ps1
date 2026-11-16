param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'windows'
$count = 0
foreach ($file in Get-ChildItem -LiteralPath $source -Filter '*.ps1') {
    $tokens = $null; $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($file.Name + ': ' + (($errors | ForEach-Object {$_.Message}) -join '; ')) }
    $count++
}
if (!$OutputDirectory) { $OutputDirectory = Join-Path ([IO.Path]::GetTempPath()) ('relay-ps-check-' + [guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$env:ProgramFiles = $OutputDirectory
$env:ProgramData = $OutputDirectory
. (Join-Path $source 'common.ps1')
$installerTokens = $null; $installerErrors = $null
$installerAst = [Management.Automation.Language.Parser]::ParseFile((Join-Path $source 'install.ps1'), [ref]$installerTokens, [ref]$installerErrors)
foreach ($name in @('Get-RelayInstallPresentation','Start-RelayGuidedInstall')) {
    $definition = $installerAst.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name}, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
if (!(Get-RelayInstallPresentation 5 0).success -or (Get-RelayInstallPresentation 5 2).success -or
    (Get-RelayInstallPresentation 5 1).success -or (Get-RelayInstallPresentation 5).progress -ge 100) { throw 'Only cloud-confirmed exit code zero may show success.' }
# Execute the real elevation branch with a local process double: no UAC,
# installation or browser launch is involved in this regression check.
$elevation = $installerAst.Find({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Clauses[0].Item1.Extent.Text -eq '!$admin'}, $true)
$elevationFixture = Join-Path $OutputDirectory 'elevation-wait.ps1'
$elevationMock = @'
param([int]$ExpectedExit,[string]$TraceFile,[switch]$Unattended)
$ErrorActionPreference='Stop'
$admin=$false; $BootstrapPath='bootstrap.ps1'; $ExpectedScriptHash='f'*64; $EnrollmentToken='a'*43; $Origin='https://example.invalid'
function Write-Host { param($Object,[string]$ForegroundColor) }
function Start-Process {
    param([string]$FilePath,[string]$Verb,[switch]$Wait,[switch]$PassThru,[string]$ArgumentList,[string]$WindowStyle)
    if ($Wait -or !$PassThru -or $FilePath -ne 'powershell.exe' -or $Verb -ne 'RunAs' -or $ArgumentList -notmatch '-Sta .* -EncodedCommand ') { throw 'Unexpected elevated launch options.' }
    if ((!$Unattended -and $WindowStyle -ne 'Hidden') -or ($Unattended -and $WindowStyle)) { throw 'Guided launch visibility changed.' }
    $worker=[pscustomobject]@{ExpectedExit=$ExpectedExit;TraceFile=$TraceFile;HandleRead=$false;Waited=$false}
    $worker | Add-Member ScriptProperty Handle { $this.HandleRead=$true; return 123 }
    $worker | Add-Member ScriptMethod WaitForExit {
        if (!$this.HandleRead) { throw 'The direct process handle was not retained.' }
        $this.Waited=$true
        [IO.File]::WriteAllText($this.TraceFile,'direct child waited')
    }
    $worker | Add-Member ScriptProperty ExitCode {
        if (!$this.Waited) { throw 'Exit code read before direct process completion.' }
        return $this.ExpectedExit
    }
    return $worker
}
'@
[IO.File]::WriteAllText($elevationFixture, $elevationMock + [Environment]::NewLine + $elevation.Extent.Text, (New-Object Text.UTF8Encoding($true)))
foreach ($unattendedCase in @($false,$true)) {
    foreach ($expectedExit in @(0,2,1)) {
        $traceFile=Join-Path $OutputDirectory ('elevation-' + $unattendedCase + '-' + $expectedExit + '.txt')
        & $elevationFixture -ExpectedExit $expectedExit -TraceFile $traceFile -Unattended:$unattendedCase
        if ($LASTEXITCODE -ne $expectedExit -or !(Test-Path -LiteralPath $traceFile) -or
            [IO.File]::ReadAllText($traceFile) -ne 'direct child waited') { throw 'Elevated launcher did not wait only for its direct child and preserve its result.' }
    }
}
$nativeGuiChecked = $false
if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
    Add-Type -AssemblyName System.Windows.Forms
    # Exercise the real window, pipe reader and completion events with a local
    # pinned worker. No installation, network, elevation or tasks are involved.
    foreach ($expectedExit in @(0,2,1)) {
        $BootstrapPath = Join-Path $OutputDirectory ('gui-worker-' + $expectedExit + '.ps1')
        [IO.File]::WriteAllText($BootstrapPath, 'param($EnrollmentToken,$Origin,$ExpectedScriptHash,$BootstrapPath,[switch]$Unattended)' + [Environment]::NewLine + 'Write-Host "[1/5] check"; Write-Host "[5/5] verify"; exit ' + $expectedExit)
        $ExpectedScriptHash = (Get-FileHash $BootstrapPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $EnrollmentToken = 'a'*43; $Origin = 'https://example.invalid'
        $watch = [pscustomobject]@{expired=$false;finished=$false;title='';started=[DateTime]::UtcNow}
        $observer = New-Object Windows.Forms.Timer; $observer.Interval=100
        $observer.Add_Tick({
            foreach ($window in @([Windows.Forms.Application]::OpenForms)) {
                $finish = @($window.Controls | Where-Object {$_ -is [Windows.Forms.Button] -and $_.Left -eq 372})
                if ($finish.Count -eq 1 -and $finish[0].Enabled) {
                    $watch.finished=$true;$watch.title=@($window.Controls | Where-Object {$_.Top -eq 82})[0].Text
                    $window.Close()
                } elseif (([DateTime]::UtcNow-$watch.started).TotalSeconds -gt 20) {
                    $watch.expired=$true
                    # The fixture has no external side effects. A watchdog
                    # failure terminates this test process rather than hangs CI.
                    [Environment]::Exit(1)
                }
            }
        }.GetNewClosure())
        try {
            $observer.Start()
            $actualExit=Start-RelayGuidedInstall
            if ($actualExit -ne $expectedExit -or !$watch.finished -or $watch.expired -or
                $watch.title -ne (Get-RelayInstallPresentation 0 $expectedExit).title) { throw 'Native guided installation did not preserve the worker result.' }
        } finally { $observer.Stop();$observer.Dispose() }
    }
    $nativeGuiChecked=$true
}
$nativeAclChecked = $false
if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
    if (!(Test-RelayAdmin)) { throw 'Run the Windows ACL checks as an administrator.' }
    $aclTestRoot = Join-Path $OutputDirectory 'acl-checks'
    New-Item -ItemType Directory -Path $aclTestRoot | Out-Null
    $empty = Join-Path $aclTestRoot 'empty'
    New-Item -ItemType Directory -Path $empty | Out-Null
    # Model a directory pre-created by an ordinary user, including an explicit
    # FullControl ACE that /grant:r alone would have left in place.
    & icacls.exe $empty /grant '*S-1-5-32-545:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare the isolated ACL fixture.' }
    & icacls.exe $empty /setowner '*S-1-5-32-545' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare the isolated ACL fixture.' }
    Protect-RelayDirectory $empty
    $security = Get-Acl -LiteralPath $empty
    $rules = @($security.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($security.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-18' -or
        !$security.AreAccessRulesProtected -or $rules.Count -ne 2) { throw 'Empty-directory takeover did not replace the owner and DACL.' }
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $rule = @($rules | Where-Object { $_.IdentityReference.Value -eq $sid })
        if ($rule.Count -ne 1 -or $rule[0].IsInherited -or
            $rule[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            $rule[0].FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
            $rule[0].InheritanceFlags -ne ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit)) {
            throw 'Protected DACL contains unexpected permissions.'
        }
    }
    $forged = Join-Path $aclTestRoot 'forged'
    New-Item -ItemType Directory -Path $forged | Out-Null
    [IO.File]::WriteAllText((Join-Path $forged 'ownership.txt'), 'JiraRelay/1')
    & icacls.exe $forged /setowner '*S-1-5-32-545' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare the forged-marker fixture.' }
    $rejected = $false
    try { Protect-RelayDirectory $forged } catch { $rejected = $true }
    if (!$rejected) { throw 'A nonempty ordinary-user directory with a forged marker was accepted.' }
    if ((Get-Acl -LiteralPath $forged).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-32-545') { throw 'An untrusted tree was modified before rejection.' }
    # A normal installation and administrator repair must remain upgradable.
    [IO.File]::WriteAllText((Join-Path $empty 'ownership.txt'), 'JiraRelay/1')
    Set-RelayTreeOwner $empty
    Protect-RelayDirectory $empty
    $nativeAclChecked = $true
} else {
    # This OS boundary is exercised by the real Windows checks above.
    function Set-RelayTreeOwner([string]$Path) { }
}
New-Item -ItemType Directory -Path $script:RelayRoot -Force | Out-Null
New-Item -ItemType Directory -Path $script:RelayData -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'nginx.conf.template') -Destination $script:RelayRoot
# Certificate stores exist only on Windows. Mock just this OS boundary, while
# running the real config generation logic under PowerShell itself.
function Export-RelayRoots([string]$Path) { [IO.File]::WriteAllText($Path,'PUBLIC TEST ROOT') }
$identity = [pscustomobject]@{id=('a'*32);secret=('b'*64);origin='https://203.0.113.10'}
$config = [pscustomobject]@{device_id=$identity.id;origin=$identity.origin;server_host='203.0.113.10';server_port=7001;remote_port=18120;relay_server_name='jira-relay';relay_ca='PUBLIC TEST ROOT';token_url=($identity.origin+'/relay-oidc/token');jira_origin='https://jira.example.internal'}
Write-RelayConfiguration $identity $config 18380
$nginx = [IO.File]::ReadAllText((Join-Path $script:RelayData 'nginx.conf'))
if ($nginx -match '@[A-Z_]+@' -or $nginx -notmatch 'listen 127.0.0.1:18380;' -or $nginx -notmatch 'proxy_ssl_verify on;') { throw 'nginx generated config invalid' }
$config.remote_port = 22
$rejected = $false
try { Write-RelayConfiguration $identity $config 18380 } catch { $rejected = $true }
if (!$rejected) { throw 'unauthorized remote port accepted' }
[pscustomobject]@{parsed_scripts=$count;native_windows_gui_checks=$nativeGuiChecked;native_windows_acl_checks=$nativeAclChecked;real_template_generation=$true;invalid_config_rejected=$true;windows_task_scheduler='requires actual Windows acceptance';output=$OutputDirectory} | ConvertTo-Json
