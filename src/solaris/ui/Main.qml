pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "components"
import "pages"

ApplicationWindow {
    id: window

    // Injected from Python via QQmlApplicationEngine.setInitialProperties.
    required property ShellViewModel shellViewModel
    required property OverviewViewModel overviewViewModel
    required property ProxiesViewModel proxiesViewModel
    required property ProfilesViewModel profilesViewModel
    required property RoutingViewModel routingViewModel
    required property Appearance appearance
    required property ConnectionsViewModel connectionsViewModel
    required property LogsViewModel logsViewModel
    required property SettingsViewModel settingsViewModel
    required property DesktopViewModel desktopViewModel
    required property AboutViewModel aboutViewModel
    required property BackupViewModel backupViewModel
    required property SystemExtensionViewModel systemExtensionViewModel

    // Bring the window back from the tray, the Dock or a second launch.
    function present() {
        if (visibility === Window.Minimized)
            showNormal()
        else
            show()
        raise()
        requestActivate()
    }

    width: Theme.windowDefaultWidth
    height: Theme.windowDefaultHeight
    minimumWidth: Theme.windowMinWidth
    minimumHeight: Theme.windowMinHeight
    visible: !desktopViewModel.startHidden
    title: Qt.application.name
    // No system title bar: the sidebar and the pages run up to the top edge, under the title
    // band (DESIGN.md "顶部区域"), which keeps clear of the traffic lights itself.
    flags: desktopViewModel.titleBar.windowFlags
    topPadding: 0
    color: Theme.background
    font.family: Fonts.resolve(Theme.type.bodyMd.fontFamily)
    font.pixelSize: Theme.type.bodyMd.fontSize

    // The Basic style's own drawing (scroll bars, text selection, Qt's context menus) takes
    // the theme's tokens too; popups inherit the window's palette (ui.md §6).
    palette.window: Theme.background
    palette.windowText: Theme.text
    palette.base: Theme.surface
    palette.alternateBase: Theme.surfaceSubtle
    palette.text: Theme.text
    palette.placeholderText: Theme.textMuted
    palette.button: Theme.surface
    palette.buttonText: Theme.text
    palette.brightText: Theme.textOnPrimary
    palette.highlight: Theme.primary
    palette.highlightedText: Theme.textOnPrimary
    palette.accent: Theme.primary
    palette.link: Theme.link
    palette.linkVisited: Theme.link
    palette.light: Theme.surfaceSelected
    palette.midlight: Theme.border
    palette.mid: Theme.borderControl
    palette.dark: Theme.textMuted
    palette.shadow: Theme.scrim
    palette.toolTipBase: Theme.surface
    palette.toolTipText: Theme.text

    Binding {
        target: Theme
        property: "appearance"
        value: window.appearance
    }

    Binding {
        target: TitleBand
        property: "titleBar"
        value: window.desktopViewModel.titleBar
    }

    Component.onCompleted: {
        desktopViewModel.titleBar.attach(window)
        // Qt keeps the frame a window with a title bar would have, so the window grew by the
        // title bar it does not show; the design's size is the whole window.
        width = Theme.windowDefaultWidth
        height = Theme.windowDefaultHeight
    }

    // Closing the window never stops the tunnel: it either stays in the tray or the UI quits.
    onClosing: close => {
        if (desktopViewModel.hideOnClose) {
            close.accepted = false
            hide()
        } else {
            desktopViewModel.quit()
        }
    }

    Connections {
        target: window.desktopViewModel

        function onShowWindowRequested() {
            window.present()
        }

        function onReopened() {
            if (!window.visible)
                window.present()
        }
    }

    Loader {
        active: window.desktopViewModel.trayAvailable

        sourceComponent: TrayIcon {
            desktop: window.desktopViewModel
            overview: window.overviewViewModel
            onShowWindow: window.present()
        }
    }

    ExtensionSetupDialog {
        id: extensionSetupDialog

        overview: window.overviewViewModel
    }

    Connections {
        target: window.overviewViewModel

        function onExtensionSetupRequested() {
            const from = window.visible && Focus.within(window.activeFocusItem, overviewPage)
                ? window.activeFocusItem : overviewPage.connectionControl
            window.shellViewModel.setPage("overview")
            window.present()
            if (!extensionSetupDialog.visible)
                extensionSetupDialog.show(from)
        }

        function onChanged() {
            if (window.overviewViewModel.extensionSetup === "" && extensionSetupDialog.visible)
                extensionSetupDialog.close()
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Sidebar {
            Layout.fillHeight: true
            Layout.preferredWidth: Theme.sidebarWidth
            shell: window.shellViewModel
            routing: window.routingViewModel
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: window.shellViewModel.pageIndex

            OverviewPage {
                id: overviewPage

                overview: window.overviewViewModel
                profiles: window.profilesViewModel
                routing: window.routingViewModel
                shell: window.shellViewModel
            }

            ProxiesPage {
                proxies: window.proxiesViewModel
            }

            ProfilesPage {
                profiles: window.profilesViewModel
            }

            RoutingPage {
                routing: window.routingViewModel
                shell: window.shellViewModel
            }

            ConnectionsPage {
                connections: window.connectionsViewModel
            }

            LogsPage {
                logs: window.logsViewModel
            }

            SettingsPage {
                settings: window.settingsViewModel
                overview: window.overviewViewModel
                about: window.aboutViewModel
                backup: window.backupViewModel
                systemExtension: window.systemExtensionViewModel
            }
        }
    }

    // Last, so that they are above the pages and Tab reaches them after the page.
    CaptionButtons {
        anchors.top: parent.top
        anchors.right: parent.right
        visible: TitleBand.windows
        titleBar: window.desktopViewModel.titleBar
    }
}
