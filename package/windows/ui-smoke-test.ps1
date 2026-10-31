# Read-only UI checks on the disposable Windows runner. The harness renders the same
# native surface as setup/uninstall; it cannot install, uninstall or start Solaris.
# This file stays ASCII so Windows PowerShell 5.1 can load it in any system locale.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Preview,
    [Parameter(Mandatory = $true)][string] $Output
)
$ErrorActionPreference = 'Stop'
$Preview = (Resolve-Path -LiteralPath $Preview).Path
$null = New-Item -ItemType Directory -Path $Output -Force
$Output = (Resolve-Path -LiteralPath $Output).Path
. "$PSScriptRoot/automation.ps1"
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public static class SetupUI {
    public delegate bool Enumerate(IntPtr window, IntPtr data);
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct GuiInfo {
        public int Size, Flags; public IntPtr Active, Focus, Capture, MenuOwner, MoveSize, Caret;
        public Rect CaretRect;
    }
    [DllImport("user32.dll")] public static extern bool EnumWindows(Enumerate callback, IntPtr data);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr window, StringBuilder value, int count);
    [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr window, int id);
    [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr window);
    [DllImport("user32.dll")] public static extern bool GetGUIThreadInfo(uint thread, ref GuiInfo info);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr window);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr window, IntPtr dc, uint flags);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
    public static IntPtr Find(int processId) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr window, IntPtr data) {
            uint process;
            GetWindowThreadProcessId(window, out process);
            var name = new StringBuilder(128);
            GetClassName(window, name, name.Capacity);
            if (process == processId && name.ToString() == "SolarisSetupWindow") { found = window; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
    public static int FocusedControl(IntPtr window) {
        uint process;
        uint thread = GetWindowThreadProcessId(window, out process);
        var info = new GuiInfo(); info.Size = Marshal.SizeOf(info);
        return GetGUIThreadInfo(thread, ref info) ? GetDlgCtrlID(info.Focus) : -1;
    }
    public static void Capture(IntPtr window, string path) {
        Rect rect; GetWindowRect(window, out rect);
        using (var image = new Bitmap(rect.Right - rect.Left, rect.Bottom - rect.Top)) {
            using (var graphics = Graphics.FromImage(image)) {
                var dc = graphics.GetHdc();
                try { if (!PrintWindow(window, dc, 2)) throw new Exception("PrintWindow failed"); }
                finally { graphics.ReleaseHdc(dc); }
            }
            image.Save(path, ImageFormat.Png);
        }
    }
    public static void LayoutDpi(IntPtr window, int dpi) {
        // A scalar-only message to the read-only harness, not a cross-process RECT pointer.
        SendMessage(window, 0x8005, new IntPtr(dpi), IntPtr.Zero);
    }
}
'@ -ReferencedAssemblies System.Drawing

function Wait-Until([scriptblock] $Condition, [string] $Message, [int] $Seconds = 20) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (& $Condition) { return }
        Start-Sleep -Milliseconds 100
    }
    throw $Message
}

function Element([IntPtr] $Window, [int] $Id) {
    $handle = [SetupUI]::GetDlgItem($Window, $Id)
    if ($handle -eq [IntPtr]::Zero) { throw "Missing control $Id" }
    [System.Windows.Automation.AutomationElement]::FromHandle($handle)
}

function Invoke-Button([IntPtr] $Window, [int] $Id) {
    $element = Element $Window $Id
    $current = $element.Current
    Write-Output "Control $Id`: $($current.ClassName), $($current.ControlType.ProgrammaticName) ($($current.ControlType.Id)), patterns: $(($element.GetSupportedPatterns() | ForEach-Object ProgrammaticName) -join ', ')"
    if ($current.ControlType.Id -ne [System.Windows.Automation.ControlType]::Button.Id) {
        throw "Control $Id is not accessible as a button: $($current.ControlType.ProgrammaticName)"
    }
    if (-not $element.Current.Name) { throw "Control $Id has no accessible name" }
    $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    try { $pattern.Invoke() } catch {
        if ([SetupUI]::IsWindow($Window)) { throw }
    }
}

function Start-Preview([string] $Mode, [string] $Language, [string] $Theme, [string] $Outcome = 'success') {
    $process = Start-Process -FilePath $Preview -ArgumentList "/MODE=$Mode /LANG=$Language /THEME=$Theme /OUTCOME=$Outcome" -PassThru
    $null = $process.Handle
    Wait-Until { [SetupUI]::Find($process.Id) -ne [IntPtr]::Zero } 'No installer window appeared'
    $window = [SetupUI]::Find($process.Id)
    Wait-Until {
        if ([SetupUI]::GetDlgItem($window, 1001) -eq [IntPtr]::Zero) { return $false }
        try { return (Element $window 1001).Current.Name.Length -gt 0 } catch { return $false }
    } 'Installer text was not initialized'
    Start-Sleep -Milliseconds 250
    if ($Language -eq 'zh' -and (Element $window 1001).Current.Name -notmatch '[\u4e00-\u9fff]') {
        throw 'Chinese UI did not load'
    }
    return @{ Process = $process; Window = $window }
}

