# Installs, upgrades and uninstalls a built installer on a disposable GitHub Actions runner
# (docs/architecture.md section 9): CI's windows-installer job and the release workflow run it.
# No TUN and no connection: the service starts and listens on its pipe, the UI opens its window.
#
#     ./package/windows/smoke-test.ps1 -Installer build/windows/Solaris-<version>-windows-x64-setup.exe
#
# 1. A fresh install: files, service (LocalSystem, automatic, restarts on failure, listening),
#    the uninstall entry, the solaris: and clash: schemes, the Start menu shortcut.
# 2. Solaris.exe --quit with no UI running does nothing; the installed UI starts and opens its
#    window.
# 3. The same installer again, over the running UI: the UI quits when asked (not ended), and
#    the upgrade restarts the service from the new files.
# 4. Uninstall over the running UI, which quits likewise: no service, task, registration,
#    shortcut or file left; the user data stays.
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
$DumpRoot = 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps'
$UiLock = Join-Path $UserData 'ui.lock'  # src/solaris/desktop/instance.py
# src/solaris/desktop/login.py's name for this user's task, and one of the same form that
# starts something else.
$Task = "Solaris ($($env:USERDOMAIN)_$($env:USERNAME))"
$OtherTask = 'Solaris (OTHER_someone)'
$ErrorLog = Join-Path $env:TEMP 'Solaris-setup-error.log'

# A check that fails is reported and the test goes on, so one run shows every problem; the
# test fails at the end. What the later steps cannot do without still stops it at once.
$Failures = [System.Collections.Generic.List[string]]::new()
function Expect([bool] $Condition, [string] $Problem) {
    if (-not $Condition) {
        Write-Output "::error::$Problem"
        $Failures.Add($Problem)
    }
}

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

# The first install exercises the real custom UI -> NSIS engine bridge. The later
# upgrade/uninstall cases continue to exercise /S. Only run on this disposable runner.
function Invoke-InteractiveSetup([string] $Path) {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $process = Start-Process -FilePath $Path -PassThru
    $null = $process.Handle
    try {
        $rootElement = [System.Windows.Automation.AutomationElement]::RootElement
        $pidCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $process.Id)
        $classCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty, 'SolarisSetupWindow')
        $condition = [System.Windows.Automation.AndCondition]::new($pidCondition, $classCondition)
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        $window = $null
        while (-not $window -and [DateTime]::UtcNow -lt $deadline) {
            $window = $rootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $condition)
            if (-not $window) { Start-Sleep -Milliseconds 100 }
        }
        if (-not $window) { throw 'The custom installer window did not open' }
        $actionCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1001')
        $closeCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1002')
        $action = $null
        while (-not $action -and [DateTime]::UtcNow -lt $deadline) {
            $candidate = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $actionCondition)
            if ($candidate -and $candidate.Current.Name) { $action = $candidate }
            else { Start-Sleep -Milliseconds 100 }
        }
        if (-not $action) { throw 'The custom installer has no accessible Install button' }
        $initialName = $action.Current.Name
        $action.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        $deadline = [DateTime]::UtcNow.AddMinutes(5)
        while (-not $process.HasExited -and $action.Current.Name -eq $initialName -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
        }
        $openChinese = '{0}{1} Solaris' -f [char]0x6253, [char]0x5F00
        if ($process.HasExited -or $action.Current.Name -notin @('Open Solaris', $openChinese)) {
            if (Test-Path $ErrorLog) { Get-Content -LiteralPath $ErrorLog | Write-Output }
            throw 'The custom installer did not reach its successful completion page'
        }
        # Close, so the following --quit test still starts with no UI and no user data.
        $close = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $closeCondition)
        $close.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        if (-not $process.WaitForExit(30000) -or $process.ExitCode -ne 0) { throw 'The custom installer did not exit successfully' }
    } finally {
        if (-not $process.HasExited) { $process.Kill() }
    }
}

function Get-SolarisService {
    Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
}

