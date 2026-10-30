# Installs, upgrades and uninstalls a built installer on a disposable GitHub Actions runner
# (docs/architecture.md section 9): CI's windows-installer job and the release workflow run it.
# No TUN and no connection: the service starts and listens on its pipe, the UI opens its window.
#
#     ./package/windows/smoke-test.ps1 -Installer build/windows/Solaris-<version>-windows-x64-setup.exe
#
# 1. A fresh install: files, service (LocalSystem, automatic, restarts on failure, listening),
#    the uninstall entry, the solaris: and clash: schemes, the Start menu shortcut.
# 2. The installed UI starts and opens its window.
# 3. The same installer again, over the running UI: an upgrade closes the UI and restarts the
#    service from the new files.
# 4. Uninstall: no service, task, registration, shortcut or file left; the user data stays.
# 5. Install again and uninstall with /PURGE: the user data goes too.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string] $Installer)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') {
    throw 'This installs a LocalSystem service: run it only on a disposable GitHub Actions runner.'
}

$Installer = (Resolve-Path -LiteralPath $Installer).Path
if ((Split-Path $Installer -Leaf) -notmatch '^Solaris-(\d+\.\d+\.\d+)-windows-x64-setup\.exe$') {
    throw "Not an installer as docs/release.md names it: $Installer"
}
$Version = $Matches[1]
$Root = Join-Path $env:ProgramW6432 'Solaris'
$Client = Join-Path $Root 'Solaris.exe'
$ServiceExecutable = Join-Path $Root 'solaris-service.exe'
$Uninstaller = Join-Path $Root 'uninstall.exe'
$UninstallKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Solaris'
$Shortcut = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Solaris.lnk'
$ServiceName = 'SolarisService'
$Pipe = 'ProtectedPrefix\Administrators\solaris'
$LinkCommand = "`"$Client`" `"%1`""
$ServiceData = Join-Path $env:ProgramData 'Solaris'
$UserData = Join-Path $env:LOCALAPPDATA 'Solaris'
# src/solaris/desktop/login.py's name for this user's task, and one of the same form that
# starts something else.
$Task = "Solaris ($($env:USERDOMAIN)_$($env:USERNAME))"
$OtherTask = 'Solaris (OTHER_someone)'
$ErrorLog = Join-Path $env:TEMP 'Solaris-setup-error.log'

function Invoke-Setup([string] $Path, [string] $Arguments) {
    Write-Output "> $(Split-Path $Path -Leaf) $Arguments"
    # One string: the uninstaller's _?= must come last and unquoted, spaces and all.
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru
    $null = $process.Handle  # keeps the exit code readable after the process exits
    if (-not $process.WaitForExit(300000)) { throw "$Path timed out" }
    if ($process.ExitCode -ne 0) {
        if (Test-Path $ErrorLog) { Get-Content -LiteralPath $ErrorLog | Write-Output }
        throw "$Path $Arguments exited with $($process.ExitCode)"
    }
}

function Get-SolarisService {
    Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
}

function Assert-Installed {
    foreach ($name in @(
            'Solaris.exe', 'solaris-service.exe', 'uninstall.exe', 'LICENSE',
            'geodata\GeoSite.dat', 'geodata\GeoIP.dat', 'solaris\licenses\THIRD_PARTY_NOTICES.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $name) -PathType Leaf)) { throw "$name is missing" }
    }
    $product = (Get-Item -LiteralPath $Client).VersionInfo.ProductVersion
    if ($product -notin @($Version, "$Version.0")) { throw "Solaris.exe is version $product, not $Version" }

    $service = Get-SolarisService
    if (-not $service) { throw "$ServiceName is not installed" }
    $expected = "`"$ServiceExecutable`""
    if ($service.PathName -ne $expected) { throw "$ServiceName runs $($service.PathName), not $expected" }
    if ($service.StartMode -ne 'Auto') { throw "$ServiceName starts $($service.StartMode)" }
    if ($service.StartName -ne 'LocalSystem') { throw "$ServiceName runs as $($service.StartName)" }
    if ($service.State -ne 'Running') { throw "$ServiceName is $($service.State)" }
    $recovery = (& sc.exe qfailure $ServiceName) -join "`n"
    foreach ($delay in 5000, 15000, 60000) {
        if ($recovery -notmatch "RESTART -- Delay = $delay milliseconds") {
            throw "$ServiceName does not restart after $delay ms:`n$recovery"
        }
    }
    # Listening: the service accepts the connection, then drops it, as this is not its UI.
    $client = New-Object System.IO.Pipes.NamedPipeClientStream('.', $Pipe, [System.IO.Pipes.PipeDirection]::InOut)
    try { $client.Connect(20000) } finally { $client.Dispose() }

    $entry = Get-ItemProperty -LiteralPath $UninstallKey
    if ($entry.DisplayVersion -ne $Version -or $entry.InstallLocation -ne $Root) {
        throw "The uninstall entry says $($entry.DisplayVersion) in $($entry.InstallLocation)"
    }
    foreach ($scheme in 'solaris', 'clash') {
        $key = "HKLM:\Software\Classes\$scheme"
        $command = (Get-ItemProperty -LiteralPath "$key\shell\open\command").'(default)'
        if ($command -ne $LinkCommand) { throw "${scheme}: opens $command" }
        if ($null -eq (Get-ItemProperty -LiteralPath $key).'URL Protocol') { throw "${scheme}: is no URL scheme" }
    }
    $shell = New-Object -ComObject WScript.Shell
    if ($shell.CreateShortcut($Shortcut).TargetPath -ne $Client) { throw 'The Start menu shortcut is wrong' }
    Write-Output "Installed $Version; $ServiceName running as process $($service.ProcessId)"
}

function Assert-Removed {
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) { throw "$ServiceName is still there" }
    if (Test-Path -LiteralPath $UninstallKey) { throw 'The uninstall entry is still there' }
    foreach ($scheme in 'solaris', 'clash') {
        if (Test-Path -LiteralPath "HKLM:\Software\Classes\$scheme") { throw "${scheme}: is still registered" }
    }
    if (Test-Path -LiteralPath $Shortcut) { throw 'The Start menu shortcut is still there' }
    if (Get-ScheduledTask -TaskName $Task -TaskPath '\' -ErrorAction SilentlyContinue) {
        throw "The task $Task is still there"
    }
    if (-not (Get-ScheduledTask -TaskName $OtherTask -TaskPath '\' -ErrorAction SilentlyContinue)) {
        throw "The task $OtherTask, which starts another program, was removed"
    }
    # Run in place with _?=, the uninstaller cannot delete itself; anything else is left over.
    $left = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName.Substring($Root.Length + 1) })
    if (($left -join '|') -ne 'uninstall.exe') { throw "Left behind in ${Root}: $($left -join ', ')" }
    Remove-Item -LiteralPath $Uninstaller
    Remove-Item -LiteralPath $Root
}

