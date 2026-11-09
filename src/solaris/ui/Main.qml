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

    // Whether the window holds its pages: while it shows, and for a moment after it hides.
    // Hidden in the tray or the menu bar, it lets them go and gives back the memory they and
    // their drawing took (docs/ui.md §8, "隐藏时释放"); it makes them again before it shows.
    // The page on show and what the ViewModels hold come back as they were, with what `kept`
    // keeps; scroll positions and the keyboard focus do not.
    property bool holdsPages: !desktopViewModel.startHidden

    // Bring the window back from the tray, the Dock or a second launch, its pages made before
    // its first frame.
    function present() {
        holdsPages = true
        if (visibility === Window.Minimized)
            showNormal()
        else
            show()
        raise()
        requestActivate()
    }

    // Let the pages go, unless a dialog or a menu is still open on them, and give back what
    // drawing the window took. The window keeps the routing page's tab and the import form.
    function release() {
        if (visible || desktopViewModel.quitting)
            return
        if (!Overlay.overlay.children.some(item => item.visible)) {
            const routing = routingLoader.item as RoutingPage
            const profiles = profilesLoader.item as ProfilesPage
            if (routing)
                kept.routingTab = routing.tab
            if (profiles)
                kept.importForm = profiles.importDraft()
            holdsPages = false
        }
        desktopViewModel.releaseWindow(window)
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

    // macOS: Solaris is in the Dock while the window shows, however it came up, and in the
    // menu bar only while it hides; showing it activates Solaris.
    onVisibleChanged: {
        desktopViewModel.setWindowShown(visible)
        if (visible) {
            releaseTimer.stop()
            holdsPages = true
        } else {
            releaseTimer.restart()
        }
    }

    // Long enough that a window brought straight back, or Solaris quitting as it closes, does
    // not load its pages again for nothing.
    Timer {
        id: releaseTimer

        interval: 2000
        onTriggered: window.release()
    }

    // What the pages keep while the window lets them go. The ViewModels hold the rest of what
    // they show, such as the proxies page's group and the search fields.
    QtObject {
        id: kept

        property string routingTab: "rules"
        property var importForm: null  // ProfilesPage.importDraft()
    }

    // Closing the window leaves the UI in the tray, the tunnel running, or quits Solaris,
    // which disconnects first (D41) and shows the window again if that fails.
    onClosing: close => {
        close.accepted = false
        hide()
        if (!desktopViewModel.hideOnClose)
            desktopViewModel.quit()
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

        function onQuitFailed() {
            window.present()
            // A retry may fail again while the dialog still fades out; it opens again.
            if (!quitDialog.holdsFocus)
                quitDialog.show(window.activeFocusItem)
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

    QuitDialog {
        id: quitDialog

        desktop: window.desktopViewModel
    }

    ExtensionSetupDialog {
        id: extensionSetupDialog

        overview: window.overviewViewModel
    }

    Connections {
        target: window.overviewViewModel

        function onExtensionSetupRequested() {
            const focused = window.visible ? window.activeFocusItem : null
            window.shellViewModel.setPage("overview")
            window.present()
            const overview = overviewLoader.item as OverviewPage
            const from = Focus.within(focused, overview) ? focused : overview.connectionControl
            if (!extensionSetupDialog.visible)
                extensionSetupDialog.show(from)
        }

        function onChanged() {
            if (window.overviewViewModel.extensionSetup === "" && extensionSetupDialog.visible)
                extensionSetupDialog.close()
        }
    }

    // An import that ends while the window has let the page go closes its form, as it would
    // on the page.
    Connections {
        target: window.profilesViewModel

        function onImported() {
            kept.importForm = null
        }
    }

    // The tunnel stopped under a page that needs it, which gives way to the overview: focus
    // on the page moves to the overview's tab, with its ring if the keyboard had put it there.
    Connections {
        target: window.shellViewModel

        function onLeavingPage() {
            const focused = window.activeFocusItem
            if (focused && Focus.within(focused, pages.children[window.shellViewModel.pageIndex]))
                sidebar.tab("overview")?.forceActiveFocus(Focus.reasonAfter(focused))
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Sidebar {
            id: sidebar

            Layout.fillHeight: true
            Layout.preferredWidth: Theme.sidebarWidth
            shell: window.shellViewModel
            routing: window.routingViewModel
        }

        StackLayout {
            id: pages

            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: window.shellViewModel.pageIndex

            PageLoader {
                id: overviewLoader

                active: window.holdsPages

                sourceComponent: OverviewPage {
                    overview: window.overviewViewModel
                    profiles: window.profilesViewModel
                    routing: window.routingViewModel
                    shell: window.shellViewModel
                }
            }

            PageLoader {
                active: window.holdsPages

                sourceComponent: ProxiesPage {
                    proxies: window.proxiesViewModel
                }
            }

            PageLoader {
                id: profilesLoader

                active: window.holdsPages

                sourceComponent: ProfilesPage {
                    profiles: window.profilesViewModel
                    Component.onCompleted: restoreImport(kept.importForm)
                }
            }

            PageLoader {
                id: routingLoader

                active: window.holdsPages

                sourceComponent: RoutingPage {
                    routing: window.routingViewModel
                    shell: window.shellViewModel
                    tab: kept.routingTab
                }
            }

            PageLoader {
                active: window.holdsPages

                sourceComponent: ConnectionsPage {
                    connections: window.connectionsViewModel
                }
            }

            PageLoader {
                active: window.holdsPages

                sourceComponent: LogsPage {
                    logs: window.logsViewModel
                }
            }

            PageLoader {
                active: window.holdsPages

                sourceComponent: SettingsPage {
                    settings: window.settingsViewModel
                    overview: window.overviewViewModel
                    about: window.aboutViewModel
                    backup: window.backupViewModel
                    systemExtension: window.systemExtensionViewModel
                }
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
