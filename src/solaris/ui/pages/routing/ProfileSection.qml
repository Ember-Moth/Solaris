pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../../components"

// 订阅自带 (docs/routing.md §4.3): the profile whose own rules route; while it runs, its
// groups and rules, read only.
ColumnLayout {
    id: profilePane

    required property RoutingViewModel routing
    required property ShellViewModel shell
    required property DialogOpener opener
    required property real availableHeight
    readonly property ProfileRoutes profile: routing.profileRoutes
    readonly property string query: profile.query
    readonly property string profileId: profile.profileId

    spacing: Theme.space5
    onQueryChanged: ruleList.positionViewAtBeginning()
    onProfileIdChanged: {
        groupList.positionViewAtBeginning()
        ruleList.positionViewAtBeginning()
    }

    RowLayout {
        id: toolbar

        Layout.fillWidth: true
        visible: profilePane.profile.hasProfiles && profilePane.profile.hasRules
        spacing: Theme.space3

        UiText {
            text: qsTr("使用的订阅")
            role: Theme.type.bodySm
            color: Theme.textSecondary
        }

        // Narrows to the select's least width before the row runs past the page, as it
        // would in English at the narrowest window.
        AppSelect {
            Layout.fillWidth: true
            Layout.preferredWidth: 220
            Layout.maximumWidth: 220
            Layout.minimumWidth: 120
            options: profilePane.profile.options
            current: profilePane.profile.profileId
            accessibleName: qsTr("使用的订阅")
            enabled: !profilePane.routing.busy
            onPicked: value => profilePane.profile.use(value)
        }

        SearchField {
            Layout.preferredWidth: Math.max(180, placeholderWidth)
            implicitHeight: Theme.controlSm
            visible: profilePane.profile.running
            placeholderText: qsTr("搜索规则")
            text: profilePane.profile.query
            onTextEdited: profilePane.profile.setQuery(text)
        }

        Item {
            Layout.fillWidth: true
        }

        // At the top: a profile's rules run to hundreds of lines (routing.md §4.3).
        AppButton {
            id: convertButton

            visible: profilePane.profile.profileId !== "" && profilePane.routing.coreAvailable
            text: qsTr("以此创建我的方案")
            enabled: !profilePane.routing.busy && !profilePane.routing.transfer.busy
            onClicked: {
                profilePane.opener.from(convertButton, Focus.reasonAfter(convertButton))
                profilePane.routing.transfer.convertProfile()
            }
        }
    }

    AccessibleText {
        id: transferError

        Layout.fillWidth: true
        visible: text !== ""
        text: profilePane.routing.transfer.error
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }

    // Every profile has nodes only.
    Group {
        Layout.fillWidth: true
        Layout.maximumWidth: 720
        visible: !profilePane.profile.hasRules

        GroupRow {
            divider: false

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                AccessibleText {
                    Layout.fillWidth: true
                    text: profilePane.profile.hasProfiles ? qsTr("订阅里没有规则") : qsTr("尚未导入订阅")
                    role: Theme.type.labelMd
                }

                AccessibleText {
                    Layout.fillWidth: true
                    text: profilePane.profile.hasProfiles ? qsTr("这些订阅只有节点；改用我的方案后，它们的节点组成节点池") : qsTr("导入订阅后才能使用它的规则")
                    role: Theme.type.caption
                    color: Theme.textMuted
                    elide: Text.ElideRight
                }
            }

            AppButton {
                variant: "compact"
                text: profilePane.profile.hasProfiles ? qsTr("改用我的方案") : qsTr("导入订阅")
                onClicked: {
                    if (profilePane.profile.hasProfiles)
                        profilePane.routing.setMode("plan")
                    else
                        profilePane.shell.setPage("profiles")
                }
            }
        }
    }

    // The profile is not running: what the core read from it.
    Group {
        Layout.fillWidth: true
        Layout.maximumWidth: 720
        visible: profilePane.profile.hasRules && !profilePane.profile.running && profilePane.profile.counts !== ""

        GroupRow {
            divider: false
            rowHeight: Theme.rowSingle

            AccessibleText {
                Layout.fillWidth: true
                text: profilePane.profile.counts
                role: Theme.type.bodySm
                color: Theme.textSecondary
                elide: Text.ElideRight
            }
        }
    }

    GridLayout {
        id: sourcePanes

        readonly property bool twoPanes: profilePane.width >= 260 + Theme.space6 + 440
        readonly property real availableHeight: profilePane.availableHeight - toolbar.implicitHeight - profilePane.spacing
                                                - (transferError.visible ? transferError.implicitHeight + profilePane.spacing : 0)
        readonly property real stackedGroupsHeight: (availableHeight - rowSpacing
                                                     - 2 * (groupsLabel.implicitHeight + Theme.space2)) * 2 / 5

        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: profilePane.profile.running
        columns: twoPanes ? 2 : 1
        columnSpacing: Theme.space6
        rowSpacing: Theme.space5

        ColumnLayout {
            Layout.preferredWidth: sourcePanes.twoPanes ? 260 : -1
            Layout.fillWidth: !sourcePanes.twoPanes
            Layout.fillHeight: sourcePanes.twoPanes
            spacing: Theme.space2

            RowLayout {
                Layout.fillWidth: true

                SectionLabel {
                    id: groupsLabel

                    Layout.fillWidth: true
                    text: qsTr("策略组")
                }

                UiText {
                    text: profilePane.profile.groupCount
                    role: Theme.type.caption
                    color: Theme.textMuted
                }
            }

            ScrollableListBox {
                Layout.fillWidth: true
                Layout.fillHeight: sourcePanes.twoPanes
                Layout.minimumHeight: Theme.rowDouble + 2
                Layout.preferredHeight: sourcePanes.twoPanes ? -1
                                       : Math.min(groupList.count * Theme.rowDouble + 2, sourcePanes.stackedGroupsHeight)
                activeFocusOnTab: groupList.count > 0
                Accessible.role: Accessible.List
                Accessible.name: qsTr("策略组")
                list: groupList
                rowHeight: Theme.rowDouble

                ListView {
                    id: groupList

                    anchors.fill: parent
                    anchors.margins: 1
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    currentIndex: -1
                    keyNavigationEnabled: false
                    highlightFollowsCurrentItem: false
                    reuseItems: true
                    model: profilePane.profile.groups
                    ScrollBar.vertical: ScrollBar {}

                    delegate: GroupRow {
                        id: sourceGroup

                        required property int index
                        required property string name
                        required property string caption

                        width: groupList.width
                        height: implicitHeight
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
            Layout.fillHeight: true
            spacing: Theme.space2

            RowLayout {
                Layout.fillWidth: true

                SectionLabel {
                    Layout.fillWidth: true
                    text: qsTr("规则")
                }

                UiText {
                    text: profilePane.profile.ruleCount
                    role: Theme.type.caption
                    color: Theme.textMuted
                }
            }

            ScrollableListBox {
                id: sourceRules

                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: Theme.rowHeader + Theme.rowSingle + 2
                activeFocusOnTab: true
                Accessible.role: Accessible.Table
                Accessible.name: qsTr("规则")
                list: ruleList
                rowHeight: 36

                Rectangle {
                    id: ruleHeader

                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 1
                    height: Theme.rowHeader
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

                ListView {
                    id: ruleList

                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: ruleHeader.bottom
                    anchors.bottom: parent.bottom
                    anchors.margins: 1
                    anchors.topMargin: 0
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    currentIndex: -1
                    keyNavigationEnabled: false
                    highlightFollowsCurrentItem: false
                    reuseItems: true
                    model: profilePane.profile.rules
                    ScrollBar.vertical: ScrollBar {}

                    delegate: Item {
                        id: sourceRule

                        required property string match
                        required property string target

                        width: ruleList.width
                        height: sourceRules.rowHeight
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
                    anchors.left: ruleList.left
                    anchors.right: ruleList.right
                    anchors.top: ruleList.top
                    height: implicitHeight
                    visible: profilePane.profile.rulesEmpty
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

    component ScrollableListBox: ListBox {
        id: box

        required property ListView list
        required property int rowHeight

        function scrollBy(dy: real) {
            const first = list.originY
            const last = Math.max(first, first + list.contentHeight - list.height)
            list.contentY = Math.max(first, Math.min(last, list.contentY + dy))
        }

        Accessible.focusable: activeFocusOnTab
        Keys.onUpPressed: scrollBy(-rowHeight)
        Keys.onDownPressed: scrollBy(rowHeight)
        Keys.onPressed: event => {
            if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
                scrollBy((event.key === Qt.Key_PageUp ? -1 : 1) * list.height * 0.9)
                event.accepted = true
            }
        }

        FocusRing {
            z: 2
            targetRadius: Theme.radiusMd
            visible: box.activeFocus
        }
    }
}
