import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import MihomoDesktop
import "components"
import "pages"

ApplicationWindow {
    id: window

    // Injected from Python via QQmlApplicationEngine.setInitialProperties.
    required property ShellViewModel shellViewModel
    required property OverviewViewModel overviewViewModel
    required property ProxiesViewModel proxiesViewModel
    required property ProfilesViewModel profilesViewModel
    required property Appearance appearance
    required property ConnectionsViewModel connectionsViewModel
    required property LogsViewModel logsViewModel
    required property SettingsViewModel settingsViewModel
    required property DesktopViewModel desktopViewModel

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
    color: Theme.background
    font.family: Fonts.resolve(Theme.type.bodyMd.fontFamily)
    font.pixelSize: Theme.type.bodyMd.fontSize

    // The Basic style's own drawing (scroll bars, text selection, Qt's context menus) takes
    // the theme's tokens too; popups inherit the window's palette (architecture.md §7.6).
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

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Sidebar {
            Layout.fillHeight: true
            Layout.preferredWidth: Theme.sidebarWidth
            shell: window.shellViewModel
            profiles: window.profilesViewModel
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: window.shellViewModel.pageIndex

            OverviewPage {
                overview: window.overviewViewModel
                profiles: window.profilesViewModel
                shell: window.shellViewModel
            }

            ProxiesPage {
                proxies: window.proxiesViewModel
            }

            ProfilesPage {
                profiles: window.profilesViewModel
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
            }
        }
    }
}
