pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Solaris

// Adds or changes a mesh network (docs/mesh.md §10.7): its type, name, the type's own
// fields and the group it connects through. Secrets are never shown: a field left empty
// keeps the saved one, which the placeholder says.
AppDialog {
    id: dialog

    required property MeshNetworks networks
    property string login: "browser"
    property string via: ""
    readonly property string kind: networks.kind
    readonly property var saved: networks.savedSecrets

    function load() {
        const values = networks.values
        nameField.text = values.name
        login = values.login
        via = values.via
        authKeyField.text = ""
        controlField.text = values.control_url
        hostnameField.text = values.hostname
        networkField.text = values.network
        identityField.text = ""
        networkNameField.text = values.network_name
        networkSecretField.text = ""
        peersField.text = values.peers
        ipv4Field.text = values.ipv4
    }

    function save() {
        networks.save({
            "name": nameField.text,
            "via": via,
            "login": login,
            "auth_key": authKeyField.text,
            "control_url": controlField.text,
            "hostname": hostnameField.text,
            "network": networkField.text,
            "identity_secret": identityField.text,
            "network_name": networkNameField.text,
            "network_secret": networkSecretField.text,
            "peers": peersField.text,
            "ipv4": ipv4Field.text
        })
    }

    function savedHint(secret: string, empty: string): string {
        return saved.indexOf(secret) >= 0 ? qsTr("已保存；留空不改") : empty
    }

    title: networks.title
    wide: true
    defaultButton: saveButton
    initialFocus: nameField

    onAboutToShow: load()
    // A new type starts from its own defaults; the name typed so far stays.
    onKindChanged: {
        const name = nameField.text
        load()
        nameField.text = name
    }

    // Two columns, so the longest type still fits the smallest window. Hidden fields leave
    // no cell, so each type's fields pair up in this order.
    GridLayout {
        id: grid

        // From the dialog's fixed width: the grid's own would depend on its children.
        readonly property real full: dialog.availableWidth
        readonly property real half: (full - columnSpacing) / 2

        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        columns: 2
        columnSpacing: Theme.space4
        rowSpacing: Theme.space3

        Field {
            label: qsTr("类型")
            span: 2
            visible: !dialog.networks.editing

            SegmentedControl {
                options: dialog.networks.kindOptions
                current: dialog.kind
                accessibleName: qsTr("类型")
                onPicked: value => dialog.networks.setKind(value)
            }
        }

        Field {
            label: qsTr("名称")

            AppTextField {
                id: nameField

                Layout.fillWidth: true
                placeholderText: qsTr("如：家里")
                Accessible.name: qsTr("名称")
                onAccepted: dialog.save()
            }
        }

        Field {
            label: qsTr("设备名")
            visible: dialog.kind === "tailscale"

            AppTextField {
                id: hostnameField

                Layout.fillWidth: true
                Accessible.name: qsTr("设备名")
                onAccepted: dialog.save()
            }
        }

        Field {
            label: qsTr("网络 ID")
            visible: dialog.kind === "zerotier"

            AppTextField {
                id: networkField

                Layout.fillWidth: true
                placeholderText: qsTr("16 位十六进制数")
                Accessible.name: qsTr("网络 ID")
                onAccepted: dialog.save()
            }
        }

        Field {
            label: qsTr("本机地址（可选）")
            visible: dialog.kind === "easytier"

            AppTextField {
                id: ipv4Field

                Layout.fillWidth: true
                placeholderText: qsTr("如 10.144.144.2/24；留空由网络分配")
                Accessible.name: qsTr("本机地址")
                onAccepted: dialog.save()
            }
        }

        // Tailscale
        Field {
            label: qsTr("登录方式")
            visible: dialog.kind === "tailscale"

            SegmentedControl {
                options: dialog.networks.loginOptions
                current: dialog.login
                accessibleName: qsTr("登录方式")
                onPicked: value => dialog.login = value
            }
        }

        Field {
            label: qsTr("授权密钥")
            visible: dialog.kind === "tailscale" && dialog.login === "key"

            AppTextField {
                id: authKeyField

                Layout.fillWidth: true
                echoMode: TextInput.Password
                placeholderText: dialog.savedHint("auth_key", "tskey-auth-…")
                Accessible.name: qsTr("授权密钥")
                onAccepted: dialog.save()
            }
        }

        Field {
            label: qsTr("控制服务器（可选）")
            span: 2
            visible: dialog.kind === "tailscale"

            AppTextField {
                id: controlField

                Layout.fillWidth: true
                placeholderText: qsTr("留空使用 Tailscale；自建的 Headscale 填它的地址")
                Accessible.name: qsTr("控制服务器")
                onAccepted: dialog.save()
            }
        }

        // ZeroTier
        Field {
            label: qsTr("身份密钥（可选）")
            caption: qsTr("留空由本机生成；之后要在网络的管理页授权这台设备")
            span: 2
            visible: dialog.kind === "zerotier"

            AppTextField {
                id: identityField

                Layout.fillWidth: true
                echoMode: TextInput.Password
                placeholderText: dialog.savedHint("identity_secret", "")
                Accessible.name: qsTr("身份密钥")
                onAccepted: dialog.save()
            }
        }

        // EasyTier
        Field {
            label: qsTr("网络名称")
            visible: dialog.kind === "easytier"

            AppTextField {
                id: networkNameField

                Layout.fillWidth: true
                Accessible.name: qsTr("网络名称")
                onAccepted: dialog.save()
            }
        }

        Field {
            label: qsTr("网络密钥")
            visible: dialog.kind === "easytier"

            AppTextField {
                id: networkSecretField

                Layout.fillWidth: true
                echoMode: TextInput.Password
                placeholderText: dialog.savedHint("network_secret", "")
                Accessible.name: qsTr("网络密钥")
                onAccepted: dialog.save()
            }
        }

        Field {
            label: qsTr("对等节点")
            caption: qsTr("多个用逗号分开；本机只连出去，不接受连接")
            span: 2
            visible: dialog.kind === "easytier"

            AppTextField {
                id: peersField

                Layout.fillWidth: true
                placeholderText: "tcp://example.com:11010"
                Accessible.name: qsTr("对等节点")
                onAccepted: dialog.save()
            }
        }

        Field {
            label: qsTr("经由")
            caption: dialog.via !== "" ? qsTr("经节点连接时没有点对点直连，只走中继") : ""
            span: 2

            AppSelect {
                Layout.preferredWidth: grid.half
                options: dialog.networks.viaOptions
                current: dialog.via
                accessibleName: qsTr("经由")
                onPicked: value => dialog.via = value
            }
        }

        AccessibleText {
            Layout.columnSpan: 2
            Layout.fillWidth: true
            visible: text !== ""
            text: dialog.networks.error
            role: Theme.type.caption
            color: Theme.danger
            wrapMode: Text.Wrap
        }
    }

    buttons: [
        AppButton {
            text: qsTr("取消")
            onClicked: dialog.reject()
        },
        AppButton {
            id: saveButton

            text: dialog.networks.editing ? qsTr("保存") : qsTr("添加")
            variant: "primary"
            enabled: nameField.text.trim() !== "" && !dialog.networks.busy
            onClicked: dialog.save()
        }
    ]

    // A field: its label, the control, and a line on what it does; half the width unless
    // it spans both columns.
    component Field: ColumnLayout {
        id: field

        property string label
        property string caption
        property int span: 1
        default property alias control: holder.data

        Layout.columnSpan: span
        Layout.fillWidth: true
        Layout.preferredWidth: span === 1 ? grid.half : grid.full
        Layout.alignment: Qt.AlignTop
        spacing: 6

        UiText {
            text: field.label
            role: Theme.type.caption
            color: Theme.textTertiary
        }

        ColumnLayout {
            id: holder

            Layout.fillWidth: true
            spacing: 0
        }

        UiText {
            Layout.fillWidth: true
            visible: field.caption !== ""
            text: field.caption
            role: Theme.type.caption
            color: Theme.textMuted
            wrapMode: Text.Wrap
        }
    }
}
