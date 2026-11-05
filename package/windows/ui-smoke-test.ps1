# Read-only UI checks on the disposable Windows runner. The harness renders the same
# native surface as setup/uninstall; it cannot install, uninstall or start Solaris.
# It walks every state the preview reports (ui/preview.nsi) and saves a screenshot of each:
# ready, license, every step, details, completion and failure, for install, update and
# uninstall, in both languages, light and dark, and at 150%.
# This file stays ASCII so Windows PowerShell 5.1 can load it in any system locale.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Preview,
    [Parameter(Mandatory = $true)][string] $Output,
    [ValidateSet('x64', 'arm64')][string] $Architecture = 'x64'
)
$ErrorActionPreference = 'Stop'
$Preview = (Resolve-Path -LiteralPath $Preview).Path
$null = New-Item -ItemType Directory -Path $Output -Force
$Output = (Resolve-Path -LiteralPath $Output).Path
# The reader shows LICENSE without the GPL's centring and paragraph indents.
$licenseSource = ([IO.File]::ReadAllText((Join-Path $PSScriptRoot '../../LICENSE'), [Text.Encoding]::UTF8).Replace("`r`n", "`n").Split("`n") |
    ForEach-Object { $_.TrimStart([char[]]" `t") }) -join "`n"
$licenseSource = $licenseSource.TrimEnd([char[]]"`r`n")
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
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wparam, StringBuilder lparam);
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
    // WM_GETTEXT is marshalled across processes, unlike GetWindowText for a child control.
    public static string Text(IntPtr control) {
        var value = new StringBuilder(1024);
        SendMessage(control, 0x000D, new IntPtr(value.Capacity), value);
        return value.ToString();
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
    public static void Key(IntPtr window, int key) {
        PostMessage(window, 0x0100, new IntPtr(key), IntPtr.Zero);
    }
}
'@ -ReferencedAssemblies System.Drawing

# Control IDs (setup_ui.cpp).
$Action = 1001; $Cancel = 1002; $Details = 1003; $Purge = 1004; $Agreement = 1005; $Path = 1006
$Link = 1008; $LicenseText = 1009; $Subtitle = 1010; $FirstStep = 1011; $Log = 1015
$Tab = 0x09; $Enter = 0x0D; $Escape = 0x1B

# A step's accessible name ends with its state, in English or Chinese.
$StepStates = [ordered]@{
    current = @('(in progress)', ([string][char]0x8FDB + [char]0x884C + [char]0x4E2D))
    done = @('(done)', ([string][char]0x5DF2 + [char]0x5B8C + [char]0x6210))
    failed = @('(failed)', ([string][char]0x5931 + [char]0x8D25))
    waiting = @('(waiting)', ([string][char]0x7B49 + [char]0x5F85))
}

function Wait-Until([scriptblock] $Condition, [string] $Message, [int] $Seconds = 20) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (& $Condition) { return }
        Start-Sleep -Milliseconds 50
    }
    throw $Message
}

function Handle([IntPtr] $Window, [int] $Id) {
    $handle = [SetupUI]::GetDlgItem($Window, $Id)
    if ($handle -eq [IntPtr]::Zero) { throw "Missing control $Id" }
    $handle
}

function Element([IntPtr] $Window, [int] $Id) {
    [System.Windows.Automation.AutomationElement]::FromHandle((Handle $Window $Id))
}

function Visible([IntPtr] $Window, [int] $Id) { [SetupUI]::IsWindowVisible((Handle $Window $Id)) }
function Enabled([IntPtr] $Window, [int] $Id) { [SetupUI]::IsWindowEnabled((Handle $Window $Id)) }

function Invoke-Button([IntPtr] $Window, [int] $Id) {
    $element = Element $Window $Id
    $current = $element.Current
    if ($current.ControlType.Id -ne [System.Windows.Automation.ControlType]::Button.Id) {
        throw "Control $Id is not accessible as a button: $($current.ControlType.ProgrammaticName)"
    }
    if (-not $current.Name) { throw "Control $Id has no accessible name" }
    $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    try { $pattern.Invoke() } catch {
        if ([SetupUI]::IsWindow($Window)) { throw }
    }
}

