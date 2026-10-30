pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../../components"

// Chained exits: WARP and VPN Gate (docs/exits.md §6).
ColumnLayout {
    id: section

    required property RoutingViewModel routing
    required property DialogOpener opener
    readonly property WarpExit warp: routing.warp
    readonly property VpnGateExit vpngate: routing.vpngate

    spacing: Theme.space2

    Connections {
        target: section.warp

        function onOpened() {
            section.opener.show(warpDialog)
        }
    }

    Connections {
        target: section.vpngate

        function onOpened() {
            section.opener.show(vpngateDialog)
        }
    }

    SectionLabel {
        Layout.fillWidth: true
        text: qsTr("链式出口")
    }

    Group {
        Layout.fillWidth: true

        GroupRow {
            divider: false

            AbstractButton {
                id: warpButton

                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 0
                Accessible.role: Accessible.Button
                Accessible.name: qsTr("设置「%1」").arg(section.warp.name)
                Accessible.description: qsTr("%1。%2").arg(section.warp.caption).arg(section.warp.status)
                onClicked: {
                    section.opener.from(warpButton, Focus.reasonAfter(warpButton))
                    section.warp.open()
                }

                contentItem: Item {
                    ColumnLayout {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 0

                        UiText {
                            Layout.fillWidth: true
                            text: section.warp.name
                            role: Theme.type.labelMd
                            color: section.warp.active ? Theme.text : Theme.textMuted
                            elide: Text.ElideRight
                        }

                        UiText {
                            Layout.fillWidth: true
                            text: section.warp.caption
                            role: Theme.type.caption
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }
                }

                background: Item {
                    FocusRing {
                        targetRadius: 4
                        visible: warpButton.visualFocus
                    }
                }
            }

            UiText {
                text: section.warp.status
                role: Theme.type.bodySm
                color: section.warp.statusTone === "warning" ? Theme.warning : section.warp.statusTone === "muted" ? Theme.textMuted : Theme.text
            }

            IconButton {
                id: warpMore

                iconName: "more"
                accessibleName: qsTr("%1 的更多操作").arg(section.warp.name)
                onClicked: exitMenu.openBelow(warpMore)
            }
        }

        GroupRow {
            AbstractButton {
                id: vpngateButton

                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 0
                Accessible.role: Accessible.Button
                Accessible.name: qsTr("设置「%1」").arg(section.vpngate.name)
                Accessible.description: qsTr("%1。%2").arg(section.vpngate.caption).arg(section.vpngate.status)
                onClicked: {
                    section.opener.from(vpngateButton, Focus.reasonAfter(vpngateButton))
                    section.vpngate.open()
                }

                contentItem: Item {
                    ColumnLayout {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 0

                        UiText {
                            Layout.fillWidth: true
                            text: section.vpngate.name
                            role: Theme.type.labelMd
                            color: section.vpngate.active ? Theme.text : Theme.textMuted
                            elide: Text.ElideRight
                        }

                        UiText {
                            Layout.fillWidth: true
                            text: section.vpngate.caption
                            role: Theme.type.caption
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }
                }

                background: Item {
                    FocusRing {
                        targetRadius: 4
                        visible: vpngateButton.visualFocus
                    }
                }
            }

            UiText {
                text: section.vpngate.status
                role: Theme.type.bodySm
                color: section.vpngate.statusTone === "warning" ? Theme.warning : section.vpngate.statusTone === "muted" ? Theme.textMuted : Theme.text
            }

            IconButton {
                id: vpngateMore

                iconName: "more"
                accessibleName: qsTr("%1 的更多操作").arg(section.vpngate.name)
                onClicked: vpngateMenu.openBelow(vpngateMore)
            }
        }
    }

    AppMenu {
        id: exitMenu

        AppMenuItem {
            text: qsTr("设置…")
            onTriggered: {
                section.opener.from(exitMenu.trigger, exitMenu.triggerFocusReason)
                section.warp.open()
            }
        }

        AppMenuItem {
            text: section.warp.registerLabel
            enabled: section.warp.canRegister
            onTriggered: section.warp.register()
        }
    }

    AppMenu {
        id: vpngateMenu

        AppMenuItem {
            text: qsTr("设置…")
            onTriggered: {
                section.opener.from(vpngateMenu.trigger, vpngateMenu.triggerFocusReason)
                section.vpngate.open()
            }
        }

        AppMenuItem {
            text: qsTr("立即刷新")
            enabled: section.vpngate.canRefresh
            onTriggered: section.vpngate.refresh()
        }
    }

    WarpDialog {
        id: warpDialog

        warp: section.warp
    }

    VpnGateDialog {
        id: vpngateDialog

        vpngate: section.vpngate
    }
}
