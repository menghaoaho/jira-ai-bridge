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
[pscustomobject]@{parsed_scripts=$count;native_windows_acl_checks=$nativeAclChecked;real_template_generation=$true;invalid_config_rejected=$true;windows_task_scheduler='requires actual Windows acceptance';output=$OutputDirectory} | ConvertTo-Json
