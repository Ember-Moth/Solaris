pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../components"

// The header stays put; the groups and the current group's nodes each scroll in their own
// box, which makes only the rows in sight: a group can hold hundreds of nodes.
Item {
    id: page

    required property ProxiesViewModel proxies
    readonly property int sourceWidth: 140
    // Tab stops at every row only where the platform tabs to buttons (DESIGN.md "焦点与键盘").
    readonly property bool tabsToRows: Application.styleHints.tabFocusBehavior === Qt.TabFocusAllControls
    readonly property string currentGroup: proxies.currentGroup
    readonly property string sort: proxies.sort

    // Another group's nodes, or the nodes in another order, show from the top.
    function showFirstNodes() {
        if (!nodeList.activeFocus)
            nodeList.currentIndex = -1
        nodeList.positionViewAtBeginning()
    }

    // The keys move by index, since a row out of sight may not be made yet: the list scrolls
    // to it first.
    function focusRow(list: ListView, index: int, reason: int) {
        const row = Math.max(0, Math.min(index, list.count - 1))
        list.positionViewAtIndex(row, ListView.Contain)
        list.itemAtIndex(row)?.forceActiveFocus(reason)
    }

    // Tab goes on to the next row, and past the last one to the controls after the list;
    // Shift+Tab likewise backwards. Whether it handled the key.
    function tabFrom(list: ListView, row: Item, index: int, forward: bool): bool {
        if (!tabsToRows)
            return false
        const reason = forward ? Qt.TabFocusReason : Qt.BacktabFocusReason
        const next = index + (forward ? 1 : -1)
        if (next >= 0 && next < list.count) {
            focusRow(list, next, reason)
            return true
        }
        let item = row.nextItemInFocusChain(forward)
        while (item && item !== row && Focus.within(item, list))
            item = item.nextItemInFocusChain(forward)
        item?.forceActiveFocus(reason)
        return true
    }

    // The list holds its rows in the order they were made, not in the rows' order, so Tab
    // from the controls before it, or Shift+Tab from those after it, reaches whichever row
    // comes first, or last, there. Focus that arrives there moves on to the first or last
    // row; focus given to a row on purpose stays.
    function enter(list: ListView) {
        const row = list.Window.activeFocusItem as AbstractButton
        if (!row)
            return
        let first = null
        let last = null
        for (const item of list.contentItem.children) {
            if (item.visible && item.activeFocusOnTab) {
                first = first ?? item
                last = item
            }
        }
        if (row.focusReason === Qt.TabFocusReason && row === first)
            focusRow(list, 0, Qt.TabFocusReason)
        else if (row.focusReason === Qt.BacktabFocusReason && row === last)
            focusRow(list, list.count - 1, Qt.BacktabFocusReason)
    }

    onCurrentGroupChanged: showFirstNodes()
    onSortChanged: showFirstNodes()

    ScrollToFocus {
        flickable: groupList
        horizontal: false
    }

    ScrollToFocus {
        flickable: nodeList
        horizontal: false
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: Theme.titlebarPageTop
        anchors.leftMargin: Theme.pageX
        anchors.rightMargin: Theme.pageX
        anchors.bottomMargin: Theme.pageBottom
        spacing: Theme.space5

        PageHeader {
            id: pageHeader

            Layout.fillWidth: true
            title: qsTr("代理")
            detail: page.proxies.summary

            SegmentedControl {
                options: page.proxies.sortOptions
                current: page.proxies.sort
                accessibleName: qsTr("节点排序")
                onPicked: value => page.proxies.setSort(value)
            }

            AppButton {
                text: page.proxies.testing ? qsTr("测速中…") : qsTr("测速")
                enabled: !page.proxies.testing && page.proxies.currentGroup !== ""
                onClicked: page.proxies.testCurrentGroup()
            }
        }

        // Side by side, both boxes reach down to the page's bottom margin. Stacked in a narrow
        // window, the groups take up to two fifths of the boxes' height and the nodes the rest.
        // The arrangement goes by the page's size and the rows' count, never by this layout's
        // own size or the list's measured height: those change while the layout arranges, and
        // a binding to them makes it arrange again inside itself (a recursive rearrange).
        GridLayout {
            id: panes

            readonly property real availableWidth: page.width - 2 * Theme.pageX
            readonly property real availableHeight: page.height - Theme.titlebarPageTop - Theme.pageBottom - pageHeader.implicitHeight - Theme.space5
            readonly property bool twoPanes: availableWidth >= 260 + Theme.space6 + 420
            readonly property real stackedGroupsHeight: (availableHeight - rowSpacing - 2 * (groupsLabel.implicitHeight + Theme.space2)) * 2 / 5
            // Every row at its taller height: the one row that folds the regions is shorter.
            readonly property real groupRowsHeight: groupList.count * Theme.rowDouble + 2

            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: twoPanes ? 2 : 1
            columnSpacing: Theme.space6
            rowSpacing: Theme.space5

            ColumnLayout {
                Layout.preferredWidth: panes.twoPanes ? 260 : -1
                Layout.fillWidth: !panes.twoPanes
                Layout.fillHeight: panes.twoPanes
                spacing: Theme.space2

                SectionLabel {
                    id: groupsLabel

                    text: qsTr("代理组")
                }

                ListBox {
                    Layout.fillWidth: true
                    Layout.fillHeight: panes.twoPanes
                    Layout.preferredHeight: panes.twoPanes ? -1 : Math.min(panes.groupRowsHeight, panes.stackedGroupsHeight)

                    ListView {
                        id: groupList

                        anchors.fill: parent
                        anchors.margins: 1
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        currentIndex: -1
                        keyNavigationEnabled: false
                        highlightFollowsCurrentItem: false
                        model: page.proxies.groups
                        onActiveFocusChanged: {
                            if (activeFocus)
                                page.enter(groupList)
                        }

                        ScrollBar.vertical: ScrollBar {}

                        delegate: AbstractButton {
                            id: groupButton

                            required property int index
                            required property string name
                            required property string kind
                            required property string now
                            required property bool selected
                            required property bool header  // folds the region groups
                            required property bool region
                            required property bool expanded

                            width: groupList.width
                            implicitHeight: header ? Theme.rowSingle : Theme.rowDouble
                            leftPadding: region ? 14 + Theme.space3 : 14
                            rightPadding: 14
                            topPadding: Theme.space2
                            bottomPadding: Theme.space2
                            checked: selected  // what screen readers hear; clicking does not toggle it
                            Accessible.role: header ? Accessible.Button : Accessible.RadioButton
                            Accessible.name: header ? qsTr("%1，%2").arg(name).arg(now) : name
                            Accessible.description: header ? (expanded ? qsTr("已展开") : qsTr("已收起")) : qsTr("%1，当前 %2").arg(kind).arg(now)
                            onClicked: {
                                if (header)
                                    page.proxies.toggleRegions()
                                else
                                    page.proxies.selectGroup(name)
                            }
                            // The list keeps its current row while it scrolls out of sight.
                            onActiveFocusChanged: {
                                if (activeFocus)
                                    groupList.currentIndex = index
                            }
                            Keys.onUpPressed: page.focusRow(groupList, index - 1, Qt.TabFocusReason)
                            Keys.onDownPressed: page.focusRow(groupList, index + 1, Qt.TabFocusReason)
                            Keys.onTabPressed: event => event.accepted = page.tabFrom(groupList, groupButton, index, !(event.modifiers & Qt.ShiftModifier))
                            Keys.onBacktabPressed: event => event.accepted = page.tabFrom(groupList, groupButton, index, false)

                            contentItem: ColumnLayout {
                                spacing: 0

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    // The region section's disclosure (DESIGN.md "可折叠区").
                                    Icon {
                                        visible: groupButton.header
                                        name: "chevron"
                                        size: 14
                                        rotation: groupButton.expanded ? 90 : 0

                                        Behavior on rotation {
                                            NumberAnimation {
                                                duration: Motion.stateChange
                                                easing.type: Easing.OutCubic
                                            }
                                        }
                                    }

                                    UiText {
                                        Layout.fillWidth: true
                                        text: groupButton.name
                                        role: groupButton.selected ? Theme.type.bodyMdStrong : Theme.type.labelMd
                                        elide: Text.ElideRight
                                    }

                                    // The section's group count, or the group's type.
                                    UiText {
                                        text: groupButton.header ? groupButton.now : groupButton.kind
                                        role: Theme.type.caption
                                        color: Theme.textMuted
                                    }
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    visible: !groupButton.header
                                    text: groupButton.now
                                    role: Theme.type.caption
                                    color: Theme.textTertiary
                                    elide: Text.ElideRight
                                }
                            }

                            background: Rectangle {
                                color: groupButton.selected ? Theme.surfaceSelected : groupButton.hovered ? Theme.surfaceSubtle : "transparent"

                                Rectangle {
                                    visible: groupButton.index > 0
                                    width: parent.width
                                    height: 1
                                    color: Theme.divider
                                }

                                FocusRing {
                                    anchors.margins: 1
                                    targetRadius: 4
                                    visible: groupButton.visualFocus
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: Theme.space2

                RowLayout {
                    Layout.fillWidth: true

                    SectionLabel {
                        Layout.fillWidth: true
                        text: page.proxies.currentGroup
                    }

                    AccessibleText {
                        text: page.proxies.currentHint
                        role: Theme.type.caption
                        color: Theme.textMuted
                    }
                }

                ListBox {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    // The column titles stay at the top of the box over the nodes.
                    Rectangle {
                        id: nodeHeader

                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 1
                        height: Theme.rowHeader
                        color: Theme.surfaceSubtle
                        Accessible.ignored: true

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 14
                            anchors.rightMargin: 14
                            spacing: Theme.space3

                            Item {
                                implicitWidth: 16
                            }

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("名称")
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }

                            UiText {
                                Layout.preferredWidth: page.sourceWidth
                                visible: page.proxies.showSource
                                text: qsTr("来源")
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }

                            UiText {
                                Layout.preferredWidth: 120
                                text: qsTr("类型")
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }

                            UiText {
                                Layout.preferredWidth: 72
                                horizontalAlignment: Text.AlignRight
                                text: qsTr("延迟")
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }
                        }
                    }

                    ListView {
                        id: nodeList

                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: nodeHeader.bottom
                        anchors.bottom: parent.bottom
                        anchors.margins: 1
                        anchors.topMargin: 0
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        currentIndex: -1
                        keyNavigationEnabled: false
                        highlightFollowsCurrentItem: false
                        reuseItems: true
                        model: page.proxies.nodes
                        onActiveFocusChanged: {
                            if (activeFocus)
                                page.enter(nodeList)
                        }

                        ScrollBar.vertical: ScrollBar {}

                        delegate: AbstractButton {
                            id: nodeRow

                            required property int index
                            required property string name
                            required property string type
                            required property string delay
                            required property string level
                            required property bool selected
                            required property string source
                            readonly property bool selectable: page.proxies.currentSelectable

                            width: nodeList.width
                            implicitHeight: Theme.rowSingle
                            leftPadding: 14
                            rightPadding: 14
                            hoverEnabled: selectable
                            focusPolicy: selectable ? Qt.StrongFocus : Qt.NoFocus
                            checked: selected  // what screen readers hear; clicking does not toggle it
                            Accessible.role: selectable ? Accessible.RadioButton : Accessible.ListItem
                            Accessible.name: name
                            Accessible.description: source ? qsTr("%1，%2，延迟 %3").arg(source).arg(type).arg(delay) : qsTr("%1，延迟 %2").arg(type).arg(delay)
                            Accessible.selected: selected
                            onClicked: {
                                if (selectable)
                                    page.proxies.selectProxy(name)
                            }
                            // The list keeps its current row while it scrolls out of sight.
                            onActiveFocusChanged: {
                                if (activeFocus)
                                    nodeList.currentIndex = index
                            }
                            Keys.onUpPressed: page.focusRow(nodeList, index - 1, Qt.TabFocusReason)
                            Keys.onDownPressed: page.focusRow(nodeList, index + 1, Qt.TabFocusReason)
                            Keys.onTabPressed: event => event.accepted = page.tabFrom(nodeList, nodeRow, index, !(event.modifiers & Qt.ShiftModifier))
                            Keys.onBacktabPressed: event => event.accepted = page.tabFrom(nodeList, nodeRow, index, false)

                            contentItem: RowLayout {
                                spacing: Theme.space3

                                Item {
                                    implicitWidth: 16
                                    implicitHeight: 16

                                    Icon {
                                        visible: nodeRow.selected
                                        name: "check"
                                        size: 16
                                        color: Theme.primary
                                    }
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    text: nodeRow.name
                                    role: nodeRow.selected ? Theme.type.bodyMdStrong : Theme.type.bodyMd
                                    elide: Text.ElideRight
                                }

                                UiText {
                                    Layout.preferredWidth: page.sourceWidth
                                    visible: page.proxies.showSource
                                    text: nodeRow.source
                                    role: Theme.type.bodySm
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }

                                UiText {
                                    Layout.preferredWidth: 120
                                    text: nodeRow.type
                                    role: Theme.type.bodySm
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: 72
                                    horizontalAlignment: Text.AlignRight
                                    text: nodeRow.delay
                                    role: Theme.type.dataSm
                                    color: Status.latencyColor(nodeRow.level)
                                }
                            }

                            background: Rectangle {
                                color: nodeRow.selected ? Theme.surfaceSelected : nodeRow.hovered ? Theme.surfaceSubtle : "transparent"

                                Rectangle {
                                    width: parent.width
                                    height: 1
                                    color: Theme.divider
                                }

                                FocusRing {
                                    anchors.margins: 1
                                    targetRadius: 4
                                    visible: nodeRow.visualFocus
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // A group-style box (DESIGN.md "分组") whose rows scroll inside it. A list clips to a
    // rectangle, so the border is drawn over the rows, with the page's background past its
    // rounded corners: a row scrolled into a corner does not show beyond the curve.
    component ListBox: Rectangle {
        color: Theme.surface
        radius: Theme.radiusMd

        Rectangle {
            z: 1
            anchors.fill: parent
            anchors.margins: -Theme.space1
            radius: Theme.radiusMd + Theme.space1
            color: "transparent"
            border.width: Theme.space1 + 1
            border.color: Theme.background
        }

        Rectangle {
            z: 1
            anchors.fill: parent
            radius: Theme.radiusMd
            color: "transparent"
            border.width: 1
            border.color: Theme.border
        }
    }
}
