pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// The group editor (docs/routing.md §4.5; DESIGN.md "对话框", "可折叠区"): the conditions on
// the left and, beside them, the members the core computes from them. A window too narrow for
// two columns puts the preview under the conditions. The dialog stays open with the core's
// error when the change is refused.
AppDialog {
    id: dialog

    required property GroupEditor editor
    readonly property bool twoPanes: width >= 720
    readonly property var matchModes: editor.matchModeOptions

    function save() {
        if (editor.canSave)
            editor.save()
    }

    title: editor.title
    width: Math.min(Theme.dialogWidthEditor, parent.width - 2 * Theme.overlayMargin)
    defaultButton: saveButton
    initialFocus: nameField

    onAboutToShow: {
        nameField.text = editor.name
        namePattern.text = editor.namePattern
        excludePattern.text = editor.excludePattern
        manualSearch.text = ""
        urlField.text = editor.url
        intervalField.text = editor.interval
        toleranceField.text = editor.tolerance
        nameField.selectAll()
    }

    GridLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.topMargin: Theme.space2
        columns: dialog.twoPanes ? 2 : 1
        columnSpacing: Theme.space6
        rowSpacing: Theme.space5

        // -- the conditions ------------------------------------------------------------------
        ScrollView {
            id: form

            Layout.fillWidth: true
            Layout.preferredWidth: dialog.twoPanes ? 460 : -1
            Layout.maximumWidth: dialog.twoPanes ? 460 : Number.POSITIVE_INFINITY
            Layout.fillHeight: true
            Layout.preferredHeight: Math.min(conditions.implicitHeight, dialog.twoPanes ? 520 : 320)
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                id: conditions

                width: form.availableWidth
                spacing: Theme.space4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space3

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
                            onTextEdited: dialog.editor.setName(text)
                            onAccepted: dialog.save()
                        }
                    }

                    ColumnLayout {
                        Layout.preferredWidth: 140
                        spacing: 6

                        UiText {
                            text: qsTr("类型")
                            role: Theme.type.caption
                            color: Theme.textTertiary
                        }

                        AppSelect {
                            Layout.fillWidth: true
                            implicitHeight: Theme.controlMd
                            options: dialog.editor.typeOptions
                            current: dialog.editor.type
                            accessibleName: qsTr("类型")
                            onPicked: value => dialog.editor.setType(value)
                        }
                    }
                }

                CheckGrid {
                    Layout.fillWidth: true
                    visible: dialog.editor.hasRegions
                    label: qsTr("地区")
                    model: dialog.editor.regions
                    onToggled: id => dialog.editor.toggleRegion(id)
                }

                AccessibleText {
                    Layout.fillWidth: true
                    visible: !dialog.editor.hasRegions
                    text: qsTr("节点池为空，没有地区可选")
                    role: Theme.type.caption
                    color: Theme.textMuted
                }

                Disclosure {
                    Layout.fillWidth: true
                    text: dialog.editor.moreLabel
                    open: dialog.editor.moreOpen
                    onToggled: open => dialog.editor.setMoreOpen(open)

                    CheckGrid {
                        Layout.fillWidth: true
                        label: qsTr("来源订阅")
                        model: dialog.editor.sources
                        onToggled: id => dialog.editor.toggleSource(id)
                    }

                    MatchField {
                        id: namePattern

                        label: qsTr("名称包含")
                        mode: dialog.editor.nameMode
                        invalid: dialog.editor.nameInvalid
                        placeholderText: qsTr("关键字或正则，如 IPLC|专线")
                        onEdited: (mode, pattern) => dialog.editor.setNameMatch(mode, pattern)
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        UiText {
                            text: qsTr("手动挑选")
                            role: Theme.type.caption
                            color: Theme.textTertiary
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space3

                            AccessibleText {
                                Layout.fillWidth: true
                                text: dialog.editor.manualSummary
                                role: Theme.type.bodySm
                                color: Theme.textSecondary
                            }

                            AppButton {
                                variant: "compact"
                                text: dialog.editor.manualOpen ? qsTr("收起") : qsTr("挑选")
                                Accessible.name: dialog.editor.manualOpen ? qsTr("收起手动挑选") : qsTr("挑选节点")
                                onClicked: dialog.editor.setManualOpen(!dialog.editor.manualOpen)
                            }
                        }

                        SearchField {
                            id: manualSearch

                            Layout.fillWidth: true
                            visible: dialog.editor.manualOpen
                            placeholderText: qsTr("搜索节点")
                            onTextEdited: dialog.editor.setManualQuery(text)
                            Keys.onDownPressed: nodeList.forceActiveFocus(Qt.TabFocusReason)
                        }

                        PickList {
                            id: nodeList

                            Layout.fillWidth: true
                            implicitHeight: 160
                            visible: dialog.editor.manualOpen
                            model: dialog.editor.nodes
                            accessibleName: qsTr("手动挑选")
                            emptyText: dialog.editor.coreNote || qsTr("没有匹配的节点")
                            empty: dialog.editor.nodesEmpty
                            onToggled: id => dialog.editor.toggleNode(id)
                        }
                    }

                    CheckGrid {
                        Layout.fillWidth: true
                        label: qsTr("其他成员")
                        model: dialog.editor.others
                        onToggled: id => dialog.editor.toggleOther(id)
                    }

                    MatchField {
                        id: excludePattern

                        label: qsTr("排除")
                        mode: dialog.editor.excludeMode
                        invalid: dialog.editor.excludeInvalid
                        placeholderText: qsTr("名称包含的关键字或正则")
                        note: qsTr("满足任一条件即加入，再去掉排除的节点；手动挑选和其他成员不受排除影响")
                        onEdited: (mode, pattern) => dialog.editor.setExclude(mode, pattern)
                    }
                }

                Disclosure {
                    Layout.fillWidth: true
                    visible: dialog.editor.hasAdvanced
                    text: qsTr("高级")
                    open: dialog.editor.advancedOpen
                    onToggled: open => dialog.editor.setAdvancedOpen(open)

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        UiText {
                            text: qsTr("测速地址")
                            role: Theme.type.caption
                            color: Theme.textTertiary
                        }

                        AppTextField {
                            id: urlField

                            Layout.fillWidth: true
                            placeholderText: "https://www.gstatic.com/generate_204"
                            Accessible.name: qsTr("测速地址")
                            onTextEdited: dialog.editor.setOption("url", text)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space3

                        UiText {
                            text: qsTr("间隔")
                            role: Theme.type.bodySm
                            color: Theme.textSecondary
                        }

                        AppTextField {
                            id: intervalField

                            Layout.preferredWidth: 80
                            placeholderText: "300"
                            validator: IntValidator {
                                bottom: 0
                            }
                            Accessible.name: qsTr("间隔（秒）")
                            onTextEdited: dialog.editor.setOption("interval", text)
                        }

                        UiText {
                            text: qsTr("秒")
                            role: Theme.type.bodySm
                            color: Theme.textSecondary
                        }

                        UiText {
                            Layout.leftMargin: Theme.space3
                            visible: dialog.editor.type === "url-test"
                            text: qsTr("容差")
                            role: Theme.type.bodySm
                            color: Theme.textSecondary
                        }

                        AppTextField {
                            id: toleranceField

                            Layout.preferredWidth: 80
                            visible: dialog.editor.type === "url-test"
                            placeholderText: "50"
                            validator: IntValidator {
                                bottom: 0
                            }
                            Accessible.name: qsTr("容差（毫秒）")
                            onTextEdited: dialog.editor.setOption("tolerance", text)
                        }

                        UiText {
                            visible: dialog.editor.type === "url-test"
                            text: "ms"
                            role: Theme.type.bodySm
                            color: Theme.textSecondary
                        }

                        Item {
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: dialog.editor.type === "load-balance"
                        spacing: Theme.space3

                        UiText {
                            text: qsTr("策略")
                            role: Theme.type.bodySm
                            color: Theme.textSecondary
                        }

                        AppSelect {
                            Layout.preferredWidth: 200
                            options: dialog.editor.strategyOptions
                            current: dialog.editor.strategy
                            accessibleName: qsTr("负载均衡的策略")
                            onPicked: value => dialog.editor.setOption("strategy", value)
                        }
                    }
                }
            }
        }

        // -- the preview ---------------------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignTop
            Layout.minimumHeight: 200
            spacing: Theme.space2

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space2

                SectionLabel {
                    text: qsTr("预览")
                }

                AccessibleText {
                    Layout.fillWidth: true
                    text: dialog.editor.coreNote === "" ? dialog.editor.previewTitle : ""
                    role: Theme.type.caption
                    color: Theme.textMuted
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: Theme.border

                ListView {
                    id: previewList

                    anchors.fill: parent
                    anchors.margins: 1
                    visible: dialog.editor.coreNote === ""
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: dialog.editor.preview
                    Accessible.role: Accessible.List
                    Accessible.name: qsTr("预览")

                    ScrollBar.vertical: ScrollBar {}

                    delegate: Item {
                        id: member

                        required property int index
                        required property string name
                        required property string source
                        required property string delay
                        required property string level
                        required property bool other

                        width: ListView.view.width
                        height: Theme.controlSm
                        Accessible.role: Accessible.ListItem
                        Accessible.name: [name, source, delay].filter(part => part !== "").join("，")

                        Rectangle {
                            visible: member.index > 0
                            width: parent.width
                            height: 1
                            color: Theme.divider
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: Theme.space2

                            UiText {
                                Layout.fillWidth: true
                                text: member.name
                                role: member.other ? Theme.type.labelSm : Theme.type.bodySm
                                elide: Text.ElideRight
                            }

                            UiText {
                                Layout.maximumWidth: 120
                                visible: member.source !== ""
                                text: member.source
                                role: Theme.type.caption
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }

                            UiText {
                                Layout.preferredWidth: 52
                                visible: !member.other
                                text: member.delay
                                role: Theme.type.dataSm
                                color: Status.latencyColor(member.level)
                                horizontalAlignment: Text.AlignRight
                            }
                        }
                    }
                }

                UiText {
                    visible: dialog.editor.coreNote !== "" || dialog.editor.previewEmpty || dialog.editor.previewError !== ""
                    x: 10
                    y: 10
                    width: parent.width - 20
                    text: dialog.editor.coreNote || (dialog.editor.previewError !== "" ? dialog.editor.previewError : qsTr("没有匹配的节点"))
                    role: Theme.type.bodySm
                    color: dialog.editor.previewError !== "" && dialog.editor.coreNote === "" ? Theme.danger : Theme.textMuted
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: text !== ""
        text: dialog.editor.error
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
            id: saveButton

            text: dialog.editor.confirmLabel
            variant: "primary"
            enabled: dialog.editor.canSave
            onClicked: dialog.save()
        }
    ]

    // A name condition: keyword or regular expression, and the pattern. A pattern that does
    // not compile turns the field to its error state.
    component MatchField: ColumnLayout {
        id: match

        property string label
        property string mode
        property bool invalid
        property string note
        property alias text: field.text
        property alias placeholderText: field.placeholderText
        signal edited(string mode, string pattern)

        Layout.fillWidth: true
        spacing: 6

        UiText {
            text: match.label
            role: Theme.type.caption
            color: Theme.textTertiary
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space2

            AppSelect {
                Layout.preferredWidth: 88
                implicitHeight: Theme.controlMd
                options: dialog.matchModes
                current: match.mode
                accessibleName: qsTr("%1的写法").arg(match.label)
                onPicked: value => match.edited(value, field.text)
            }

            AppTextField {
                id: field

                Layout.fillWidth: true
                error: match.invalid
                Accessible.name: match.label
                Accessible.description: match.note
                onTextEdited: match.edited(match.mode, text)
            }
        }

        AccessibleText {
            Layout.fillWidth: true
            visible: match.invalid
            text: qsTr("正则无效")
            role: Theme.type.caption
            color: Theme.danger
        }

        AccessibleText {
            Layout.fillWidth: true
            visible: match.note !== ""
            text: match.note
            role: Theme.type.caption
            color: Theme.textMuted
            wrapMode: Text.Wrap
        }
    }
}
