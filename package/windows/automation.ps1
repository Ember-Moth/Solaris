# Initialize the .NET Framework Win32 proxies from a real managed method. Its
# ProxyManager.LoadDefaultProxies walks ReflectedType.Assembly on the caller's
# stack; PowerShell dynamic methods have no ReflectedType. A non-inlined C# entry
# provides a concrete calling assembly before that stack walk reaches PowerShell.
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName UIAutomationClientsideProviders
if (-not ('SolarisAutomation' -as [type])) {
    $automationReferences = @(
        [System.Windows.Automation.AutomationElement].Assembly.Location,
        [System.Windows.Automation.ControlType].Assembly.Location,
        [UIAutomationClientsideProviders.UIAutomationClientSideProviders].Assembly.Location
    )
    Add-Type -TypeDefinition @'
using System.Runtime.CompilerServices;
using System.Windows.Automation;
public static class SolarisAutomation {
    [MethodImpl(MethodImplOptions.NoInlining)]
    public static void Initialize() {
        ClientSettings.RegisterClientSideProviders(
            UIAutomationClientsideProviders.UIAutomationClientSideProviders.ClientSideProviderDescriptionTable);
    }
}
'@ -ReferencedAssemblies $automationReferences
}
try { [SolarisAutomation]::Initialize() }
catch { throw $_.Exception.ToString() }
