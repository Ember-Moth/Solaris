pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../../components"

// The plan's groups: built in, one per region with nodes, and custom (routing.md §4.5).
ColumnLayout {
    id: section

    required property RoutingViewModel routing
    required property DialogOpener opener
    readonly property PlanGroups groups: routing.planGroups

    spacing: Theme.space5

    Connections {
        target: section.groups

        function onRemovalRequested() {
            removeGroupDialog.showFor(customGroupMenu.trigger, customGroupMenu.triggerFocusReason)
        }
    }

    Repeater {
        model: [
            {
                "title": qsTr("内置"),
                "hint": "",
                "rows": section.groups.builtin,
                "empty": ""
            },
            {
                "title": qsTr("地区"),
                "hint": qsTr("按节点名称自动生成"),
                "rows": section.groups.regions,
                "empty": qsTr("节点池为空，没有地区组")
            },
            {
                "title": qsTr("自建"),
                "hint": "",
                "rows": section.groups.custom,
                "empty": qsTr("还没有自建策略组")
            }
        ]

        ColumnLayout {
            id: groupSection

            required property var modelData

            Layout.fillWidth: true
            spacing: Theme.space2

            RowLayout {
                Layout.fillWidth: true

                SectionLabel {
                    Layout.fillWidth: true
                    text: groupSection.modelData.title
                }

                UiText {
                    text: groupSection.modelData.hint
                    role: Theme.type.caption
                    color: Theme.textMuted
                }
            }

            Group {
                Layout.fillWidth: true

                Repeater {
                    id: groupRepeater

                    model: groupSection.modelData.rows

                    GroupRow {
                        id: groupRow

                        required property int index
                        required property string groupId
                        required property string name
                        required property string caption
                        required property string type
                        required property bool hidden
                        readonly property bool custom: groupSection.modelData.rows === section.groups.custom

                        divider: index > 0

                        // A custom group's name opens its editor.
                        AbstractButton {
                            id: groupName

                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            padding: 0
                            focusPolicy: groupRow.custom ? Qt.StrongFocus : Qt.NoFocus
                            Accessible.role: groupRow.custom ? Accessible.Button : Accessible.StaticText
                            Accessible.name: groupRow.custom ? qsTr("编辑「%1」").arg(groupRow.name) : groupRow.name
                            Accessible.description: groupRow.caption
                            onClicked: {
                                if (!groupRow.custom)
                                    return
                                section.opener.from(groupName, Focus.reasonAfter(groupName))
                                section.routing.groupEditor.openEdit(groupRow.groupId)
                            }

                            contentItem: Item {
                                ColumnLayout {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 0

                                    UiText {
                                        Layout.fillWidth: true
                                        text: groupRow.name
                                        role: Theme.type.labelMd
                                        color: groupRow.hidden ? Theme.textMuted : Theme.text
                                        elide: Text.ElideRight
                                    }

                                    UiText {
                                        Layout.fillWidth: true
                                        text: groupRow.caption
                                        role: Theme.type.caption
                                        color: Theme.textMuted
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            background: Item {
                                FocusRing {
                                    targetRadius: 4
                                    visible: groupName.visualFocus
                                }
                            }
                        }

                        IconButton {
                            id: groupMore

                            iconName: "more"
                            accessibleName: qsTr("%1 的更多操作").arg(groupRow.name)
                            onClicked: {
                                if (groupRow.custom)
                                    customGroupMenu.openFor(groupRow.groupId, groupMore)
                                else
                                    groupMenu.openFor(groupRow.groupId, groupRow.type, groupRow.hidden, groupMore)
                            }
                        }
                    }
                }

                GroupRow {
                    visible: groupRepeater.count === 0
                    divider: false
                    rowHeight: Theme.rowSingle

                    AccessibleText {
                        Layout.fillWidth: true
                        text: groupSection.modelData.empty
                        role: Theme.type.bodySm
                        color: Theme.textMuted
                    }
                }
            }
        }
    }

    AppMenu {
        id: groupMenu

        property string groupId
        property string type
        property bool hidden

        function openFor(id: string, groupType: string, isHidden: bool, anchor: Item) {
            groupId = id
            type = groupType
            hidden = isHidden
            openBelow(anchor)
        }

        Instantiator {
            model: section.groups.typeOptions
            onObjectAdded: (index, object) => groupMenu.insertItem(index, object)
            onObjectRemoved: (index, object) => groupMenu.removeItem(object)

            delegate: AppMenuItem {
                id: typeChoice

                required property var modelData

                text: typeChoice.modelData.label
                checkable: true
                checked: typeChoice.modelData.value === groupMenu.type
                onTriggered: {
                    section.groups.setType(groupMenu.groupId, typeChoice.modelData.value)
                    checked = Qt.binding(() => typeChoice.modelData.value === groupMenu.type)
                }
            }
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: groupMenu.hidden ? qsTr("显示") : qsTr("隐藏")
            onTriggered: section.groups.setHidden(groupMenu.groupId, !groupMenu.hidden)
        }

        AppMenuItem {
            text: qsTr("复制为自建策略组…")
            onTriggered: {
                section.opener.from(groupMenu.trigger, groupMenu.triggerFocusReason)
                section.routing.groupEditor.openCopy(groupMenu.groupId)
            }
        }
    }

    AppMenu {
        id: customGroupMenu

        property string groupId

        function openFor(id: string, anchor: Item) {
            groupId = id
            openBelow(anchor)
        }

        AppMenuItem {
            text: qsTr("编辑…")
            onTriggered: {
                section.opener.from(customGroupMenu.trigger, customGroupMenu.triggerFocusReason)
                section.routing.groupEditor.openEdit(customGroupMenu.groupId)
            }
        }

        AppMenuItem {
            text: qsTr("复制…")
            onTriggered: {
                section.opener.from(customGroupMenu.trigger, customGroupMenu.triggerFocusReason)
                section.routing.groupEditor.openCopy(customGroupMenu.groupId)
            }
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("删除")
            destructive: true
            onTriggered: section.groups.requestRemove(customGroupMenu.groupId)
        }
    }

    ConfirmDialog {
        id: removeGroupDialog

        title: section.groups.removalTitle
        message: section.groups.removalText
        confirmText: qsTr("删除")
        destructive: true
        onAccepted: section.groups.confirmRemove()
    }
}