function Assert-Exit($Case, [int] $Expected) {
    if (-not $Case.Process.WaitForExit(10000)) { throw 'Preview process did not exit' }
    if ($Case.Process.ExitCode -ne $Expected) { throw "Exit $($Case.Process.ExitCode), expected $Expected" }
}

function Snapshot([IntPtr] $Window, [string] $Name) {
    Start-Sleep -Milliseconds 150
    [SetupUI]::Capture($Window, (Join-Path $Output "$Name.png"))
}

# Cancel remains the default for uninstall. Enter must not remove anything.
$case = Start-Preview 'uninstall' 'en' 'light'
try {
    Wait-Until { [SetupUI]::FocusedControl($case.Window) -eq 1002 } 'Uninstall did not default to Cancel'
    Snapshot $case.Window 'uninstall-en-light'
    $null = [SetupUI]::PostMessage($case.Window, 0x0100, [IntPtr]13, [IntPtr]::Zero)
    Assert-Exit $case 1223
} finally { if (-not $case.Process.HasExited) { $case.Process.Kill() } }

foreach ($sample in @(
    @{ Mode='install'; Lang='zh'; Theme='light'; Outcome='success'; Code=10 },
    @{ Mode='upgrade'; Lang='en'; Theme='dark'; Outcome='success'; Code=10 },
    @{ Mode='install'; Lang='en'; Theme='light'; Outcome='failure'; Code=0 },
    @{ Mode='uninstall'; Lang='zh'; Theme='dark'; Outcome='success'; Code=20 }
)) {
    $case = Start-Preview $sample.Mode $sample.Lang $sample.Theme $sample.Outcome
    try {
        $name = "$($sample.Mode)-$($sample.Lang)-$($sample.Theme)"
        Snapshot $case.Window "$name-ready"
        if ($sample.Mode -ne 'uninstall') {
            if ([SetupUI]::IsWindowEnabled([SetupUI]::GetDlgItem($case.Window, 1001))) { throw 'Install was enabled before license consent' }
            if ($sample.Mode -eq 'install') {
                $path = (Element $case.Window 1006).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                $path.SetValue('C:\Solaris Preview')
            }
            Invoke-Button $case.Window 1008
            $license = (Element $case.Window 1009).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            if (-not $license.Current.IsReadOnly -or $license.Current.Value.Length -lt 30000 -or
                $license.Current.Value -notmatch 'END OF TERMS AND CONDITIONS') { throw 'The full GPLv3 text is not readable' }
            Snapshot $case.Window "$name-license"
            Invoke-Button $case.Window 1002
            if ([SetupUI]::IsWindowEnabled([SetupUI]::GetDlgItem($case.Window, 1001))) { throw 'Reading the license implicitly accepted it' }
            $agreement = (Element $case.Window 1005).GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
            if ($agreement.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::Off) { throw 'License consent was selected by default' }
            $agreement.Toggle()
            if (-not [SetupUI]::IsWindowEnabled([SetupUI]::GetDlgItem($case.Window, 1001))) { throw 'License consent did not enable Install' }
            $agreement.Toggle()
            if ([SetupUI]::IsWindowEnabled([SetupUI]::GetDlgItem($case.Window, 1001))) { throw 'Revoking consent did not disable Install' }
            Invoke-Button $case.Window 1008
            Invoke-Button $case.Window 1001
            if ($agreement.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) { throw 'Agree and return did not accept the license' }
        }
        if ($sample.Mode -eq 'uninstall') {
            $check = Element $case.Window 1004
            $toggle = $check.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
            if ($toggle.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::Off) { throw 'Data deletion was selected by default' }
            $toggle.Toggle()
            if ($toggle.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) { throw 'Data deletion toggle failed' }
        }
        if ($sample.Lang -eq 'zh' -and $sample.Mode -eq 'install') {
            # Exercise the DPI-change layout path without changing the runner's display settings.
            [SetupUI]::LayoutDpi($case.Window, 144)
            Snapshot $case.Window "$name-layout-150percent"
        }
        Invoke-Button $case.Window 1001
        Wait-Until { -not [SetupUI]::IsWindowEnabled([SetupUI]::GetDlgItem($case.Window, 1002)) } 'Progress was not shown'
        $null = [SetupUI]::SendMessage($case.Window, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
        if (-not [SetupUI]::IsWindow($case.Window)) { throw 'The busy window closed mid-operation' }
        Snapshot $case.Window "$name-progress"
        Wait-Until { [SetupUI]::IsWindowVisible([SetupUI]::GetDlgItem($case.Window, 1001)) } 'No completion state'
        if ($sample.Outcome -eq 'failure') { Invoke-Button $case.Window 1003 }
        Snapshot $case.Window "$name-$($sample.Outcome)"
        Invoke-Button $case.Window 1001
        Assert-Exit $case $sample.Code
    } finally { if (-not $case.Process.HasExited) { $case.Process.Kill() } }
}
Write-Output 'Installer UI: license consent, full GPL text, custom path, locale, theme, keyboard default, progress, completion, failure and purge choice passed.'
