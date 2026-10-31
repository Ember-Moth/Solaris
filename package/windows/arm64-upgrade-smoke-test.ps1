# Run on a disposable ARM64 Windows runner. No TUN or connection is started.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $X64Installer,
    [Parameter(Mandatory = $true)][string] $ARM64Installer
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Run only on a disposable GitHub Actions runner.' }
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
public static class MigrationArchitecture {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool IsWow64Process2(IntPtr process, out ushort processMachine, out ushort nativeMachine);
    public static bool IsArm64() {
        ushort process, native;
        return IsWow64Process2(Process.GetCurrentProcess().Handle, out process, out native) && native == 0xAA64;
    }
}
'@
if (-not [MigrationArchitecture]::IsArm64()) { throw 'The migration test needs native ARM64 Windows.' }
$X64Installer = (Resolve-Path -LiteralPath $X64Installer).Path
$ARM64Installer = (Resolve-Path -LiteralPath $ARM64Installer).Path
$root = Join-Path $env:SystemDrive 'Solaris CI Migration'
$client = Join-Path $root 'Solaris.exe'
$serviceBinary = Join-Path $root 'solaris-service.exe'
$uninstaller = Join-Path $root 'uninstall.exe'
$key = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Solaris'
$userData = Join-Path $env:LOCALAPPDATA 'Solaris'
$serviceData = Join-Path $env:ProgramData 'Solaris'
if ((Test-Path $root) -or (Test-Path $key) -or (Get-Service SolarisService -ErrorAction SilentlyContinue) -or
    (Test-Path $userData) -or (Test-Path $serviceData)) { throw 'The migration test needs a fresh runner.' }

function Run-Installer([string] $Executable, [string] $Arguments) {
    Write-Output "> $([IO.Path]::GetFileName($Executable)) $Arguments"
    $setupProcess = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru
    $null = $setupProcess.Handle
    if (-not $setupProcess.WaitForExit(300000)) { $setupProcess.Kill(); throw 'Installer timed out' }
    if ($setupProcess.ExitCode -ne 0) {
        $errorLog = Join-Path $env:TEMP 'Solaris-setup-error.log'
        if (Test-Path $errorLog) { Get-Content -LiteralPath $errorLog | Write-Output }
        throw "Installer returned $($setupProcess.ExitCode)"
    }
    Write-Output 'Installer exited successfully.'
}

function Read-Machine([string] $File) {
    $reader = [IO.BinaryReader]::new([IO.File]::OpenRead($File))
    try {
        $reader.BaseStream.Position = 0x3c
        $offset = $reader.ReadInt32()
        $reader.BaseStream.Position = $offset
        if ($reader.ReadUInt32() -ne 0x4550) { throw "Invalid PE: $File" }
        return $reader.ReadUInt16()
    } finally { $reader.Dispose() }
}

