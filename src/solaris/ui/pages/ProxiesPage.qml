pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../components"

ScrollView {
    id: page

    required property ProxiesViewModel proxies
    readonly property int sourceWidth: 140

    // The arrows move the focus between the rows of a list; Space selects the focused one.
    function focusRow(rows: Repeater, index: int) {
        rows.itemAt(Math.max(0, Math.min(index, rows.count - 1))).forceActiveFocus(Qt.TabFocusReason)
    }

    contentWidth: availableWidth
    clip: true

    ScrollToFocus {
        flickable: page.contentItem as Flickable
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: Theme.space5

        PageHeader {
            Layout.fillWidth: true
            Layout.topMargin: Theme.titlebarPageTop
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
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

        GridLayout {
            id: panes

            readonly property bool twoPanes: width >= 260 + Theme.space6 + 420

            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            Layout.bottomMargin: Theme.pageBottom
            columns: twoPanes ? 2 : 1
            columnSpacing: Theme.space6
            rowSpacing: Theme.space5

            ColumnLayout {
                Layout.preferredWidth: panes.twoPanes ? 260 : panes.width
                Layout.fillWidth: !panes.twoPanes
                Layout.alignment: Qt.AlignTop
                spacing: Theme.space2

                SectionLabel {
                    text: qsTr("代理组")
                }

                Group {
                    Layout.fillWidth: true

                    Repeater {
                        id: groupRepeater

                        model: page.proxies.groups

                        AbstractButton {
                            id: groupButton

                            required property int index
                            required property string name
                            required property string kind
                            required property string now
                            required property bool selected
                            required property bool header  // folds the region groups
                            required property bool region
                            required property bool expanded
                            readonly property bool last: index === groupRepeater.count - 1

                            Layout.fillWidth: true
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
                            Keys.onUpPressed: page.focusRow(groupRepeater, index - 1)
                            Keys.onDownPressed: page.focusRow(groupRepeater, index + 1)

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
                                topLeftRadius: groupButton.index === 0 ? Theme.radiusMd - 1 : 0
                                topRightRadius: topLeftRadius
                                bottomLeftRadius: groupButton.last ? Theme.radiusMd - 1 : 0
                                bottomRightRadius: bottomLeftRadius
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
                Layout.alignment: Qt.AlignTop
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

                Group {
                    Layout.fillWidth: true

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Theme.rowHeader
                        topLeftRadius: Theme.radiusMd - 1
                        topRightRadius: Theme.radiusMd - 1
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

                    Repeater {
                        id: nodeRepeater

                        model: page.proxies.nodes

                        AbstractButton {
                            id: nodeRow

                            required property int index
                            required property string name
                            required property string type
                            required property string delay
                            required property string level
                            required property bool selected
                            required property string source
                            readonly property bool selectable: page.proxies.currentSelectable

                            Layout.fillWidth: true
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
                            Keys.onUpPressed: page.focusRow(nodeRepeater, index - 1)
                            Keys.onDownPressed: page.focusRow(nodeRepeater, index + 1)

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
                                bottomLeftRadius: nodeRow.index === nodeRepeater.count - 1 ? Theme.radiusMd - 1 : 0
                                bottomRightRadius: bottomLeftRadius
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
}
