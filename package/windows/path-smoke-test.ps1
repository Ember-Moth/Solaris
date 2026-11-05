# Directory security checks on a disposable runner; no services or network changes.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Run only on a disposable GitHub Actions runner.' }
$sandbox = Join-Path $env:SystemDrive ("Solaris Path Test " + [Guid]::NewGuid().ToString('N'))
$pathFile = Join-Path $env:RUNNER_TEMP ("solaris-path-" + [Guid]::NewGuid().ToString('N') + '.txt')
$setup = Join-Path $PSScriptRoot 'setup.ps1'
function Check-Path([string] $Path, [bool] $Allowed) {
    [IO.File]::WriteAllText($pathFile, $Path, [Text.Encoding]::Unicode)
    $result = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $setup -Operation prepare-directory -InstallDirectoryFile $pathFile 2>&1
    if (($LASTEXITCODE -eq 0) -ne $Allowed) { throw "Unexpected path result for ${Path}: $result" }
}
try {
    Check-Path $sandbox $true
    $unicode = '{0}{1}' -f [char]0x8DEF, [char]0x5F84
    $safe = Join-Path $sandbox "Custom $unicode"
    Check-Path $safe $true
    Check-Path $safe $true # repair/retry of a directory prepared by this installer
    $acl = Get-Acl -LiteralPath $safe
    if (-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-32-544') {
        throw 'The custom directory was not created with its protected administrator ACL'
    }
    foreach ($invalid in @('C:\', 'relative\Solaris', '\\localhost\C$\Solaris', 'C:\Solaris:stream', 'C:\Solaris.\Child', 'C:\Solaris\..\Other', 'C:\NUL\Solaris')) {
        Check-Path $invalid $false
    }
    $occupied = Join-Path $sandbox 'Occupied'
    $null = New-Item -ItemType Directory -Path $occupied
    Set-Content -LiteralPath (Join-Path $occupied 'unrelated.txt') -Value 'keep'
    Check-Path $occupied $false
    if ((Get-Content -LiteralPath (Join-Path $occupied 'unrelated.txt')) -ne 'keep') { throw 'Unrelated content was changed' }
    $writable = Join-Path $sandbox 'Writable'
    $null = New-Item -ItemType Directory -Path $writable
    $acl = Get-Acl -LiteralPath $writable
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'), [Security.AccessControl.FileSystemRights]::Modify,
        [Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $writable -AclObject $acl
    $before = (Get-Acl -LiteralPath $writable).Sddl
    Check-Path (Join-Path $writable 'Child') $false
    if ((Test-Path (Join-Path $writable 'Child')) -or (Get-Acl -LiteralPath $writable).Sddl -ne $before) { throw 'An unsafe parent was changed' }
    # A silent /D= passes '/' through: Windows reads it as a separator, so the writable
    # parent must not hide behind it as part of one path component.
    Check-Path "$writable/Child" $false
    Check-Path "$sandbox/Slash" $false
    if ((Test-Path (Join-Path $writable 'Child')) -or (Test-Path (Join-Path $sandbox 'Slash'))) { throw 'A path with / was created' }
    $link = Join-Path $sandbox 'Junction'
    $null = New-Item -ItemType Junction -Path $link -Target $safe
    Check-Path (Join-Path $link 'Child') $false
    Check-Path "$link/Child" $false
    if (Test-Path (Join-Path $safe 'Child')) { throw 'A directory link was followed' }
    # Inherit-only entries grant nothing on a folder itself, but the installed files and
    # folders inherit them. Fixtures are owned by Administrators, so only the entry decides.
    function New-Fixture([string] $Path, [string] $Sid = '', [string] $Inheritance = 'ObjectInherit') {
        if (-not (Test-Path -LiteralPath $Path)) { $null = New-Item -ItemType Directory -Path $Path }
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        if ($Sid) {
            $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
                [Security.Principal.SecurityIdentifier]::new($Sid), [Security.AccessControl.FileSystemRights]::Modify,
                [Security.AccessControl.InheritanceFlags]$Inheritance, [Security.AccessControl.PropagationFlags]::InheritOnly,
                [Security.AccessControl.AccessControlType]::Allow))
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
    }
    $control = Join-Path $sandbox 'Existing'
    New-Fixture $control
    Check-Path $control $true
    $inheritOnly = Join-Path $sandbox 'InheritOnly'
    New-Fixture $inheritOnly 'S-1-5-32-545'
    Check-Path $inheritOnly $false
    $folders = Join-Path $sandbox 'InheritOnlyFolders'
    New-Fixture $folders 'S-1-5-32-545' 'ContainerInherit'
    Check-Path $folders $false
    # An earlier installation's folder, as an upgrade finds it.
    $upgrade = Join-Path $sandbox 'Upgrade'
    Check-Path $upgrade $true
    New-Fixture (Join-Path $upgrade 'Lib')
    Check-Path $upgrade $true
    New-Fixture (Join-Path $upgrade 'Lib') 'S-1-5-32-545'
    Check-Path $upgrade $false
    # CREATOR OWNER's entry becomes the elevated installer's owner, as in Program Files.
    $creator = Join-Path $sandbox 'CreatorOwner'
    New-Fixture $creator 'S-1-3-0' 'ContainerInherit, ObjectInherit'
    Check-Path $creator $true
    # A parent's inherit-only entry does not reach the protected folder created in it.
    $parent = Join-Path $sandbox 'InheritOnlyParent'
    New-Fixture $parent 'S-1-5-32-545' 'ContainerInherit, ObjectInherit'
    Check-Path (Join-Path $parent 'Child') $true
    $userOwned = Join-Path $sandbox 'UserOwned'
    $null = New-Item -ItemType Directory -Path $userOwned
    $acl = Get-Acl -LiteralPath $userOwned
    $acl.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
    Set-Acl -LiteralPath $userOwned -AclObject $acl
    Check-Path (Join-Path $userOwned 'Child') $false
    # Even an otherwise protected installation cannot contain an untrusted DLL.
    $unsafeFile = Join-Path $safe 'untrusted.dll'
    Set-Content -LiteralPath $unsafeFile -Value 'fixture'
    $acl = Get-Acl -LiteralPath $unsafeFile
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'), [Security.AccessControl.FileSystemRights]::Write,
        [Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $unsafeFile -AclObject $acl
    Check-Path $safe $false
    Write-Output 'Custom paths: Unicode, protected ACL, retries, unsafe parents/content, / separators, inherit-only entries, user ownership and reparse points passed.'
} finally {
    if (Test-Path -LiteralPath (Join-Path $sandbox 'Junction')) { (Get-Item -LiteralPath (Join-Path $sandbox 'Junction') -Force).Delete() }
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pathFile -Force -ErrorAction SilentlyContinue
}
# Rejected-path probes intentionally exit 1. Do not propagate the last probe's
# exit code after all expectations passed (GitHub's PowerShell wrapper reads it).
exit 0