function Assert-Architecture([string] $Architecture, [int] $Machine) {
    $entry = Get-ItemProperty -LiteralPath $key
    if ($entry.InstallLocation -ne $root -or $entry.InstallArchitecture -ne $Architecture) { throw 'The installation registration changed incorrectly' }
    foreach ($file in @($client, $serviceBinary, (Join-Path $root 'libcronet.dll'))) {
        $actual = Read-Machine $file
        Write-Output ('{0}: machine 0x{1:X4}, expected 0x{2:X4}' -f $file, $actual, $Machine)
        if ($actual -ne $Machine) { throw "Unexpected architecture: $file" }
    }
    $service = Get-CimInstance Win32_Service -Filter "Name='SolarisService'"
    if (-not $service -or $service.State -ne 'Running' -or $service.StartName -ne 'LocalSystem' -or
        $service.PathName -ne "`"$serviceBinary`"") { throw 'The expected system service is not running' }
    $log = Get-Content -LiteralPath (Join-Path $serviceData 'service.log') -Raw
    if ($log -match 'Naive native runtime unavailable') { throw 'The service could not load its matching Cronet DLL' }
}

function Open-Client {
    $opened = Start-Process -FilePath $client -PassThru
    $null = $opened.Handle
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $deadline) {
        $opened.Refresh()
        if ($opened.HasExited) { throw "The GUI exited with $($opened.ExitCode)" }
        if ($opened.MainWindowHandle -ne [IntPtr]::Zero) { return $opened }
        Start-Sleep -Milliseconds 200
    }
    $opened.Kill()
    throw 'The GUI window did not open'
}

$oldUI = $null
$newUI = $null
try {
    Write-Output 'Installing and opening the x64 application.'
    Run-Installer $X64Installer "/S /D=$root"
    Assert-Architecture 'x64' 0x8664
    $oldUI = Open-Client
    $markers = foreach ($folder in @($userData, $serviceData)) {
        $marker = Join-Path $folder 'architecture-migration-marker'
        [IO.File]::WriteAllText($marker, 'keep across architecture upgrade')
        $marker
    }
    Write-Output 'Migrating the running x64 installation to ARM64.'
    Run-Installer $ARM64Installer '/S'
    if (-not $oldUI.WaitForExit(30000) -or $oldUI.ExitCode -ne 0) { throw 'The x64 GUI did not quit cleanly during migration' }
    Assert-Architecture 'arm64' 0xAA64
    foreach ($marker in $markers) {
        if ([IO.File]::ReadAllText($marker) -ne 'keep across architecture upgrade') { throw 'User or service data was lost' }
    }
    foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File) {
        if ($file.FullName -ne $uninstaller -and $file.Extension -in @('.exe', '.dll', '.pyd') -and
            (Read-Machine $file.FullName) -ne 0xAA64) { throw "A non-ARM64 runtime file remains: $($file.FullName)" }
    }
    $newUI = Open-Client
    $quit = Start-Process -FilePath $client -ArgumentList '--quit' -PassThru
    if (-not $quit.WaitForExit(30000) -or -not $newUI.WaitForExit(30000) -or $newUI.ExitCode -ne 0) { throw 'The ARM64 GUI did not quit cleanly' }
    Run-Installer $uninstaller "/S /PURGE _?=$root"
    # With _?= the uninstaller runs in place and cannot delete its own executable.
    $remaining = @(Get-ChildItem -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue)
    if ($remaining.Count -ne 1 -or $remaining[0].FullName -ne $uninstaller) { throw 'Migration cleanup left runtime files behind' }
    Remove-Item -LiteralPath $uninstaller
    Remove-Item -LiteralPath $root
    if ((Test-Path $key) -or (Test-Path $userData) -or (Test-Path $serviceData) -or
        (Get-Service SolarisService -ErrorAction SilentlyContinue)) { throw 'Migration cleanup left application state behind' }
    Write-Output 'x64 to ARM64 migration passed: native payload, running service/GUI, preserved data and clean uninstall.'
} catch {
    $nativeLibrary = Join-Path $root 'libcronet.dll'
    if (Test-Path -LiteralPath $nativeLibrary) {
        Get-Item -LiteralPath $nativeLibrary | Select-Object FullName, Attributes, Length | Format-List | Out-String | Write-Output
        try {
            $probe = [IO.File]::Open($nativeLibrary, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            $probe.Dispose()
            Write-Output 'Cronet DLL is writable and has no incompatible open handle.'
        } catch {
            Write-Output "Cronet DLL write probe: $($_.Exception.ToString())"
        }
    }
    $serviceLog = Join-Path $serviceData 'service.log'
    if (Test-Path -LiteralPath $serviceLog) { Get-Content -LiteralPath $serviceLog -Tail 60 | Write-Output }
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "$root\*" } |
        Select-Object ProcessId, Name, ExecutablePath | Format-Table | Out-String | Write-Output
    throw
} finally {
    foreach ($opened in @($oldUI, $newUI)) { if ($opened -and -not $opened.HasExited) { $opened.Kill() } }
}
