pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// New plan or rename (docs/routing.md §4.3): a name, and for a new plan what it starts from,
// the default template or a copy of a plan. A new plan comes into use.
AppDialog {
    id: dialog

    required property RoutingViewModel routing
    property bool renaming
    property string base  // "" for the template, otherwise the plan copied

    function save() {
        if (nameField.text.trim() === "")
            return
        if (renaming)
            routing.renamePlan(nameField.text)
        else
            routing.createPlan(nameField.text, base)
    }

    title: renaming ? qsTr("重命名「%1」").arg(routing.planName) : qsTr("新建方案")
    defaultButton: saveButton
    initialFocus: nameField

    onAboutToShow: {
        routing.clearPlanError()
        base = ""
        nameField.text = renaming ? routing.planName : routing.newPlanName
        nameField.selectAll()
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        spacing: Theme.space4

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6

            UiText {
                text: qsTr("名称")
                role: Theme.type.caption
                color: Theme.textTertiary
            }

            AppTextField {
                id: nameField

                Layout.fillWidth: true
                Accessible.name: qsTr("名称")
                onAccepted: dialog.save()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: !dialog.renaming
            spacing: 0
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("基于")

            UiText {
                Layout.bottomMargin: Theme.space1
                text: qsTr("基于")
                role: Theme.type.caption
                color: Theme.textTertiary
            }

            Repeater {
                model: [
                    {
                        "value": "",
                        "label": qsTr("默认模板")
                    }
                ].concat(dialog.routing.planOptions.map(option => ({
                                "value": option.value,
                                "label": qsTr("复制「%1」").arg(option.label)
                            })))

                AbstractButton {
                    id: choice

                    required property var modelData
                    readonly property bool selected: modelData.value === dialog.base

                    Layout.fillWidth: true
                    implicitHeight: Theme.controlSm
                    padding: 0
                    checked: selected  // what screen readers hear; clicking does not toggle it
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label
                    onClicked: dialog.base = modelData.value

                    contentItem: RowLayout {
                        spacing: 10

                        RadioMark {
                            checked: choice.selected
                        }

                        UiText {
                            Layout.fillWidth: true
                            text: choice.modelData.label
                            role: Theme.type.bodySm
                            elide: Text.ElideRight
                        }
                    }

                    background: Item {
                        FocusRing {
                            targetRadius: 4
                            visible: choice.visualFocus
                        }
                    }
                }
            }
        }

        AccessibleText {
            Layout.fillWidth: true
            visible: text !== ""
            text: dialog.routing.planError
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

            text: dialog.renaming ? qsTr("保存") : qsTr("创建")
            variant: "primary"
            enabled: nameField.text.trim() !== "" && !dialog.routing.busy
            onClicked: dialog.save()
        }
    ]
}