function Toggle([IntPtr] $Window, [int] $Id) {
    (Element $Window $Id).GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
}

# The license link is a SysLink: its link item is invoked like a button.
function Invoke-Link([IntPtr] $Window) {
    $element = Element $Window $Link
    $candidates = @($element) + @($element.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition))
    foreach ($candidate in $candidates) {
        $pattern = $null
        if ($candidate.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref] $pattern)) {
            if ($candidate.Current.Name -notmatch 'GPL') { throw "The license link is named '$($candidate.Current.Name)'" }
            $pattern.Invoke()
            Wait-Until { Visible $Window $LicenseText } 'The license link did not open the license'
            return
        }
    }
    # Keep capturing the remaining states; the annotation reports the missing pattern.
    Write-Output "::warning::The license link exposes no invoke pattern: $(($candidates | ForEach-Object { $_.Current.ControlType.ProgrammaticName }) -join ', ')"
    $element.SetFocus()
    [SetupUI]::Key($Window, $Enter)
    Wait-Until { Visible $Window $LicenseText } 'The license link did not open the license'
}

function Step-State([IntPtr] $Window, [int] $Index) {
    $name = [SetupUI]::Text((Handle $Window ($FirstStep + $Index)))
    foreach ($state in $StepStates.Keys) {
        foreach ($marker in $StepStates[$state]) { if ($name.Contains($marker)) { return $state } }
    }
    throw "Step $Index has no state: '$name'"
}

function Wait-Step($Case, [int] $Index, [string] $State = 'current') {
    Wait-Until { (Step-State $Case.Window $Index) -eq $State } "Step $Index did not become $State" 30
}

function Start-Preview([string] $Mode, [string] $Language, [string] $Theme, [string] $Outcome = 'success', [int] $Dpi = 96) {
    $process = Start-Process -FilePath $Preview -ArgumentList "/MODE=$Mode /LANG=$Language /THEME=$Theme /OUTCOME=$Outcome" -PassThru
    $null = $process.Handle
    Wait-Until { [SetupUI]::Find($process.Id) -ne [IntPtr]::Zero } 'No installer window appeared'
    $window = [SetupUI]::Find($process.Id)
    Wait-Until {
        if ([SetupUI]::GetDlgItem($window, $Action) -eq [IntPtr]::Zero) { return $false }
        try { return (Element $window $Action).Current.Name.Length -gt 0 } catch { return $false }
    } 'Installer text was not initialized'
    Start-Sleep -Milliseconds 250
    if ($Language -eq 'zh' -and (Element $window $Action).Current.Name -notmatch '[\u4e00-\u9fff]') {
        throw 'Chinese UI did not load'
    }
    if ((Element $window $Subtitle).Current.Name -notmatch "Windows $Architecture$") { throw 'Wrong application architecture in the installer header' }
    if ($Dpi -ne 96) { [SetupUI]::LayoutDpi($window, $Dpi) }
    $scale = if ($Dpi -ne 96) { "-$([int]($Dpi * 100 / 96))" } else { '' }
    return @{ Process = $process; Window = $window; Name = "$Mode-$Language-$Theme$scale" }
}

function Assert-Exit($Case, [int] $Expected) {
    if (-not $Case.Process.WaitForExit(10000)) { throw 'Preview process did not exit' }
    if ($Case.Process.ExitCode -ne $Expected) { throw "Exit $($Case.Process.ExitCode), expected $Expected" }
}

function Snapshot($Case, [string] $State) {
    Start-Sleep -Milliseconds 150
    [SetupUI]::Capture($Case.Window, (Join-Path $Output "$($Case.Name)-$State.png"))
}

