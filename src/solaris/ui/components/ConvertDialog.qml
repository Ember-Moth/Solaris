pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// What a conversion makes of a subscription's or a configuration's groups and rules (docs/
// routing.md §4.7), shown before the plan is created: the summary, the rules that stay out,
// the plan's name and whether to route with it.
AppDialog {
    id: dialog

    required property PlanTransfer transfer

    function create() {
        if (nameField.text.trim() !== "")
            transfer.create(nameField.text, useBox.checked)
    }

    title: transfer.conversionTitle
    wide: true
    defaultButton: createButton
    initialFocus: nameField

    onAboutToShow: {
        nameField.text = transfer.suggestedName
        useBox.checked = true
        nameField.selectAll()
    }
    onRejected: transfer.dismiss()

    AccessibleText {
        Layout.fillWidth: true
        text: dialog.transfer.conversionSummary
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        visible: dialog.transfer.skippedRules.length > 0
        spacing: 6

        UiText {
            text: qsTr("无法转换的规则")
            role: Theme.type.caption
            color: Theme.textTertiary
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: Math.min(skippedList.contentHeight, 140) + 2
            radius: Theme.radiusSm
            color: Theme.surface
            border.width: 1
            border.color: Theme.border

            ListView {
                id: skippedList

                anchors.fill: parent
                anchors.margins: 1
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: dialog.transfer.skippedRules
                activeFocusOnTab: true
                Accessible.role: Accessible.List
                Accessible.name: qsTr("无法转换的规则")
                Keys.onUpPressed: flick(0, 800)
                Keys.onDownPressed: flick(0, -800)

                ScrollBar.vertical: ScrollBar {}

                delegate: Item {
                    id: skippedRule

                    required property int index
                    required property string modelData

                    width: ListView.view.width
                    height: 28
                    Accessible.role: Accessible.ListItem
                    Accessible.name: modelData

                    Rectangle {
                        visible: skippedRule.index > 0
                        width: parent.width
                        height: 1
                        color: Theme.divider
                    }

                    UiText {
                        anchors.verticalCenter: parent.verticalCenter
                        x: 10
                        width: parent.width - 20
                        text: skippedRule.modelData
                        role: Theme.type.dataSm
                        color: Theme.textSecondary
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        spacing: 6

        UiText {
            text: qsTr("方案名称")
            role: Theme.type.caption
            color: Theme.textTertiary
        }

        AppTextField {
            id: nameField

            Layout.fillWidth: true
            Accessible.name: qsTr("方案名称")
            onAccepted: dialog.create()
        }
    }

    AppCheckBox {
        id: useBox

        Layout.topMargin: Theme.space1
        text: qsTr("创建后改用这套方案")
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: text !== ""
        text: dialog.transfer.error
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }

    buttons: [
        AppButton {
            text: qsTr("取消")
            onClicked: dialog.reject()
        },
        AppButton {
            id: createButton

            text: qsTr("创建")
            variant: "primary"
            enabled: nameField.text.trim() !== "" && !dialog.transfer.saving
            onClicked: dialog.create()
        }
    ]
}
