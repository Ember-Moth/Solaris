# NSIS for the installer builds on the GitHub Actions runners, from Chocolatey's community feed.
# When the feed fails (a 504, say), choco installs nothing and still exits 0 ("installed 0/0
# packages"), and the build only finds out later that makensis is missing. So this tries again
# and stops here unless makensis is there and runs.
[CmdletBinding()]
param(
    [int] $Attempts = 4
)
$ErrorActionPreference = 'Stop'
$makensis = Join-Path ${env:ProgramFiles(x86)} 'NSIS\makensis.exe'  # build_windows.find_makensis
for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    choco install nsis --yes --no-progress
    $code = $LASTEXITCODE
    if ($code -eq 0 -and (Test-Path -LiteralPath $makensis)) {
        & $makensis /VERSION
        if ($LASTEXITCODE -eq 0) { exit 0 }
    }
    if ($attempt -lt $Attempts) {
        $wait = 20 * $attempt
        Write-Warning "NSIS is not installed (attempt $attempt of $Attempts, choco exit $code); trying again in $wait s"
        Start-Sleep -Seconds $wait
    }
}
Write-Error "NSIS could not be installed in $Attempts attempts; makensis is not at $makensis"
exit 1
