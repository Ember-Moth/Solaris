pragma ComponentBehavior: Bound
import QtQuick
import Qt.labs.platform as Platform
import Solaris

// The tray icon and its menu: the tunnel's state, connect or disconnect, the outbound mode,
// the window, and quitting the UI, which leaves the tunnel running. A native menu, so it
// follows the platform's look rather than DESIGN.md's in-window menus.
Platform.SystemTrayIcon {
    id: tray

    required property DesktopViewModel desktop
    required property OverviewViewModel overview
    signal showWindow

    visible: true
    icon.source: overview.connected ? desktop.trayIconOn : desktop.trayIconOff
    icon.mask: desktop.trayIconMask
    tooltip: Qt.application.name + " · " + overview.statusTitle
    onActivated: reason => {
        if (tray.desktop.showOnTrayClick && (reason === Platform.SystemTrayIcon.Trigger || reason === Platform.SystemTrayIcon.DoubleClick))
            tray.showWindow()
    }

    menu: Platform.Menu {
        Platform.MenuItem {
            text: tray.overview.statusTitle
            enabled: false
        }

        Platform.MenuSeparator {}

        Platform.MenuItem {
            text: tray.overview.connectLabel
            // Another user's tunnel is theirs to disconnect (docs/architecture.md §5.4).
            enabled: tray.overview.canToggleConnection
            onTriggered: tray.overview.toggleConnection()
        }

        Platform.Menu {
            id: modeMenu

            title: qsTr("出站模式")
            enabled: tray.overview.connected && !tray.overview.busy

            Platform.MenuItemGroup {
                id: modes
            }

            Instantiator {
                model: tray.overview.modeOptions
                onObjectAdded: (index, object) => modeMenu.insertItem(index, object)
                onObjectRemoved: (index, object) => modeMenu.removeItem(object)

                delegate: Platform.MenuItem {
                    id: modeItem

                    required property var modelData

                    text: modeItem.modelData.label
                    checkable: true
                    group: modes
                    checked: modeItem.modelData.value === tray.overview.mode
                    onTriggered: {
                        tray.overview.setMode(modeItem.modelData.value)
                        // Triggering checked it; the mode the core reports decides again.
                        checked = Qt.binding(() => modeItem.modelData.value === tray.overview.mode)
                    }
                }
            }
        }

        Platform.MenuSeparator {}

        Platform.MenuItem {
            text: qsTr("打开主窗口")
            onTriggered: tray.showWindow()
        }

        Platform.MenuSeparator {}

        Platform.MenuItem {
            text: qsTr("退出界面（隧道保持运行）")
            onTriggered: tray.desktop.quit()
        }
    }
}
