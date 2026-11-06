# Signs Windows programs with Azure Artifact Signing (docs/decisions.md D42) and checks each
# signature. The release workflow runs it, through scripts/build_windows.py --sign; never a
# developer's machine.
#
#     ./package/windows/sign.ps1 -Directory build/windows/Solaris   # the install directory
#     ./package/windows/sign.ps1 <file> [<file> ...]                # these files
#     ./package/windows/sign.ps1 -Check <file> [<file> ...]         # only check them
#
# -Directory signs every .exe, .dll and .pyd under it that does not carry a signature of its
# own that Windows trusts. Those another publisher signed keep their signature: Microsoft's C++
# runtime, Python's and Qt's libraries. A file Windows finds only in a catalog of the build
# machine's is signed: users' machines may lack the catalog. Solaris.exe and
# solaris-service.exe must end up signed by Solaris. The setup UI's plugin is signed on its
# own, and the uninstaller and the installer through makensis' !uninstfinalize and !finalize
# (package/windows/installer.nsi).
#
# Signing needs what the release workflow sets up: SIGNTOOL, the x64 signtool.exe of the
# Windows SDK build tools; ARTIFACT_SIGNING_DLIB, the x64 Azure.CodeSigning.Dlib.dll of
# Microsoft.ArtifactSigning.Client, with an x64 .NET runtime for it; ARTIFACT_SIGNING_METADATA,
# its metadata.json; and the Azure CLI signed in (azure/login) as an identity allowed to sign
# with the certificate profile. Each file signed is then checked as the service and the updater
# check the UI and the installer (docs/architecture.md §5.3, docs/release.md §3): Windows
# trusts the signature, the certificate is Herta Network LLC's and has the certificate
# profile's extended key usage; and the signature has a timestamp, without which it would
# expire with the certificate in three days. -Check does only that.
[CmdletBinding(DefaultParameterSetName = 'Files')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Directory')][string] $Directory,
    [Parameter(ParameterSetName = 'Files')][switch] $Check,
    [Parameter(Mandatory = $true, ParameterSetName = 'Files', Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $Path
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
# Started under PowerShell 7, as the workflow's steps and what they run start it, Windows
# PowerShell inherits PowerShell 7's PSModulePath and fails to load that version of the module
# Get-AuthenticodeSignature comes from: load its own.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
}

# src/core/internal/product's WindowsSigner and WindowsSignerEKU; tests/test_update.py
# keeps the three copies equal.
$Signer = 'Herta Network LLC'
$SignerEku = '1.3.6.1.4.1.311.97.242074350.326480862.720558710.91084369'
$TimestampServer = 'http://timestamp.acs.microsoft.com'
$Programs = @('.exe', '.dll', '.pyd')  # scripts/build_windows.py's PE_SUFFIXES
# PowerShell names ignore case: $OwnNames, not $Own, beside $own below.
$OwnNames = @('Solaris.exe', 'solaris-service.exe')
# A command line holds 32767 characters; each signtool run gets a batch of files well under.
$BatchLength = 16000

# What keeps $File from being signed as a release signs it; nothing when it is.
function Get-SignatureProblem([string] $File) {
    $signature = Get-AuthenticodeSignature -LiteralPath $File
    if ($signature.Status -ne 'Valid') {
        return "$($signature.Status): $($signature.StatusMessage)"
    }
    if ($signature.SignatureType -ne 'Authenticode') {
        return "signed only in this machine's $($signature.SignatureType)"
    }
    $certificate = $signature.SignerCertificate
    $name = $certificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
    if ($name -ne $Signer) {
        return "signed by '$name', not '$Signer'"
    }
    $usages = @($certificate.Extensions |
            Where-Object { $_ -is [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] } |
            ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
    if ($usages -notcontains $SignerEku) {
        return "signed by '$name' without the extended key usage $SignerEku"
    }
    if ($null -eq $signature.TimeStamperCertificate) {
        return 'the signature has no timestamp'
    }
    return $null
}

function Invoke-SignTool([string[]] $Arguments) {
    & $env:SIGNTOOL @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "signtool $($Arguments[0]) failed with exit code $LASTEXITCODE"
    }
}

# Signs $Files with a timestamp; signtool replaces a signature a file already has.
function Invoke-Signing([string[]] $Files) {
    foreach ($variable in 'SIGNTOOL', 'ARTIFACT_SIGNING_DLIB', 'ARTIFACT_SIGNING_METADATA') {
        $value = [Environment]::GetEnvironmentVariable($variable)
        if (-not $value -or -not (Test-Path -LiteralPath $value -PathType Leaf)) {
            throw "$variable names no file; the release workflow's signing setup provides it"
        }
    }
    $options = @(
        'sign', '/v', '/fd', 'SHA256', '/tr', $TimestampServer, '/td', 'SHA256',
        '/dlib', $env:ARTIFACT_SIGNING_DLIB, '/dmdf', $env:ARTIFACT_SIGNING_METADATA
    )
    $batch = [Collections.Generic.List[string]]::new()
    $length = 0
    foreach ($file in $Files) {
        if ($batch.Count -gt 0 -and $length + $file.Length -gt $BatchLength) {
            Invoke-SignTool ($options + $batch.ToArray())
            $batch.Clear()
            $length = 0
        }
        $batch.Add($file)
        $length += $file.Length + 3
    }
    if ($batch.Count -gt 0) {
        Invoke-SignTool ($options + $batch.ToArray())
    }
}

function Assert-Signed([string[]] $Files) {
    $problems = @(foreach ($file in $Files) {
            $problem = Get-SignatureProblem $file
            if ($problem) { "${file}: $problem" }
        })
    if ($problems.Count -gt 0) {
        foreach ($problem in $problems) { Write-Output "::error::$problem" }
        throw "$($problems.Count) of $($Files.Count) files are not signed as a release signs them"
    }
}

switch ($PSCmdlet.ParameterSetName) {
    'Files' {
        $files = @($Path | ForEach-Object { (Resolve-Path -LiteralPath $_).Path })
        if ($Check) {
            $problems = 0
            foreach ($file in $files) {
                $problem = Get-SignatureProblem $file
                if ($problem) {
                    Write-Output "::error::${file}: $problem"
                    $problems += 1
                } else {
                    Write-Output "Signed by ${Signer}: $file"
                }
            }
            exit [int]($problems -gt 0)
        }
        Invoke-Signing $files
        Assert-Signed $files
        # The chain, the timestamp and its authority, for the log.
        foreach ($file in $files) {
            Invoke-SignTool @('verify', '/pa', '/v', $file)
        }
    }
    'Directory' {
        $root = (Resolve-Path -LiteralPath $Directory).Path
        $files = @(Get-ChildItem -LiteralPath $root -Recurse -File |
                Where-Object { $Programs -contains $_.Extension.ToLowerInvariant() } |
                ForEach-Object FullName | Sort-Object)
        $own = @($OwnNames | ForEach-Object { Join-Path $root $_ })
        foreach ($file in $own) {
            if ($files -notcontains $file) { throw "$root has no $(Split-Path $file -Leaf)" }
        }
        $toSign = [Collections.Generic.List[string]]::new()
        $publishers = @{}
        foreach ($file in $files) {
            $signature = Get-AuthenticodeSignature -LiteralPath $file
            if ($signature.Status -eq 'Valid' -and $signature.SignatureType -eq 'Authenticode' -and $own -notcontains $file) {
                $name = $signature.SignerCertificate.GetNameInfo(
                    [Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
                $publishers[$name] = 1 + [int]$publishers[$name]
            } else {
                $toSign.Add($file)
            }
        }
        foreach ($name in $publishers.Keys | Sort-Object) {
            Write-Output "Keeping the signature of ${name}: $($publishers[$name]) files"
        }
        Write-Output "Signing $($toSign.Count) of $($files.Count) files in $root"
        if ($toSign.Count -gt 0) {
            Invoke-Signing $toSign.ToArray()
        }
        Assert-Signed $toSign.ToArray()
        foreach ($file in $own) {
            Invoke-SignTool @('verify', '/pa', '/v', $file)
        }
        Write-Output "Signed by ${Signer}: $($toSign.Count) files; all $($files.Count) have a signature Windows trusts"
    }
}