# Where shortcuts named Solaris*.lnk are, in every Start menu: tells a misplaced shortcut from
# a missing one.
function Find-Shortcuts {
    $menus = @(
        (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu'),
        (Join-Path $env:SystemDrive 'Users\*\AppData\Roaming\Microsoft\Windows\Start Menu'),
        (Join-Path $env:SystemRoot 'System32\config\systemprofile\AppData\Roaming\Microsoft\Windows\Start Menu')
    )
    @(Get-ChildItem -Path $menus -Recurse -Filter 'Solaris*.lnk' -Force -ErrorAction SilentlyContinue |
            ForEach-Object FullName)
}

function Assert-Installed {
    foreach ($image in @('Solaris.exe', 'solaris-service.exe')) {
        $dump = Get-ItemProperty -LiteralPath (Join-Path $DumpRoot $image)
        Expect ($dump.DumpType -eq 1 -and $dump.DumpCount -eq 5) "Unexpected WER policy for $image"
        Expect ($dump.SolarisInstallDirectory -eq $Root) "Unowned WER policy for $image"
    }
    foreach ($name in @(
            'Solaris.exe', 'solaris-service.exe', 'libcronet.dll', 'uninstall.exe', 'LICENSE',
            'geodata\GeoSite.dat', 'geodata\GeoIP.dat', 'solaris\licenses\THIRD_PARTY_NOTICES.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $name) -PathType Leaf)) { throw "$name is missing" }
    }
    $product = (Get-Item -LiteralPath $Client).VersionInfo.ProductVersion
    Expect ($product -in @($Version, "$Version.0")) "Solaris.exe is version $product, not $Version"

    $service = Get-SolarisService
    if (-not $service) { throw "$ServiceName is not installed" }
    $expected = "`"$ServiceExecutable`""
    Expect ($service.PathName -eq $expected) "$ServiceName runs $($service.PathName), not $expected"
    Expect ($service.StartMode -eq 'Auto') "$ServiceName starts $($service.StartMode)"
    Expect ($service.StartName -eq 'LocalSystem') "$ServiceName runs as $($service.StartName)"
    Expect ($service.State -eq 'Running') "$ServiceName is $($service.State)"
    $serviceLog = Get-Content -LiteralPath (Join-Path $ServiceData 'service.log') -Raw
    Expect ($serviceLog -notmatch 'Naive native runtime unavailable') 'The service could not load its packaged Cronet DLL'
    $recovery = (& sc.exe qfailure $ServiceName) -join "`n"
    foreach ($delay in 5000, 15000, 60000) {
        Expect ($recovery -match "RESTART -- Delay = $delay milliseconds") "$ServiceName does not restart after $delay ms:`n$recovery"
    }
    # Listening: the service accepts the connection, then drops it, as this is not its UI.
    # Not $client: PowerShell names ignore case, and $Client is the UI's path checked below.
    $pipeClient = New-Object System.IO.Pipes.NamedPipeClientStream('.', $Pipe, [System.IO.Pipes.PipeDirection]::InOut)
    try {
        $pipeClient.Connect(20000)
    } catch {
        Expect $false "Nothing listens on \\.\pipe\${Pipe}: $($_.Exception.Message)"
    } finally {
        $pipeClient.Dispose()
    }

    $entry = Get-ItemProperty -LiteralPath $UninstallKey
    Expect ($entry.DisplayVersion -eq $Version -and $entry.InstallLocation -eq $Root) "The uninstall entry says $($entry.DisplayVersion) in $($entry.InstallLocation)"
    foreach ($scheme in 'solaris', 'clash') {
        $key = "HKLM:\Software\Classes\$scheme"
        $command = (Get-ItemProperty -LiteralPath "$key\shell\open\command").'(default)'
        Expect ($command -eq $LinkCommand) "${scheme}: opens $command"
        Expect ($null -ne (Get-ItemProperty -LiteralPath $key).'URL Protocol') "${scheme}: is no URL scheme"
    }
    if (Test-Path -LiteralPath $Shortcut) {
        $target = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut).TargetPath
        Expect ($target -eq $Client) "The Start menu shortcut opens '$target', not $Client"
    } else {
        Expect $false "No shortcut at $Shortcut; Solaris shortcuts found: $((Find-Shortcuts) -join ', ')"
    }
    Write-Output "Installed $Version; $ServiceName running as process $($service.ProcessId)"
}

function Assert-Removed {
    foreach ($image in @('Solaris.exe', 'solaris-service.exe')) {
        Expect (-not (Test-Path -LiteralPath (Join-Path $DumpRoot $image))) "WER policy remains for $image"
    }
    Expect (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) "$ServiceName is still there"
    Expect (-not (Test-Path -LiteralPath $UninstallKey)) 'The uninstall entry is still there'
    foreach ($scheme in 'solaris', 'clash') {
        Expect (-not (Test-Path -LiteralPath "HKLM:\Software\Classes\$scheme")) "${scheme}: is still registered"
    }
    $shortcuts = Find-Shortcuts
    Expect ($shortcuts.Count -eq 0) "Shortcuts are still there: $($shortcuts -join ', ')"
    Expect (-not (Get-ScheduledTask -TaskName $Task -TaskPath '\' -ErrorAction SilentlyContinue)) "The task $Task is still there"
    Expect ([bool](Get-ScheduledTask -TaskName $OtherTask -TaskPath '\' -ErrorAction SilentlyContinue)) "The task $OtherTask, which starts another program, was removed"
    # Run in place with _?=, the uninstaller cannot delete itself; anything else is left over.
    $left = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName.Substring($Root.Length + 1) })
    Expect (($left -join '|') -eq 'uninstall.exe') "Left behind in ${Root}: $($left -join ', ')"
    Remove-Item -LiteralPath $Root -Recurse -Force
}

# Starts the installed UI as the user and waits for its window; the process is $ui.
function Start-UI {
    $script:ui = Start-Process -FilePath $Client -PassThru
    $null = $ui.Handle  # keeps the exit code readable after the process exits
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        Start-Sleep -Milliseconds 500
        $ui.Refresh()
    } while (-not $ui.HasExited -and $ui.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $deadline)
    if ($ui.HasExited) {
        Expect $false "Solaris.exe exited with $($ui.ExitCode) while starting"
    } else {
        Expect ($ui.MainWindowHandle -ne 0) 'Solaris.exe opened no window in 60 seconds'
        Write-Output "Solaris.exe ($($ui.Id)) is running; its window: '$($ui.MainWindowTitle)'"
    }
}

# setup.ps1 asks the UI to quit before it ends it: one that quit exits with 0 and lets go of
# its lock; one that was ended exits with -1 and leaves the lock file behind.
function Assert-QuitByItself([string] $During) {
    if (-not ($ui.HasExited -or $ui.WaitForExit(1000))) {
        Expect $false "$During left the UI running"
        return
    }
    Expect ($ui.ExitCode -eq 0) "The UI was ended during $During (exit code $($ui.ExitCode)) rather than quitting"
    Expect (-not (Test-Path -LiteralPath $UiLock)) "The UI left $UiLock behind during $During"
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
    Invoke-InteractiveSetup $Installer
    Assert-Installed

    Write-Output '2. The UI'
    $quit = Start-Process -FilePath $Client -ArgumentList '--quit' -PassThru
    $null = $quit.Handle
    if ($quit.WaitForExit(30000)) {
        Expect ($quit.ExitCode -eq 0) "Solaris.exe --quit exited with $($quit.ExitCode) with no UI running"
    } else {
        $quit.Kill()
        Expect $false 'Solaris.exe --quit took more than 30 seconds with no UI running'
    }
    Expect (-not (Test-Path -LiteralPath $UserData)) "Solaris.exe --quit made $UserData"
    Start-UI
    Expect (Test-Path -LiteralPath $UserData) "The UI made no $UserData"

    Write-Output '3. Upgrade over the running UI'
    $before = (Get-SolarisService).ProcessId
    Invoke-Setup $Installer '/S'
    Assert-QuitByItself 'the upgrade'
    Assert-Installed
    Expect ((Get-SolarisService).ProcessId -ne $before) "$ServiceName was not restarted"

    Write-Output '4. Uninstall over the running UI, keeping the user data'
    Start-UI
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
    Assert-QuitByItself 'the uninstall'
    Assert-Removed
    foreach ($marker in $markers) {
        Expect (Test-Path -LiteralPath $marker) "Uninstalling removed $marker"
    }

    Write-Output '5. Install again, then uninstall with /PURGE'
    Invoke-Setup $Installer '/S'
    Assert-Installed
    Invoke-Setup $Uninstaller "/S /PURGE _?=$Root"
    Assert-Removed
    foreach ($purged in $ServiceData, $UserData) {
        Expect (-not (Test-Path -LiteralPath $purged)) "/PURGE left $purged"
    }
} finally {
    if ($null -ne $ui -and -not $ui.HasExited) { $ui.Kill() }
    Unregister-ScheduledTask -TaskName $OtherTask -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Task -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
}
if ($Failures.Count -gt 0) {
    throw "$($Failures.Count) checks failed:`n$($Failures -join "`n")"
}
Write-Output 'Installed, upgraded and uninstalled cleanly.'
