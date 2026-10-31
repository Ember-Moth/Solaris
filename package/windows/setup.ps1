# The installer's and uninstaller's dealings with running Solaris (package/windows/installer.nsi,
# docs/architecture.md section 9). They run it with Windows PowerShell 5.1, elevated, as
#
#     setup.ps1 -Operation <operation> -InstallDirectory <the install directory>
#
# close-ui         ask Solaris.exe to quit, then end every one still running from the install
#                  directory, in any session; the tunnel runs in the service either way
# stop-service     stop SolarisService if it is there, and wait for its process to exit
# install-service  create SolarisService, or point it at this directory again; then start it
# remove-service   stop and delete SolarisService
# remove-tasks     delete every user's "Open at sign-in" task that starts this Solaris.exe
#
# On failure it prints the reason, which the installer shows, and exits with 1. ASCII only:
# Windows PowerShell reads a script without a byte order mark in the system's code page.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('close-ui', 'stop-service', 'install-service', 'remove-service', 'remove-tasks')]
    [string] $Operation,
    [Parameter(Mandatory = $true)][string] $InstallDirectory
)
$ErrorActionPreference = 'Stop'

$ServiceName = 'SolarisService'  # src/core/internal/product
$Client = Join-Path $InstallDirectory 'Solaris.exe'
$ServiceExecutable = Join-Path $InstallDirectory 'solaris-service.exe'
$Timeout = [TimeSpan]::FromSeconds(60)
# Solaris.exe --quit waits up to 10 seconds for the UI to go (src/solaris/desktop/instance.py);
# the UIs it cannot reach get a few seconds more before they are ended.
$QuitTimeout = 15000
$QuitGrace = 5000

# sc.exe with its command line as written: PowerShell 5.1 drops the inner quotes of native
# arguments, and the service's path must keep its own.
function Invoke-Sc([string] $Arguments) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $env:SystemRoot 'System32\sc.exe'
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $output = $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "sc.exe $Arguments failed with $($process.ExitCode): $($output.Trim())"
    }
}

# Through WMI, which sees the path of every user's processes and the service's.
function Get-ProcessFrom([string] $Path) {
    $name = Split-Path $Path -Leaf
    @(Get-CimInstance Win32_Process -Filter "Name='$name'" |
            Where-Object { $_.ExecutablePath -eq $Path } |
            ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
}

function Wait-Exit([System.Diagnostics.Process[]] $Processes, [string] $What) {
    foreach ($process in $Processes) {
        if (-not $process.WaitForExit(15000)) { throw "$What ($($process.Id)) did not exit" }
    }
}

# Solaris.exe --quit reaches the UI of the user running the installer, which then quits as
# from its tray menu; a UI of another user, or one that does not answer, is ended after the
# grace period.
function Close-UI {
    $processes = Get-ProcessFrom $Client
    if ($processes.Count -eq 0) { return }
    Write-Output 'Asking Solaris.exe to quit'
    $quit = Start-Process -FilePath $Client -ArgumentList '--quit' -PassThru
    if (-not $quit.WaitForExit($QuitTimeout)) {
        Stop-Process -Id $quit.Id -Force
        Wait-Exit @($quit) 'Solaris.exe --quit'
    }
    $deadline = [DateTime]::UtcNow.AddMilliseconds($QuitGrace)
    foreach ($process in $processes) {
        $left = [Math]::Max(0, [int](($deadline - [DateTime]::UtcNow).TotalMilliseconds))
        if ($process.WaitForExit($left)) {
            Write-Output "Solaris.exe ($($process.Id)) quit"
        } else {
            Write-Output "Ending Solaris.exe ($($process.Id))"
            Stop-Process -Id $process.Id -Force
        }
    }
    Wait-Exit $processes 'Solaris.exe'
}

function Stop-SolarisService {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) { return }
    if ($service.Status -eq 'StartPending') { $service.WaitForStatus('Running', $Timeout) }
    if ($service.Status -ne 'Stopped') {
        Write-Output "Stopping $ServiceName"
        if ($service.Status -ne 'StopPending') { $service.Stop() }
        $service.WaitForStatus('Stopped', $Timeout)
    }
    # Stopped is reported as the service returns; its process may still hold the executable.
    Wait-Exit (Get-ProcessFrom $ServiceExecutable) 'solaris-service.exe'
}

