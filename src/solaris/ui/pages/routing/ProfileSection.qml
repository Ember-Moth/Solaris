pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../../components"

// 订阅自带 (docs/routing.md §4.3): the profile whose own rules route; while it runs, its
// groups and rules, read only.
ColumnLayout {
    id: section

    required property RoutingViewModel routing
    required property ShellViewModel shell
    required property DialogOpener opener
    required property ScrollView view  // the page, which the rule table scrolls
    readonly property ProfileRoutes profile: routing.profileRoutes

    // The read-only rule table scrolls the page with the arrows.
    function scrollBy(dy: real) {
        const flickable = section.view.contentItem as Flickable
        flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, flickable.contentY + dy))
    }

    spacing: Theme.space5

    RowLayout {
        Layout.fillWidth: true
        visible: section.profile.hasProfiles && section.profile.hasRules
        spacing: Theme.space3

        UiText {
            text: qsTr("使用的订阅")
            role: Theme.type.bodySm
            color: Theme.textSecondary
        }

        AppSelect {
            Layout.preferredWidth: 220
            options: section.profile.options
            current: section.profile.profileId
            accessibleName: qsTr("使用的订阅")
            enabled: !section.routing.busy
            onPicked: value => section.profile.use(value)
        }

        SearchField {
            Layout.preferredWidth: Math.max(180, placeholderWidth)
            implicitHeight: Theme.controlSm
            visible: section.profile.running
            placeholderText: qsTr("搜索规则")
            text: section.profile.query
            onTextEdited: section.profile.setQuery(text)
        }

        Item {
            Layout.fillWidth: true
        }

        // At the top: a profile's rules run to hundreds of lines (routing.md §4.3).
        AppButton {
            id: convertButton

            visible: section.profile.profileId !== "" && section.routing.coreAvailable
            text: qsTr("以此创建我的方案")
            enabled: !section.routing.busy && !section.routing.transfer.busy
            onClicked: {
                section.opener.from(convertButton, Focus.reasonAfter(convertButton))
                section.routing.transfer.convertProfile()
            }
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: text !== ""
        text: section.routing.transfer.error
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }

    // Every profile has nodes only.
    Group {
        Layout.fillWidth: true
        Layout.maximumWidth: 720
        visible: !section.profile.hasRules

        GroupRow {
            divider: false

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                AccessibleText {
                    Layout.fillWidth: true
                    text: section.profile.hasProfiles ? qsTr("订阅里没有规则") : qsTr("尚未导入订阅")
                    role: Theme.type.labelMd
                }

                AccessibleText {
                    Layout.fillWidth: true
                    text: section.profile.hasProfiles ? qsTr("这些订阅只有节点；改用我的方案后，它们的节点组成节点池") : qsTr("导入订阅后才能使用它的规则")
                    role: Theme.type.caption
                    color: Theme.textMuted
                    elide: Text.ElideRight
                }
            }

            AppButton {
                variant: "compact"
                text: section.profile.hasProfiles ? qsTr("改用我的方案") : qsTr("导入订阅")
                onClicked: {
                    if (section.profile.hasProfiles)
                        section.routing.setMode("plan")
                    else
                        section.shell.setPage("profiles")
                }
            }
        }
    }

    // The profile is not running: what the core read from it.
    Group {
        Layout.fillWidth: true
        Layout.maximumWidth: 720
        visible: section.profile.hasRules && !section.profile.running && section.profile.counts !== ""

        GroupRow {
            divider: false
            rowHeight: Theme.rowSingle

            AccessibleText {
                Layout.fillWidth: true
                text: section.profile.counts
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
        visible: section.profile.running
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
                    text: section.profile.groupCount
                    role: Theme.type.caption
                    color: Theme.textMuted
                }
            }

            Group {
                Layout.fillWidth: true

                Repeater {
                    model: section.profile.groups

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
                    text: section.profile.ruleCount
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
                Keys.onUpPressed: section.scrollBy(-36)
                Keys.onDownPressed: section.scrollBy(36)
                Keys.onPressed: event => {
                    if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
                        section.scrollBy((event.key === Qt.Key_PageUp ? -1 : 1) * section.view.height * 0.9)
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
                        model: section.profile.rules

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
                        visible: section.profile.rulesEmpty
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
