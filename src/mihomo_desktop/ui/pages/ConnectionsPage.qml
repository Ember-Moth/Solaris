pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import MihomoDesktop
import "../components"

Item {
    id: page

    required property ConnectionsViewModel connections

    // Fixed columns hold their widest common values in English; host, rule and chain share
    // the rest. Below tableMinWidth the table scrolls sideways inside its container.
    readonly property int processWidth: 96
    readonly property int rateWidth: 96
    readonly property int trafficWidth: 88
    readonly property int durationWidth: 72
    readonly property int actionWidth: 28
    readonly property int fixedWidth: 2 * Theme.space4 + processWidth + rateWidth + trafficWidth + durationWidth + actionWidth + 7 * Theme.space3
    readonly property int tableMinWidth: fixedWidth + 180 + 144 + 126
    readonly property real flexWidth: table.contentWidth - fixedWidth
    readonly property real hostWidth: flexWidth * 2 / 5
    readonly property real ruleWidth: flexWidth * 1.6 / 5
    readonly property real chainWidth: flexWidth * 1.4 / 5
    // The search and the network filter share the header row while it has room for them
    // beside the title and status; otherwise they get a row of their own under it.
    readonly property bool filtersInHeader: header.width >= filters.implicitWidth + closeAllButton.implicitWidth + 280

    // Whether a rectangle in the table's content coordinates shows in its viewport. Qt's
    // software scene graph draws a Shape that lies wholly outside its clip anyway, so the
    // table's icons hide once they are scrolled out of it.
    function inView(x: real, y: real, w: real, h: real): bool {
        return x < table.contentX + table.width && x + w > table.contentX && y < table.contentY + table.height && y + h > table.contentY
    }

    function moveSelection(index: int) {
        page.connections.selectIndex(index)
        table.positionViewAtIndex(table.currentIndex, ListView.Contain)
    }

    function openDetail(row: Item, fromKeyboard: bool) {
        detail.toggle(row.hostCell)
        detail.returnFocusReason = fromKeyboard ? Qt.TabFocusReason : Qt.MouseFocusReason
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: Theme.pageTop
        anchors.leftMargin: Theme.pageX
        anchors.rightMargin: Theme.pageX
        anchors.bottomMargin: Theme.pageBottom
        spacing: Theme.space5

        PageHeader {
            id: header

            Layout.fillWidth: true
            title: qsTr("连接")
            detail: page.connections.summary
            detailColor: page.connections.hasError ? Theme.danger : Theme.textMuted

            Item {
                id: headerSlot

                visible: page.filtersInHeader
                implicitWidth: filters.implicitWidth
                implicitHeight: filters.implicitHeight
            }

            AppButton {
                id: closeAllButton

                text: qsTr("全部关闭")
                enabled: page.connections.running && page.connections.total > 0
                onClicked: closeAllDialog.show(closeAllButton)
            }
        }

        Item {
            id: rowSlot

            Layout.fillWidth: true
            visible: !page.filtersInHeader
            implicitHeight: filters.implicitHeight
        }

        RowLayout {
            id: filters

            parent: page.filtersInHeader ? headerSlot : rowSlot
            spacing: Theme.space2

            SearchField {
                Layout.preferredWidth: Math.max(220, placeholderWidth)
                placeholderText: qsTr("搜索主机、进程或规则")
                text: page.connections.query
                onTextEdited: page.connections.setQuery(text)
            }

            SegmentedControl {
                options: page.connections.networkOptions
                current: page.connections.network
                accessibleName: qsTr("网络类型")
                onPicked: value => page.connections.setNetwork(value)
            }
        }

        // The table: a group-style container whose list keeps the header in view.
        Control {
            id: tableBox

            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 1
            focusPolicy: Qt.StrongFocus
            Accessible.role: Accessible.Table
            Accessible.name: qsTr("活动连接")

            Keys.onPressed: event => {
                if (event.key === Qt.Key_Up)
                    page.moveSelection(table.currentIndex - 1)
                else if (event.key === Qt.Key_Down)
                    page.moveSelection(table.currentIndex + 1)
                else if (event.key === Qt.Key_PageUp)
                    page.moveSelection(table.currentIndex - Math.floor(table.height / Theme.rowDouble))
                else if (event.key === Qt.Key_PageDown)
                    page.moveSelection(table.currentIndex + Math.floor(table.height / Theme.rowDouble))
                else if (event.key === Qt.Key_Home)
                    page.moveSelection(0)
                else if (event.key === Qt.Key_End)
                    page.moveSelection(table.count - 1)
                else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) && table.currentItem)
                    page.openDetail(table.currentItem, true)
                else if ((event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace) && table.currentItem)
                    page.connections.closeConnection(table.currentItem.connectionId)
                else
                    return
                event.accepted = true
            }

            background: Rectangle {
                radius: Theme.radiusMd
                color: Theme.surface
                border.width: 1
                border.color: Theme.border

                FocusRing {
                    targetRadius: Theme.radiusMd
                    visible: tableBox.visualFocus
                }
            }

            contentItem: ListView {
                id: table

                clip: true
                model: page.connections.model
                currentIndex: page.connections.selectedIndex
                contentWidth: Math.max(width, page.tableMinWidth)
                flickableDirection: Flickable.AutoFlickIfNeeded
                boundsBehavior: Flickable.StopAtBounds
                headerPositioning: ListView.OverlayHeader
                highlightFollowsCurrentItem: false
                keyNavigationEnabled: false
                reuseItems: true

                ScrollBar.vertical: ScrollBar {}
                ScrollBar.horizontal: ScrollBar {
                    policy: table.contentWidth > table.width ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                }

                header: Rectangle {
                    z: 2
                    width: table.contentWidth
                    height: Theme.rowHeader
                    topLeftRadius: Theme.radiusMd - 1
                    topRightRadius: Theme.radiusMd - 1
                    color: Theme.surfaceSubtle

                    Row {
                        x: Theme.space4
                        height: parent.height
                        spacing: Theme.space3

                        HeaderLabel {
                            width: page.hostWidth
                            text: qsTr("主机")
                        }

                        HeaderLabel {
                            width: page.ruleWidth
                            text: qsTr("规则")
                        }

                        HeaderLabel {
                            width: page.chainWidth
                            text: qsTr("链路")
                        }

                        HeaderLabel {
                            width: page.processWidth
                            text: qsTr("进程")
                        }

                        SortHeader {
                            width: page.rateWidth
                            text: qsTr("速率")
                            alignment: Qt.AlignRight
                            sorted: page.connections.sortKey === "rate"
                            descending: page.connections.sortDescending
                            arrowShown: page.inView(Theme.space4 + x, table.contentY, width, height)
                            onClicked: page.connections.sortBy("rate")
                        }

                        SortHeader {
                            width: page.trafficWidth
                            text: qsTr("流量")
                            alignment: Qt.AlignRight
                            sorted: page.connections.sortKey === "traffic"
                            descending: page.connections.sortDescending
                            arrowShown: page.inView(Theme.space4 + x, table.contentY, width, height)
                            onClicked: page.connections.sortBy("traffic")
                        }

                        SortHeader {
                            width: page.durationWidth
                            text: qsTr("时长")
                            alignment: Qt.AlignRight
                            sorted: page.connections.sortKey === "duration"
                            descending: page.connections.sortDescending
                            arrowShown: page.inView(Theme.space4 + x, table.contentY, width, height)
                            onClicked: page.connections.sortBy("duration")
                        }
                    }
                }

                delegate: AbstractButton {
                    id: row

                    required property int index
                    required property string connectionId
                    required property string host
                    required property string protocol
                    required property string rule
                    required property string chain
                    required property string process
                    required property string downRate
                    required property string upRate
                    required property string download
                    required property string upload
                    required property string duration
                    readonly property alias hostCell: hostCell
                    readonly property bool selected: index === table.currentIndex

                    width: table.contentWidth
                    height: Theme.rowDouble
                    focusPolicy: Qt.NoFocus
                    Accessible.role: Accessible.Row
                    Accessible.name: qsTr("%1，%2").arg(host).arg(process || protocol)
                    Accessible.selected: selected
                    onClicked: {
                        tableBox.forceActiveFocus(Qt.MouseFocusReason)
                        page.connections.select(connectionId)
                        page.openDetail(row, false)
                    }

                    background: Rectangle {
                        color: row.selected ? Theme.surfaceSelected : row.hovered ? Theme.surfaceSubtle : Theme.surface

                        Rectangle {
                            width: parent.width
                            height: 1
                            color: Theme.divider
                        }
                    }

                    contentItem: Item {
                        Row {
                            x: Theme.space4
                            height: parent.height
                            spacing: Theme.space3

                            Column {
                                id: hostCell

                                width: page.hostWidth
                                anchors.verticalCenter: parent.verticalCenter

                                UiText {
                                    width: parent.width
                                    text: row.host
                                    role: row.selected ? Theme.type.bodyMdStrong : Theme.type.bodyMd
                                    elide: Text.ElideRight
                                }

                                UiText {
                                    width: parent.width
                                    text: row.protocol
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }
                            }

                            UiText {
                                width: page.ruleWidth
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.rule
                                role: Theme.type.dataSm
                                color: Theme.textSecondary
                                elide: Text.ElideRight
                            }

                            UiText {
                                width: page.chainWidth
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.chain
                                role: Theme.type.bodySm
                                elide: Text.ElideRight
                            }

                            UiText {
                                width: page.processWidth
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.process
                                role: Theme.type.bodySm
                                color: Theme.textSecondary
                                elide: Text.ElideRight
                            }

                            RatePair {
                                width: page.rateWidth
                                anchors.verticalCenter: parent.verticalCenter
                                down: row.downRate
                                up: row.upRate
                            }

                            RatePair {
                                width: page.trafficWidth
                                anchors.verticalCenter: parent.verticalCenter
                                down: row.download
                                up: row.upload
                            }

                            UiText {
                                width: page.durationWidth
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.duration
                                role: Theme.type.dataSm
                                color: Theme.textSecondary
                                horizontalAlignment: Text.AlignRight
                            }

                            IconButton {
                                id: closeButton

                                anchors.verticalCenter: parent.verticalCenter
                                opacity: page.inView(Theme.space4 + x, row.y + y, width, height) ? 1 : 0
                                iconName: "close"
                                focusPolicy: Qt.NoFocus
                                accessibleName: qsTr("关闭 %1 的连接").arg(row.host)
                                onClicked: page.connections.closeConnection(row.connectionId)
                            }
                        }
                    }
                }
            }

            UiText {
                parent: tableBox
                x: tableBox.leftPadding + Theme.space4
                y: tableBox.topPadding + Theme.rowHeader + Theme.space8
                visible: table.count === 0
                text: page.connections.emptyText
                role: Theme.type.bodySm
                color: Theme.textMuted
            }
        }
    }

    // A table-header cell that does not sort.
    component HeaderLabel: UiText {
        height: Theme.rowHeader
        role: Theme.type.caption
        color: Theme.textMuted
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    // Two right-aligned values: download over upload, each after its coloured arrow.
    component RatePair: Column {
        id: pair

        property string down
        property string up

        Repeater {
            model: [
                {
                    glyph: "↓",
                    color: Theme.primary,
                    value: pair.down
                },
                {
                    glyph: "↑",
                    color: Theme.chartUpload,
                    value: pair.up
                }
            ]

            Row {
                required property var modelData

                anchors.right: parent.right
                spacing: Theme.space1

                UiText {
                    text: parent.modelData.glyph
                    role: Theme.type.dataSm
                    color: parent.modelData.color
                }

                UiText {
                    text: parent.modelData.value
                    role: Theme.type.dataSm
                    color: Theme.textSecondary
                }
            }
        }
    }

    ConnectionDetail {
        id: detail

        connections: page.connections
        returnFocusTo: tableBox
    }

    ConfirmDialog {
        id: closeAllDialog

        title: qsTr("关闭全部连接？")
        message: qsTr("将断开 %n 个连接，进行中的传输会中断。", "", page.connections.total)
        confirmText: qsTr("全部关闭")
        destructive: true
        onAccepted: page.connections.closeAll()
    }
}
