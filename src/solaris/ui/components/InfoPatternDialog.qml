import QtQuick
import QtQuick.Layouts
import Solaris

// 节点池设置 (docs/routing.md §5.2): the expression that keeps information nodes, such as the
// quota left, out of the pool. The core checks it before it is stored.
AppDialog {
    id: dialog

    required property RoutingViewModel routing

    function save() {
        dialog.routing.saveInfoPattern(patternField.text)
    }

    title: qsTr("节点池设置")
    wide: true
    defaultButton: saveButton
    initialFocus: patternField

    onAboutToShow: {
        routing.clearInfoError()
        patternField.text = routing.infoPattern
    }

    AccessibleText {
        Layout.fillWidth: true
        text: qsTr("名称匹配这个正则表达式的节点是信息节点（如剩余流量、到期时间），不加入节点池。留空则不排除任何节点。")
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        spacing: 6

        UiText {
            text: qsTr("信息节点")
            role: Theme.type.caption
            color: Theme.textTertiary
        }

        AppTextField {
            id: patternField

            Layout.fillWidth: true
            font.family: Fonts.resolve(Theme.type.dataMd.fontFamily)
            error: dialog.routing.infoError !== ""
            Accessible.name: qsTr("信息节点")
            onAccepted: dialog.save()
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space3

            UiText {
                Layout.fillWidth: true
                text: qsTr("默认：%1").arg(dialog.routing.defaultInfoPattern)
                role: Theme.type.caption
                color: Theme.textMuted
                elide: Text.ElideRight
            }

            LinkButton {
                text: qsTr("恢复默认")
                enabled: patternField.text !== dialog.routing.defaultInfoPattern
                onClicked: patternField.text = dialog.routing.defaultInfoPattern
            }
        }

        AccessibleText {
            Layout.fillWidth: true
            visible: text !== ""
            text: dialog.routing.infoError
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

            text: qsTr("保存")
            variant: "primary"
            enabled: !dialog.routing.busy
            onClicked: dialog.save()
        }
    ]
}
