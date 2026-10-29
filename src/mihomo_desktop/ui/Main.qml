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

    width: Theme.windowDefaultWidth
    height: Theme.windowDefaultHeight
    minimumWidth: Theme.windowMinWidth
    minimumHeight: Theme.windowMinHeight
    visible: true
    title: Qt.application.name
    color: Theme.background
    font.family: Fonts.resolve(Theme.type.bodyMd.fontFamily)
    font.pixelSize: Theme.type.bodyMd.fontSize

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

            PlaceholderPage {
                title: "连接"
            }

            PlaceholderPage {
                title: "日志"
            }

            PlaceholderPage {
                title: "设置"
            }
        }
    }
}