function Register-Task([string] $Name, [string] $Program) {
    $user = "$($env:USERDOMAIN)\$($env:USERNAME)"
    $action = New-ScheduledTaskAction -Execute $Program -Argument '--background'
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal | Out-Null
}

if ((Test-Path $Root) -or (Test-Path $UninstallKey) -or (Get-SolarisService) -or
    (Test-Path $ServiceData) -or (Test-Path $UserData)) {
    throw 'Solaris is already on this machine; the test needs a fresh one.'
}
$ui = $null
try {
    Write-Output '1. Install'
    Invoke-Setup $Installer '/S'
    Assert-Installed

    Write-Output '2. The UI'
    $ui = Start-Process -FilePath $Client -PassThru
    $null = $ui.Handle
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        Start-Sleep -Milliseconds 500
        $ui.Refresh()
        if ($ui.HasExited) { throw "Solaris.exe exited with $($ui.ExitCode) while starting" }
    } while ($ui.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $deadline)
    if ($ui.MainWindowHandle -eq 0) { throw 'Solaris.exe opened no window' }
    Write-Output "Solaris.exe ($($ui.Id)) opened its window: $($ui.MainWindowTitle)"
    if (-not (Test-Path -LiteralPath $UserData)) { throw "The UI made no $UserData" }

    Write-Output '3. Upgrade over the running UI'
    $before = (Get-SolarisService).ProcessId
    Invoke-Setup $Installer '/S'
    if (-not $ui.WaitForExit(1000)) { throw 'The upgrade left the UI running' }
    Assert-Installed
    if ((Get-SolarisService).ProcessId -eq $before) { throw "$ServiceName was not restarted" }

    Write-Output '4. Uninstall, keeping the user data'
    Register-Task $Task $Client
    Register-Task $OtherTask (Join-Path $env:SystemRoot 'System32\notepad.exe')
    # Whatever the service and the UI have written so far, each data directory has a file.
    $markers = foreach ($directory in $ServiceData, $UserData) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
        $marker = Join-Path $directory 'smoke-test'
        Set-Content -LiteralPath $marker -Value 'user data'
        $marker
    }
    Invoke-Setup $Uninstaller "/S _?=$Root"
    Assert-Removed
    foreach ($marker in $markers) {
        if (-not (Test-Path -LiteralPath $marker)) { throw "Uninstalling removed $marker" }
    }

    Write-Output '5. Install again, then uninstall with /PURGE'
    Invoke-Setup $Installer '/S'
    Assert-Installed
    Invoke-Setup $Uninstaller "/S /PURGE _?=$Root"
    Assert-Removed
    foreach ($purged in $ServiceData, $UserData) {
        if (Test-Path -LiteralPath $purged) { throw "/PURGE left $purged" }
    }
    Write-Output 'Installed, upgraded and uninstalled cleanly.'
} finally {
    if ($null -ne $ui -and -not $ui.HasExited) { $ui.Kill() }
    Unregister-ScheduledTask -TaskName $OtherTask -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Task -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
}
