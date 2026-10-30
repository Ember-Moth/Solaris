pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../../components"

// Mesh networks: the ones added in the app (docs/mesh.md §10.7), then the pool's mesh
// outbounds (§5); rule targets of their own, in no group.
ColumnLayout {
    id: meshSection

    required property RoutingViewModel routing
    required property DialogOpener opener
    readonly property MeshNetworks networks: routing.meshNetworks

    // 使用它的规则: the page goes to the rules tab, filtered by the target.
    signal rulesRequested

    spacing: Theme.space2

    // How the networks stand is asked for while the section is on screen.
    onVisibleChanged: networks.setWatching(visible)
    Component.onCompleted: networks.setWatching(visible)

    Connections {
        target: meshSection.networks

        function onOpened() {
            meshSection.opener.show(meshDialog)
        }

        function onSaved() {
            meshDialog.close()
        }

        function onRemovalRequested() {
            removeNetworkDialog.showFor(networkMenu.trigger, networkMenu.triggerFocusReason)
        }
    }

    SectionLabel {
        Layout.fillWidth: true
        text: qsTr("组网")
    }

    Group {
        Layout.fillWidth: true

        Repeater {
            model: meshSection.networks.rows

            GroupRow {
                id: networkRow

                required property int index
                required property string networkId
                required property string name
                required property string caption
                required property string status
                required property string tone
                required property bool canLogin
                required property string address

                divider: index > 0

                // The name opens the network's settings.
                AbstractButton {
                    id: networkName

                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    padding: 0
                    Accessible.role: Accessible.Button
                    Accessible.name: qsTr("编辑「%1」").arg(networkRow.name)
                    Accessible.description: qsTr("%1。%2").arg(networkRow.caption).arg(networkRow.status)
                    onClicked: {
                        meshSection.opener.from(networkName, Focus.reasonAfter(networkName))
                        meshSection.networks.openEdit(networkRow.networkId)
                    }

                    contentItem: Item {
                        ColumnLayout {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 0

                            UiText {
                                Layout.fillWidth: true
                                text: networkRow.name
                                role: Theme.type.labelMd
                                elide: Text.ElideRight
                            }

                            UiText {
                                Layout.fillWidth: true
                                text: networkRow.caption
                                role: Theme.type.caption
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                    }

                    background: Item {
                        FocusRing {
                            targetRadius: 4
                            visible: networkName.visualFocus
                        }
                    }
                }

                UiText {
                    text: networkRow.status
                    role: Theme.type.bodySm
                    color: networkRow.tone === "warning" ? Theme.warning : networkRow.tone === "muted" ? Theme.textMuted : Theme.text
                }

                IconButton {
                    id: networkMore

                    iconName: "more"
                    accessibleName: qsTr("%1 的更多操作").arg(networkRow.name)
                    onClicked: networkMenu.openFor(networkRow.networkId, networkRow.canLogin, networkRow.address, networkMore)
                }
            }
        }

        Repeater {
            model: meshSection.networks.outbounds

            GroupRow {
                id: meshRow

                required property int index
                required property string meshId
                required property string name
                required property string caption

                divider: index > 0 || meshSection.networks.hasRows

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Accessible.role: Accessible.StaticText
                    Accessible.name: meshRow.name
                    Accessible.description: meshRow.caption

                    UiText {
                        Layout.fillWidth: true
                        text: meshRow.name
                        role: Theme.type.labelMd
                        elide: Text.ElideRight
                    }

                    UiText {
                        Layout.fillWidth: true
                        text: meshRow.caption
                        role: Theme.type.caption
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }
                }

                IconButton {
                    id: meshMore

                    iconName: "more"
                    accessibleName: qsTr("%1 的更多操作").arg(meshRow.name)
                    onClicked: meshMenu.openFor(meshRow.meshId, meshMore)
                }
            }
        }

        GroupRow {
            divider: meshSection.networks.hasRows || meshSection.networks.hasOutbounds
            rowHeight: Theme.rowSingle

            AccessibleText {
                Layout.fillWidth: true
                text: meshSection.networks.hasRows || meshSection.networks.hasOutbounds ? qsTr("Tailscale、ZeroTier、EasyTier") : qsTr("还没有组网：访问自己的设备和内网")
                role: Theme.type.bodySm
                color: Theme.textMuted
                elide: Text.ElideRight
            }

            AppButton {
                id: addNetworkButton

                variant: "compact"
                text: qsTr("添加网络…")
                enabled: !meshSection.networks.busy
                onClicked: {
                    meshSection.opener.from(addNetworkButton, Focus.reasonAfter(addNetworkButton))
                    meshSection.networks.openAdd()
                }
            }
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: text !== ""
        text: meshSection.networks.notice
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }

    AppMenu {
        id: networkMenu

        property string networkId
        property bool canLogin
        property string address

        function openFor(id: string, login: bool, at: string, anchor: Item) {
            networkId = id
            canLogin = login
            address = at
            openBelow(anchor)
        }

        AppMenuItem {
            text: qsTr("登录…")
            visible: networkMenu.canLogin
            onTriggered: meshSection.networks.login(networkMenu.networkId)
        }

        AppMenuItem {
            text: qsTr("复制本机地址")
            enabled: networkMenu.address !== ""
            onTriggered: meshSection.networks.copyAddress(networkMenu.networkId)
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("添加常用规则")
            enabled: !meshSection.routing.busy
            onTriggered: {
                meshSection.opener.from(networkMenu.trigger, networkMenu.triggerFocusReason)
                meshSection.networks.addCommonRules(networkMenu.networkId)
            }
        }

        AppMenuItem {
            text: qsTr("使用它的规则")
            onTriggered: {
                meshSection.networks.showRules(networkMenu.networkId)
                meshSection.rulesRequested()
            }
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("编辑…")
            onTriggered: {
                meshSection.opener.from(networkMenu.trigger, networkMenu.triggerFocusReason)
                meshSection.networks.openEdit(networkMenu.networkId)
            }
        }

        AppMenuItem {
            text: qsTr("删除…")
            destructive: true
            onTriggered: meshSection.networks.requestRemove(networkMenu.networkId)
        }
    }

    AppMenu {
        id: meshMenu

        property string meshId

        function openFor(id: string, anchor: Item) {
            meshId = id
            openBelow(anchor)
        }

        // Tailscale's rules go in at once; the others open the rule dialog.
        AppMenuItem {
            text: qsTr("添加常用规则")
            enabled: !meshSection.routing.busy
            onTriggered: {
                meshSection.opener.from(meshMenu.trigger, meshMenu.triggerFocusReason)
                meshSection.networks.addOutboundRules(meshMenu.meshId)
            }
        }

        AppMenuItem {
            text: qsTr("使用它的规则")
            onTriggered: {
                meshSection.networks.showOutboundRules(meshMenu.meshId)
                meshSection.rulesRequested()
            }
        }
    }

    MeshDialog {
        id: meshDialog

        networks: meshSection.networks
    }

    ConfirmDialog {
        id: removeNetworkDialog

        title: meshSection.networks.removalTitle
        message: meshSection.networks.removalText
        confirmText: qsTr("删除")
        destructive: true
        onAccepted: meshSection.networks.confirmRemove()
    }
}