function Install-SolarisService {
    Set-CrashDumps
    $binary = "`"\`"$ServiceExecutable\`"`""
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Invoke-Sc "config $ServiceName binPath= $binary type= own start= auto obj= LocalSystem DisplayName= Solaris"
    } else {
        Invoke-Sc "create $ServiceName binPath= $binary type= own start= auto obj= LocalSystem DisplayName= Solaris"
    }
    Invoke-Sc "description $ServiceName `"Keeps Solaris connected while its window is closed. Stopping it disconnects Solaris.`""
    # Restart after 5, 15 and 60 seconds (docs/architecture.md section 5.2), also when the
    # service stops with an error rather than crashing; a day without failures resets that.
    Invoke-Sc "failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/restart/60000"
    Invoke-Sc "failureflag $ServiceName 1"
    Write-Output "Starting $ServiceName"
    $service = Get-Service -Name $ServiceName
    if ($service.Status -ne 'Running') { $service.Start() }
    $service.WaitForStatus('Running', $Timeout)
}

function Remove-SolarisService {
    Stop-SolarisService
    Remove-CrashDumps
    if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) { return }
    Invoke-Sc "delete $ServiceName"
    # Deleted once nothing has it open, such as the Services console.
    $deadline = [DateTime]::UtcNow.Add($Timeout)
    while (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        if ([DateTime]::UtcNow -gt $deadline) {
            Write-Output "$ServiceName is marked for deletion; it goes once the programs that have it open close"
            return
        }
        Start-Sleep -Milliseconds 200
    }
}

# Per-image WER settings only. Mini dumps, never full process memory; the UI's folder
# expands in its own account. The service creates its SYSTEM-only folder before running.
# Preserve a manually configured key which this installer does not own.
$DumpRoot = 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps'
function Set-CrashDumps {
    $folders = @{
        'Solaris.exe' = '%LOCALAPPDATA%\Solaris\crashes\native'
        'solaris-service.exe' = '%ProgramData%\Solaris\crashes\native'
    }
    foreach ($image in $folders.Keys) {
        $key = Join-Path $DumpRoot $image
        if (Test-Path -LiteralPath $key) {
            $existing = Get-ItemProperty -LiteralPath $key
            if ($existing.SolarisInstallDirectory -ne $InstallDirectory) { continue }
        }
        $null = New-Item -Path $key -Force
        $null = New-ItemProperty -LiteralPath $key -Name 'SolarisInstallDirectory' -Value $InstallDirectory -PropertyType String -Force
        $null = New-ItemProperty -LiteralPath $key -Name 'DumpFolder' -Value $folders[$image] -PropertyType ExpandString -Force
        $null = New-ItemProperty -LiteralPath $key -Name 'DumpCount' -Value 5 -PropertyType DWord -Force
        $null = New-ItemProperty -LiteralPath $key -Name 'DumpType' -Value 1 -PropertyType DWord -Force
    }
}

function Remove-CrashDumps {
    foreach ($image in @('Solaris.exe', 'solaris-service.exe')) {
        $key = Join-Path $DumpRoot $image
        if ((Test-Path -LiteralPath $key) -and
            (Get-ItemProperty -LiteralPath $key).SolarisInstallDirectory -eq $InstallDirectory) {
            Remove-Item -LiteralPath $key -Recurse
        }
    }
}

# src/solaris/desktop/login.py names each user's task "Solaris (<domain>_<user>)", in the root
# folder, starting Solaris.exe with --background. Tasks of the same name that start another
# program are not this installation's.
function Remove-LoginTasks {
    $tasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction SilentlyContinue | Where-Object {
            $_.TaskName -like 'Solaris (*)' -and
            @($_.Actions | Where-Object { "$($_.Execute)".Trim('"') -eq $Client }).Count -gt 0
        })
    foreach ($task in $tasks) {
        Write-Output "Removing the task $($task.TaskName)"
        Unregister-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -Confirm:$false
    }
}

try {
    switch ($Operation) {
        'close-ui' { Close-UI }
        'stop-service' { Stop-SolarisService }
        'install-service' { Install-SolarisService }
        'remove-service' { Remove-SolarisService }
        'remove-tasks' { Remove-LoginTasks }
    }
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
exit 0