function Consent($Case) {
    if (Enabled $Case.Window $Action) { throw 'The action was enabled before license consent' }
    $agreement = Toggle $Case.Window $Agreement
    if ($agreement.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::Off) { throw 'License consent was selected by default' }
    $agreement.Toggle()
    if (-not (Enabled $Case.Window $Action)) { throw 'License consent did not enable the action' }
}

# Starts the operation and captures each step as the preview reports it. The preview fails
# at the second step with -Outcome failure; the steps after it stay waiting.
function Run-Steps($Case, [int] $Count, [string] $Outcome, [switch] $DetailsWhileWorking) {
    Invoke-Button $Case.Window $Action
    Wait-Until { -not (Visible $Case.Window $Cancel) } 'Progress was not shown'
    $null = [SetupUI]::SendMessage($Case.Window, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
    if (-not [SetupUI]::IsWindow($Case.Window)) { throw 'The busy window closed mid-operation' }
    $last = if ($Outcome -eq 'failure') { 1 } else { $Count - 1 }
    for ($index = 0; $index -le $last; $index++) {
        Wait-Step $Case $index
        for ($before = 0; $before -lt $index; $before++) {
            if ((Step-State $Case.Window $before) -ne 'done') { throw "Step $before is not done while $index runs" }
        }
        Snapshot $Case "step$index"
        if ($DetailsWhileWorking -and $index -eq 1) {
            Invoke-Button $Case.Window $Details
            Wait-Until { Visible $Case.Window $Log } 'The details did not open'
            Snapshot $Case 'working-details'
            Invoke-Button $Case.Window $Details
            Wait-Until { -not (Visible $Case.Window $Log) } 'The details did not close'
        }
    }
    Wait-Until { Visible $Case.Window $Cancel } 'No completion state' 30
    if ($Outcome -eq 'failure') {
        if ((Step-State $Case.Window 1) -ne 'failed') { throw 'The failing step is not marked failed' }
        for ($index = 2; $index -lt $Count; $index++) {
            if ((Step-State $Case.Window $index) -ne 'waiting') { throw "Step $index ran after the failure" }
        }
        if (Visible $Case.Window $Action) { throw 'A failure offers more than Close' }
    }
    Snapshot $Case $Outcome
    Invoke-Button $Case.Window $Details
    Wait-Until { Visible $Case.Window $Log } 'The details did not open'
    Snapshot $Case "$Outcome-details"
}

# Cancel remains the default for uninstall. Enter must not remove anything.
$case = Start-Preview 'uninstall' 'en' 'light'
try {
    Wait-Until { [SetupUI]::FocusedControl($case.Window) -eq $Cancel } 'Uninstall did not default to Cancel'
    Snapshot $case 'ready'
    [SetupUI]::Key($case.Window, $Enter)
    Assert-Exit $case 1223
} finally { if (-not $case.Process.HasExited) { $case.Process.Kill() } }

# Install, keyboard first: Tab reaches the license link, Enter opens it, Esc returns.
$case = Start-Preview 'install' 'zh' 'light'
try {
    Snapshot $case 'ready'
    Wait-Until { [SetupUI]::FocusedControl($case.Window) -eq $Path } 'Install did not start in the location field'
    $pathElement = Element $case.Window $Path
    if (-not $pathElement.Current.Name) { throw 'The install path has no accessible label' }
    $pathElement.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue('C:\Solaris Preview')
    1..3 | ForEach-Object { [SetupUI]::Key($case.Window, $Tab) }
    Wait-Until { [SetupUI]::FocusedControl($case.Window) -eq $Link } 'Tab did not reach the license link'
    Snapshot $case 'ready-focus'
    [SetupUI]::Key($case.Window, $Enter)
    Wait-Until { Visible $case.Window $LicenseText } 'Enter did not open the license'
    $licenseElement = Element $case.Window $LicenseText
    if ($licenseElement.Current.Name -notmatch 'GPL') { throw 'The license reader has no accessible label' }
    # Multiline native readers expose TextPattern, not the single-line ValuePattern.
    $license = $licenseElement.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
    $readOnly = $license.DocumentRange.GetAttributeValue([System.Windows.Automation.TextPattern]::IsReadOnlyAttribute)
    $licenseText = $license.DocumentRange.GetText(-1).Replace("`r`n", "`n").TrimEnd([char[]]"`r`n")
    if ($readOnly -ne $true -or $licenseText -cne $licenseSource) { throw 'The complete read-only GPLv3 text differs from LICENSE' }
    Snapshot $case 'license'
    [SetupUI]::Key($case.Window, $Escape)
    Wait-Until { -not (Visible $case.Window $LicenseText) } 'Esc did not leave the license'
    if (Enabled $case.Window $Action) { throw 'Reading the license implicitly accepted it' }
    Consent $case
    (Toggle $case.Window $Agreement).Toggle()
    if (Enabled $case.Window $Action) { throw 'Revoking consent did not disable Install' }
    Invoke-Link $case.Window
    Invoke-Button $case.Window $Action
    if ((Toggle $case.Window $Agreement).Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) { throw 'Agree did not accept the license' }
    Run-Steps $case 4 'success' -DetailsWhileWorking
    Invoke-Button $case.Window $Action
    Assert-Exit $case 10
} finally { if (-not $case.Process.HasExited) { $case.Process.Kill() } }

foreach ($sample in @(
    @{ Mode='upgrade'; Lang='en'; Theme='dark'; Outcome='success'; Steps=4; Finish=$Action; Code=10 },
    @{ Mode='install'; Lang='en'; Theme='dark'; Outcome='failure'; Steps=4; Finish=$Cancel; Code=0 },
    @{ Mode='uninstall'; Lang='zh'; Theme='dark'; Outcome='success'; Steps=4; Purge=$true; Finish=$Cancel; Code=20 },
    @{ Mode='uninstall'; Lang='en'; Theme='light'; Outcome='failure'; Steps=3; Finish=$Cancel; Code=0 },
    @{ Mode='install'; Lang='en'; Theme='light'; Outcome='success'; Steps=4; Dpi=144; License=$true; Finish=$Cancel; Code=0 },
    @{ Mode='upgrade'; Lang='zh'; Theme='dark'; Outcome='failure'; Steps=4; Dpi=144; License=$true; Finish=$Cancel; Code=0 }
)) {
    $dpi = if ($sample.Dpi) { $sample.Dpi } else { 96 }
    $case = Start-Preview $sample.Mode $sample.Lang $sample.Theme $sample.Outcome $dpi
    try {
        Snapshot $case 'ready'
        if ($sample.Mode -eq 'uninstall') {
            Wait-Until { [SetupUI]::FocusedControl($case.Window) -eq $Cancel } 'Uninstall did not default to Cancel'
            $purge = Toggle $case.Window $Purge
            if ($purge.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::Off) { throw 'Data deletion was selected by default' }
            if ($sample.Purge) {
                $purge.Toggle()
                if ($purge.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) { throw 'Data deletion toggle failed' }
                Snapshot $case 'ready-purge'
            }
        } else {
            if ($sample.License) {
                Invoke-Link $case.Window
                Snapshot $case 'license'
                Invoke-Button $case.Window $Cancel
                Wait-Until { -not (Visible $case.Window $LicenseText) } 'Back did not leave the license'
            }
            Consent $case
        }
        Run-Steps $case $sample.Steps $sample.Outcome
        if ($sample.Mode -eq 'uninstall' -and (Visible $case.Window $Action)) { throw 'Uninstall offers to open Solaris' }
        Invoke-Button $case.Window $sample.Finish
        Assert-Exit $case $sample.Code
    } finally { if (-not $case.Process.HasExited) { $case.Process.Kill() } }
}
Write-Output 'Installer UI: license consent and link, full GPL text, custom path, keyboard, locale, theme, 150% layout, every step, details, completion, failure and purge choice passed.'
