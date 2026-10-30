pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../../components"

// The plan's rules, then other traffic (routing.md §4.3).
Group {
    id: ruleList

    required property RoutingViewModel routing
    required property DialogOpener opener
    required property Item emptyFocus  // takes the focus when the last rule goes
    readonly property PlanRules rules: routing.planRules
    property string removingRule  // whose removal hands its focus on

    readonly property int targetWidth: 184

    function rowAt(index: int): RuleRow {
        return ruleRepeater.itemAt(Math.max(0, Math.min(index, ruleRepeater.count - 1))) as RuleRow
    }

    // A removed rule takes the focus with it: it goes to the rule that takes its place, the
    // one above when it was the last, or 添加规则 when none is left.
    function ruleRemoved(index: int, row: RuleRow) {
        if (row.ruleId !== removingRule)
            return
        removingRule = ""
        if (row.activeFocus || ruleMenu.trigger === row.moreButton) {
            const next = ruleRepeater.count > 0 ? rowAt(index).nameButton : emptyFocus
            next.forceActiveFocus(ruleMenu.triggerFocusReason)
        }
    }

    Accessible.role: Accessible.List
    Accessible.name: qsTr("规则，从上到下匹配")

    Item {
        id: rulesBox

        // The drop position while a rule is dragged; -1 otherwise.
        property int dropIndex: -1
        property string dragged

        Layout.fillWidth: true
        implicitHeight: ruleColumn.implicitHeight

        function dropAt(y: real): int {
            return Math.max(0, Math.min(ruleRepeater.count, Math.round(y / Theme.rowDouble)))
        }

        Column {
            id: ruleColumn

            width: parent.width

            Repeater {
                id: ruleRepeater

                model: ruleList.rules.rows
                delegate: RuleRow {}
                onItemRemoved: (index, item) => ruleList.ruleRemoved(index, item as RuleRow)
            }
        }

        // The insertion line (DESIGN.md "可排序列表").
        Rectangle {
            visible: rulesBox.dropIndex >= 0
            x: 12
            y: rulesBox.dropIndex * Theme.rowDouble - height / 2
            z: 1
            width: parent.width - 24
            height: Theme.insertionLine
            color: Theme.primary
        }
    }

    GroupRow {
        visible: ruleList.rules.filteredEmpty
        rowHeight: Theme.rowSingle
        divider: false

        AccessibleText {
            Layout.fillWidth: true
            text: qsTr("没有匹配的规则")
            role: Theme.type.bodySm
            color: Theme.textMuted
        }
    }

    // Other traffic: always last; only its target changes. Laid out as the rules are,
    // without their handle, box and menu.
    Item {
        Layout.fillWidth: true
        implicitHeight: Theme.rowDouble

        Rectangle {
            anchors.fill: parent
            visible: ruleList.rules.fallbackHit
            color: Theme.surfaceSelected
            bottomLeftRadius: Theme.radiusMd - 1
            bottomRightRadius: Theme.radiusMd - 1
        }

        Rectangle {
            visible: ruleRepeater.count > 0 || ruleList.rules.filteredEmpty
            width: parent.width
            height: 1
            color: Theme.divider
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.space3
            anchors.rightMargin: Theme.space2
            spacing: 10

            Item {
                implicitWidth: 16
            }

            Item {
                implicitWidth: 16
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                UiText {
                    Layout.fillWidth: true
                    text: qsTr("其他流量")
                    role: Theme.type.labelMd
                }

                UiText {
                    Layout.fillWidth: true
                    text: qsTr("以上规则都不匹配时")
                    role: Theme.type.caption
                    color: Theme.textMuted
                    elide: Text.ElideRight
                }
            }

            ColumnLayout {
                Layout.preferredWidth: ruleList.targetWidth
                Layout.fillWidth: false
                spacing: 2

                AppSelect {
                    Layout.fillWidth: true
                    options: ruleList.rules.targetOptions
                    current: ruleList.rules.fallbackTarget
                    displayText: ruleList.rules.fallbackLabel
                    accessibleName: qsTr("其他流量的目标")
                    onPicked: value => ruleList.rules.setFallback(value)
                }

                AccessibleText {
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: ruleList.rules.fallbackWarning
                    role: Theme.type.caption
                    color: Theme.warning
                    elide: Text.ElideRight
                }
            }

            Item {
                implicitWidth: 28
            }
        }
    }

    // One rule entry: the drag handle, the enable box, its name (which edits it), the target
    // and its menu (DESIGN.md "可排序列表", "列表行里的启用复选框").
    component RuleRow: Item {
        id: ruleRow

        required property int index
        required property string ruleId
        required property string name
        required property string caption
        required property bool mono
        required property string target
        required property bool active
        required property string warning
        required property bool first
        required property bool last
        required property bool editable
        required property bool hit
        readonly property alias nameButton: nameButton
        readonly property alias moreButton: moreButton

        width: ruleColumn.width
        height: Theme.rowDouble
        opacity: rulesBox.dragged === ruleId ? Theme.disabledOpacity : 1

        // The rule the test matched.
        Rectangle {
            anchors.fill: parent
            visible: ruleRow.hit
            color: Theme.surfaceSelected
            topLeftRadius: ruleRow.index === 0 ? Theme.radiusMd - 1 : 0
            topRightRadius: ruleRow.index === 0 ? Theme.radiusMd - 1 : 0
        }

        Rectangle {
            visible: ruleRow.index > 0
            width: parent.width
            height: 1
            color: Theme.divider
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.space3
            anchors.rightMargin: Theme.space2
            spacing: 10

            // The handle: the mouse's grip only; the keyboard moves rules from the menu. A
            // filtered list shows part of the rules, so it cannot be dragged.
            Icon {
                id: grip

                name: "grip"
                size: 16
                opacity: ruleList.rules.filtering ? Theme.disabledOpacity : 1

                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -6
                    enabled: !ruleList.rules.filtering
                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                    preventStealing: true
                    onPressed: rulesBox.dragged = ruleRow.ruleId
                    onPositionChanged: mouse => {
                        const y = mapToItem(rulesBox, mouse.x, mouse.y).y
                        rulesBox.dropIndex = rulesBox.dropAt(y)
                    }
                    onReleased: {
                        const drop = rulesBox.dropIndex
                        rulesBox.dragged = ""
                        rulesBox.dropIndex = -1
                        if (drop >= 0 && drop !== ruleRow.index && drop !== ruleRow.index + 1)
                            ruleList.rules.moveRule(ruleRow.ruleId, drop > ruleRow.index ? drop - 1 : drop)
                    }
                    onCanceled: {
                        rulesBox.dragged = ""
                        rulesBox.dropIndex = -1
                    }
                }
            }

            AppCheckBox {
                spacing: 0  // no text: the box alone
                checked: ruleRow.active
                Accessible.name: qsTr("启用「%1」").arg(ruleRow.name)
                onToggled: ruleList.rules.setRuleEnabled(ruleRow.ruleId, checked)
            }

            AbstractButton {
                id: nameButton

                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 0
                Accessible.role: Accessible.Button
                Accessible.name: qsTr("编辑「%1」").arg(ruleRow.name)
                Accessible.description: qsTr("%1。目标 %2").arg(ruleRow.caption).arg(ruleRow.target)
                onClicked: {
                    if (!ruleRow.editable)
                        return
                    ruleList.opener.from(nameButton, Focus.reasonAfter(nameButton))
                    ruleList.routing.editor.openEdit(ruleRow.ruleId)
                }
                Keys.onUpPressed: ruleList.rowAt(ruleRow.index - 1).nameButton.forceActiveFocus(Qt.TabFocusReason)
                Keys.onDownPressed: ruleList.rowAt(ruleRow.index + 1).nameButton.forceActiveFocus(Qt.TabFocusReason)

                contentItem: Item {
                    ColumnLayout {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 0

                        UiText {
                            Layout.fillWidth: true
                            text: ruleRow.name
                            role: ruleRow.mono ? Theme.type.dataMd : Theme.type.labelMd
                            color: ruleRow.active ? Theme.text : Theme.textMuted
                            elide: Text.ElideRight
                        }

                        UiText {
                            Layout.fillWidth: true
                            text: ruleRow.caption
                            role: Theme.type.caption
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }
                }

                background: Item {
                    FocusRing {
                        targetRadius: 4
                        visible: nameButton.visualFocus
                    }
                }
            }

            ColumnLayout {
                Layout.preferredWidth: ruleList.targetWidth
                Layout.fillWidth: false
                spacing: 2

                UiText {
                    Layout.fillWidth: true
                    text: ruleRow.target
                    role: Theme.type.bodySm
                    color: ruleRow.active ? Theme.text : Theme.textMuted
                    elide: Text.ElideRight
                }

                AccessibleText {
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: ruleRow.warning
                    role: Theme.type.caption
                    color: Theme.warning
                    elide: Text.ElideRight
                }
            }

            IconButton {
                id: moreButton

                iconName: "more"
                accessibleName: qsTr("「%1」的更多操作").arg(ruleRow.name)
                onClicked: ruleMenu.openFor(ruleRow, moreButton)
            }
        }
    }

    AppMenu {
        id: ruleMenu

        // What the items act on, copied when the menu opens: the row may go meanwhile.
        property string ruleId
        property int index
        property bool first
        property bool last
        property bool editable

        function openFor(row: RuleRow, anchor: Item) {
            ruleId = row.ruleId
            index = row.index
            first = row.first
            last = row.last
            editable = row.editable
            openBelow(anchor)
        }

        AppMenuItem {
            text: qsTr("编辑…")
            enabled: ruleMenu.editable
            onTriggered: {
                ruleList.opener.from(ruleMenu.trigger, ruleMenu.triggerFocusReason)
                ruleList.routing.editor.openEdit(ruleMenu.ruleId)
            }
        }

        AppMenuItem {
            text: qsTr("上移")
            enabled: !ruleMenu.first
            onTriggered: ruleList.rules.moveRuleBy(ruleMenu.ruleId, -1)
        }

        AppMenuItem {
            text: qsTr("下移")
            enabled: !ruleMenu.last
            onTriggered: ruleList.rules.moveRuleBy(ruleMenu.ruleId, 1)
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("删除")
            destructive: true
            onTriggered: {
                ruleList.removingRule = ruleMenu.ruleId
                ruleList.rules.removeRule(ruleMenu.ruleId)
            }
        }
    }
}
