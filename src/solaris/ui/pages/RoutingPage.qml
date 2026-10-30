pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../components"

// The routing page (docs/routing.md §4.3): the page switch 订阅自带 / 我的方案 in the title
// bar; under it, the rules of the profile in use, or the user's plan with its rules and its
// groups.
ScrollView {
    id: page

    required property RoutingViewModel routing
    required property ShellViewModel shell
    property string tab: "rules"
    property string removingRule  // whose removal hands its focus on
    property Item dialogTrigger  // gets focus back when a dialog closes
    property int dialogFocusReason: Qt.OtherFocusReason

    readonly property int targetWidth: 184
    readonly property bool planMode: routing.mode === "plan"

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
            const next = ruleRepeater.count > 0 ? rowAt(index).nameButton : addButton
            next.forceActiveFocus(ruleMenu.triggerFocusReason)
        }
    }

    // The read-only rule table scrolls the page with the arrows.
    function scrollBy(dy: real) {
        const view = page.contentItem as Flickable
        view.contentY = Math.max(0, Math.min(view.contentHeight - view.height, view.contentY + dy))
    }

    function openDialog(dialog: AppDialog, trigger: Item, reason: int) {
        dialogTrigger = trigger
        dialogFocusReason = reason
        dialog.showFor(trigger, reason)
    }

    contentWidth: availableWidth
    clip: true

    ScrollToFocus {
        flickable: page.contentItem as Flickable
    }

    Connections {
        target: page.routing.editor

        function onOpened() {
            ruleDialog.showFor(page.dialogTrigger, page.dialogFocusReason)
        }

        function onSaved() {
            ruleDialog.close()
        }
    }

    Connections {
        target: page.routing

        function onPlanSaved() {
            planDialog.close()
        }

        function onPlanRemovalRequested() {
            removePlanDialog.showFor(planMenu.trigger, planMenu.triggerFocusReason)
        }

        function onInfoPatternSaved() {
            infoDialog.close()
        }
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: Theme.space5

        PageHeader {
            Layout.fillWidth: true
            Layout.topMargin: Theme.pageTop
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            title: qsTr("分流")
            detail: page.routing.summary
            detailColor: page.routing.hasError ? Theme.danger : Theme.textMuted
            undoText: page.routing.undoText
            undoName: page.routing.undoName
            onUndoRequested: page.routing.undo()

            // The page switch (DESIGN.md "页面级开关").
            SegmentedControl {
                options: page.routing.modeOptions
                current: page.routing.mode
                accessibleName: qsTr("分流方式")
                enabled: !page.routing.busy
                onPicked: value => page.routing.setMode(value)
            }
        }

        // -- 订阅自带 ----------------------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            Layout.bottomMargin: Theme.pageBottom
            visible: !page.planMode
            spacing: Theme.space5

            RowLayout {
                Layout.fillWidth: true
                visible: page.routing.hasProfiles && page.routing.hasRulesProfile
                spacing: Theme.space3

                UiText {
                    text: qsTr("使用的订阅")
                    role: Theme.type.bodySm
                    color: Theme.textSecondary
                }

                AppSelect {
                    Layout.preferredWidth: 220
                    options: page.routing.profileOptions
                    current: page.routing.profileId
                    accessibleName: qsTr("使用的订阅")
                    enabled: !page.routing.busy
                    onPicked: value => page.routing.useProfile(value)
                }

                SearchField {
                    Layout.preferredWidth: Math.max(180, placeholderWidth)
                    implicitHeight: Theme.controlSm
                    visible: page.routing.profileRunning
                    placeholderText: qsTr("搜索规则")
                    text: page.routing.ruleQuery
                    onTextEdited: page.routing.setRuleQuery(text)
                }

                Item {
                    Layout.fillWidth: true
                }
            }

            // Every profile has nodes only.
            Group {
                Layout.fillWidth: true
                Layout.maximumWidth: 720
                visible: !page.routing.hasRulesProfile

                GroupRow {
                    divider: false

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        AccessibleText {
                            Layout.fillWidth: true
                            text: page.routing.hasProfiles ? qsTr("订阅里没有规则") : qsTr("尚未导入订阅")
                            role: Theme.type.labelMd
                        }

                        AccessibleText {
                            Layout.fillWidth: true
                            text: page.routing.hasProfiles ? qsTr("这些订阅只有节点；改用我的方案后，它们的节点组成节点池") : qsTr("导入订阅后才能使用它的规则")
                            role: Theme.type.caption
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }

                    AppButton {
                        variant: "compact"
                        text: page.routing.hasProfiles ? qsTr("改用我的方案") : qsTr("导入订阅")
                        onClicked: {
                            if (page.routing.hasProfiles)
                                page.routing.setMode("plan")
                            else
                                page.shell.setPage("profiles")
                        }
                    }
                }
            }

            // The profile is not running: what the core read from it.
            Group {
                Layout.fillWidth: true
                Layout.maximumWidth: 720
                visible: page.routing.hasRulesProfile && !page.routing.profileRunning && page.routing.profileCounts !== ""

                GroupRow {
                    divider: false
                    rowHeight: Theme.rowSingle

                    AccessibleText {
                        Layout.fillWidth: true
                        text: page.routing.profileCounts
                        role: Theme.type.bodySm
                        color: Theme.textSecondary
                        elide: Text.ElideRight
                    }
                }
            }

            // The profile runs: its groups and rules, read only.
            GridLayout {
                id: sourcePanes

                readonly property bool twoPanes: width >= 260 + Theme.space6 + 440

                Layout.fillWidth: true
                visible: page.routing.profileRunning
                columns: twoPanes ? 2 : 1
                columnSpacing: Theme.space6
                rowSpacing: Theme.space5

                ColumnLayout {
                    Layout.preferredWidth: sourcePanes.twoPanes ? 260 : sourcePanes.width
                    Layout.fillWidth: !sourcePanes.twoPanes
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space2

                    RowLayout {
                        Layout.fillWidth: true

                        SectionLabel {
                            Layout.fillWidth: true
                            text: qsTr("策略组")
                        }

                        UiText {
                            text: page.routing.profileGroupCount
                            role: Theme.type.caption
                            color: Theme.textMuted
                        }
                    }

                    Group {
                        Layout.fillWidth: true

                        Repeater {
                            model: page.routing.profileGroups

                            GroupRow {
                                id: sourceGroup

                                required property int index
                                required property string name
                                required property string caption

                                divider: index > 0

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0

                                    UiText {
                                        Layout.fillWidth: true
                                        text: sourceGroup.name
                                        role: Theme.type.labelMd
                                        elide: Text.ElideRight
                                    }

                                    UiText {
                                        Layout.fillWidth: true
                                        text: sourceGroup.caption
                                        role: Theme.type.caption
                                        color: Theme.textMuted
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space2

                    RowLayout {
                        Layout.fillWidth: true

                        SectionLabel {
                            Layout.fillWidth: true
                            text: qsTr("规则")
                        }

                        UiText {
                            text: page.routing.profileRuleCount
                            role: Theme.type.caption
                            color: Theme.textMuted
                        }
                    }

                    // The read-only rule table (DESIGN.md "只读规则表"): one Tab stop; the
                    // arrows scroll the page.
                    Item {
                        id: sourceRules

                        Layout.fillWidth: true
                        implicitHeight: sourceRuleGroup.implicitHeight
                        activeFocusOnTab: true
                        Accessible.role: Accessible.Table
                        Accessible.name: qsTr("规则")
                        Accessible.focusable: true
                        Keys.onUpPressed: page.scrollBy(-36)
                        Keys.onDownPressed: page.scrollBy(36)
                        Keys.onPressed: event => {
                            if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
                                page.scrollBy((event.key === Qt.Key_PageUp ? -1 : 1) * page.height * 0.9)
                                event.accepted = true
                            }
                        }

                        FocusRing {
                            targetRadius: Theme.radiusMd
                            visible: sourceRules.activeFocus
                        }

                    Group {
                        id: sourceRuleGroup

                        width: parent.width

                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: Theme.rowHeader
                            topLeftRadius: Theme.radiusMd - 1
                            topRightRadius: Theme.radiusMd - 1
                            color: Theme.surfaceSubtle
                            Accessible.ignored: true

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.space4
                                anchors.rightMargin: Theme.space4
                                spacing: Theme.space3

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("匹配")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: 140
                                    text: qsTr("目标")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }
                            }
                        }

                        Repeater {
                            model: page.routing.profileRules

                            Item {
                                id: sourceRule

                                required property string match
                                required property string target

                                Layout.fillWidth: true
                                implicitHeight: 36
                                Accessible.role: Accessible.Row
                                Accessible.name: qsTr("%1，%2").arg(match).arg(target)

                                Rectangle {
                                    width: parent.width
                                    height: 1
                                    color: Theme.divider
                                }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: Theme.space4
                                    anchors.rightMargin: Theme.space4
                                    spacing: Theme.space3

                                    UiText {
                                        Layout.fillWidth: true
                                        text: sourceRule.match
                                        role: Theme.type.dataSm
                                        color: Theme.textSecondary
                                        elide: Text.ElideRight
                                    }

                                    UiText {
                                        Layout.preferredWidth: 140
                                        text: sourceRule.target
                                        role: Theme.type.bodySm
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }

                        GroupRow {
                            visible: page.routing.profileRulesEmpty
                            rowHeight: Theme.rowSingle

                            AccessibleText {
                                Layout.fillWidth: true
                                text: qsTr("没有匹配的规则")
                                role: Theme.type.bodySm
                                color: Theme.textMuted
                            }
                        }
                    }
                    }
                }
            }
        }

        // -- 我的方案 ----------------------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            Layout.bottomMargin: Theme.pageBottom
            visible: page.planMode
            spacing: Theme.space5

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space3

                UiText {
                    text: qsTr("方案")
                    role: Theme.type.bodySm
                    color: Theme.textSecondary
                }

                // The plan dropdown: the plans, then what to do with them.
                AppSelect {
                    id: planButton

                    Layout.preferredWidth: 180
                    current: page.routing.planId
                    displayText: page.routing.planName
                    accessibleName: qsTr("方案")
                    enabled: !page.routing.busy
                    menu: planMenu
                }

                SegmentedControl {
                    options: [
                        {
                            "value": "rules",
                            "label": qsTr("规则")
                        },
                        {
                            "value": "groups",
                            "label": qsTr("策略组")
                        }
                    ]
                    current: page.tab
                    accessibleName: qsTr("方案内容")
                    Accessible.role: Accessible.PageTabList
                    onPicked: value => page.tab = value
                }

                Item {
                    Layout.fillWidth: true
                }

                IconButton {
                    id: planMore

                    iconName: "more"
                    accessibleName: qsTr("方案的更多操作")
                    onClicked: moreMenu.openBelow(planMore)
                }

                AppButton {
                    id: addButton

                    visible: page.tab === "rules"
                    text: qsTr("添加规则")
                    variant: "primary"
                    enabled: !page.routing.busy && page.routing.planId !== ""
                    onClicked: {
                        page.dialogTrigger = addButton
                        page.dialogFocusReason = Focus.reasonAfter(addButton)
                        page.routing.editor.openAdd()
                    }
                }
            }

            // The pool has no nodes yet.
            Group {
                Layout.fillWidth: true
                visible: page.routing.poolEmpty

                GroupRow {
                    divider: false

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        AccessibleText {
                            Layout.fillWidth: true
                            text: qsTr("节点池里还没有节点")
                            role: Theme.type.labelMd
                        }

                        AccessibleText {
                            Layout.fillWidth: true
                            text: qsTr("规则照常保存；指向地区的规则暂走节点选择")
                            role: Theme.type.caption
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }

                    AppButton {
                        variant: "compact"
                        text: qsTr("导入订阅")
                        onClicked: page.shell.setPage("profiles")
                    }
                }
            }

            // The rules, then other traffic.
            Group {
                id: ruleList

                Layout.fillWidth: true
                visible: page.tab === "rules"
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

                            model: page.routing.rules
                            delegate: RuleRow {}
                            onItemRemoved: (index, item) => page.ruleRemoved(index, item as RuleRow)
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

                // Other traffic: always last; only its target changes. Laid out as the rules
                // are, without their handle, box and menu.
                Item {
                    Layout.fillWidth: true
                    implicitHeight: Theme.rowDouble

                    Rectangle {
                        visible: ruleRepeater.count > 0
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
                            Layout.preferredWidth: page.targetWidth
                            Layout.fillWidth: false
                            spacing: 2

                            AppSelect {
                                Layout.fillWidth: true
                                options: page.routing.targetOptions
                                current: page.routing.fallbackTarget
                                displayText: page.routing.fallbackLabel
                                accessibleName: qsTr("其他流量的目标")
                                onPicked: value => page.routing.setFallback(value)
                            }

                            AccessibleText {
                                Layout.fillWidth: true
                                visible: text !== ""
                                text: page.routing.fallbackWarning
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
            }

            // The groups: built in, and one per region with nodes.
            Repeater {
                model: [
                    {
                        "title": qsTr("内置"),
                        "hint": "",
                        "rows": page.routing.builtinGroups,
                        "empty": ""
                    },
                    {
                        "title": qsTr("地区"),
                        "hint": qsTr("按节点名称自动生成"),
                        "rows": page.routing.regionGroups,
                        "empty": qsTr("节点池为空，没有地区组")
                    }
                ]

                ColumnLayout {
                    id: groupSection

                    required property var modelData

                    Layout.fillWidth: true
                    visible: page.tab === "groups"
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

                                divider: index > 0

                                ColumnLayout {
                                    Layout.fillWidth: true
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

                                IconButton {
                                    id: groupMore

                                    iconName: "more"
                                    accessibleName: qsTr("%1 的更多操作").arg(groupRow.name)
                                    onClicked: groupMenu.openFor(groupRow.groupId, groupRow.type, groupRow.hidden, groupMore)
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
        readonly property alias nameButton: nameButton
        readonly property alias moreButton: moreButton

        width: ruleColumn.width
        height: Theme.rowDouble
        opacity: rulesBox.dragged === ruleId ? Theme.disabledOpacity : 1

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

            // The handle: the mouse's grip only; the keyboard moves rules from the menu.
            Icon {
                id: grip

                name: "grip"
                size: 16

                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -6
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
                            page.routing.moveRule(ruleRow.ruleId, drop > ruleRow.index ? drop - 1 : drop)
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
                onToggled: page.routing.setRuleEnabled(ruleRow.ruleId, checked)
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
                    page.dialogTrigger = nameButton
                    page.dialogFocusReason = Focus.reasonAfter(nameButton)
                    page.routing.editor.openEdit(ruleRow.ruleId)
                }
                Keys.onUpPressed: page.rowAt(ruleRow.index - 1).nameButton.forceActiveFocus(Qt.TabFocusReason)
                Keys.onDownPressed: page.rowAt(ruleRow.index + 1).nameButton.forceActiveFocus(Qt.TabFocusReason)

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
                Layout.preferredWidth: page.targetWidth
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
        id: planMenu

        Instantiator {
            model: page.routing.planOptions
            onObjectAdded: (index, object) => planMenu.insertItem(index, object)
            onObjectRemoved: (index, object) => planMenu.removeItem(object)

            delegate: AppMenuItem {
                id: planChoice

                required property var modelData

                text: planChoice.modelData.label
                checkable: true
                checked: planChoice.modelData.value === page.routing.planId
                onTriggered: {
                    page.routing.selectPlan(planChoice.modelData.value)
                    checked = Qt.binding(() => planChoice.modelData.value === page.routing.planId)
                }
            }
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("新建…")
            onTriggered: {
                planDialog.renaming = false
                page.openDialog(planDialog, planMenu.trigger, planMenu.triggerFocusReason)
            }
        }

        AppMenuItem {
            text: qsTr("重命名…")
            onTriggered: {
                planDialog.renaming = true
                page.openDialog(planDialog, planMenu.trigger, planMenu.triggerFocusReason)
            }
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("删除")
            destructive: true
            enabled: page.routing.canRemovePlan
            onTriggered: page.routing.requestRemovePlan()
        }
    }

    AppMenu {
        id: moreMenu

        AppMenuItem {
            text: qsTr("节点池设置…")
            onTriggered: page.openDialog(infoDialog, moreMenu.trigger, moreMenu.triggerFocusReason)
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
                page.dialogTrigger = ruleMenu.trigger
                page.dialogFocusReason = ruleMenu.triggerFocusReason
                page.routing.editor.openEdit(ruleMenu.ruleId)
            }
        }

        AppMenuItem {
            text: qsTr("上移")
            enabled: !ruleMenu.first
            onTriggered: page.routing.moveRuleBy(ruleMenu.ruleId, -1)
        }

        AppMenuItem {
            text: qsTr("下移")
            enabled: !ruleMenu.last
            onTriggered: page.routing.moveRuleBy(ruleMenu.ruleId, 1)
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("删除")
            destructive: true
            onTriggered: {
                page.removingRule = ruleMenu.ruleId
                page.routing.removeRule(ruleMenu.ruleId)
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
            model: page.routing.groupTypeOptions
            onObjectAdded: (index, object) => groupMenu.insertItem(index, object)
            onObjectRemoved: (index, object) => groupMenu.removeItem(object)

            delegate: AppMenuItem {
                id: typeChoice

                required property var modelData

                text: typeChoice.modelData.label
                checkable: true
                checked: typeChoice.modelData.value === groupMenu.type
                onTriggered: {
                    page.routing.setGroupType(groupMenu.groupId, typeChoice.modelData.value)
                    checked = Qt.binding(() => typeChoice.modelData.value === groupMenu.type)
                }
            }
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: groupMenu.hidden ? qsTr("显示") : qsTr("隐藏")
            onTriggered: page.routing.setGroupHidden(groupMenu.groupId, !groupMenu.hidden)
        }
    }

    RuleDialog {
        id: ruleDialog

        editor: page.routing.editor
    }

    PlanDialog {
        id: planDialog

        routing: page.routing
    }

    InfoPatternDialog {
        id: infoDialog

        routing: page.routing
    }

    ConfirmDialog {
        id: removePlanDialog

        title: page.routing.planRemovalTitle
        message: page.routing.planRemovalText
        confirmText: qsTr("删除")
        destructive: true
        onAccepted: page.routing.confirmRemovePlan()
    }
}
