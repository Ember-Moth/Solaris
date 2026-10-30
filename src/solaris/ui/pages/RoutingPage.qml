pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Solaris
import "../components"
import "routing"

// The routing page (docs/routing.md §4.3): the page switch 订阅自带 / 我的方案 in the title
// bar; under it, the rules of the profile in use, or the user's plan with its rules and its
// groups. The sections are in routing/; the dialogs that more than one of them opens, and
// the plan's own, are here.
ScrollView {
    id: page

    required property RoutingViewModel routing
    required property ShellViewModel shell
    property string tab: "rules"

    readonly property bool planMode: routing.mode === "plan"

    contentWidth: availableWidth
    clip: true

    ScrollToFocus {
        flickable: page.contentItem as Flickable
    }

    DialogOpener {
        id: dialogOpener
    }

    Connections {
        target: page.routing.editor

        function onOpened() {
            dialogOpener.show(ruleDialog)
        }

        function onSaved() {
            ruleDialog.close()
        }
    }

    Connections {
        target: page.routing.groupEditor

        function onOpened() {
            dialogOpener.show(groupDialog)
        }

        function onSaved() {
            groupDialog.close()
        }
    }

    Connections {
        target: page.routing.transfer

        function onConversionReady() {
            dialogOpener.show(convertDialog)
        }

        function onConverted() {
            convertDialog.close()
        }

        function onYamlReady() {
            dialogOpener.show(yamlDialog)
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
            Layout.topMargin: Theme.titlebarPageTop
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
        ProfileSection {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            Layout.bottomMargin: Theme.pageBottom
            visible: !page.planMode
            routing: page.routing
            shell: page.shell
            opener: dialogOpener
            view: page
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
                        dialogOpener.from(addButton, Focus.reasonAfter(addButton))
                        page.routing.editor.openAdd()
                    }
                }

                AppButton {
                    id: newGroupButton

                    visible: page.tab === "groups"
                    text: qsTr("新建策略组")
                    variant: "primary"
                    enabled: !page.routing.busy && page.routing.planId !== ""
                    onClicked: {
                        dialogOpener.from(newGroupButton, Focus.reasonAfter(newGroupButton))
                        page.routing.groupEditor.openAdd()
                    }
                }
            }

            AccessibleText {
                Layout.fillWidth: true
                visible: text !== ""
                text: page.routing.transfer.error
                role: Theme.type.caption
                color: Theme.danger
                wrapMode: Text.Wrap
            }

            RuleTools {
                Layout.fillWidth: true
                visible: page.tab === "rules"
                routing: page.routing
            }

            // The pool has no nodes yet; under either tab.
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

            RuleList {
                Layout.fillWidth: true
                visible: page.tab === "rules"
                routing: page.routing
                opener: dialogOpener
                emptyFocus: addButton
            }

            GroupsSection {
                Layout.fillWidth: true
                visible: page.tab === "groups"
                routing: page.routing
                opener: dialogOpener
            }

            MeshSection {
                Layout.fillWidth: true
                visible: page.tab === "groups" && page.routing.meshNetworks.available
                routing: page.routing
                opener: dialogOpener
                onRulesRequested: page.tab = "rules"
            }

            ExitsSection {
                Layout.fillWidth: true
                visible: page.tab === "groups"
                routing: page.routing
                opener: dialogOpener
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
                planDialog.copying = false
                dialogOpener.open(planDialog, planMenu.trigger, planMenu.triggerFocusReason)
            }
        }

        AppMenuItem {
            text: qsTr("复制…")
            onTriggered: {
                planDialog.renaming = false
                planDialog.copying = true
                dialogOpener.open(planDialog, planMenu.trigger, planMenu.triggerFocusReason)
            }
        }

        AppMenuItem {
            text: qsTr("重命名…")
            onTriggered: {
                planDialog.renaming = true
                planDialog.copying = false
                dialogOpener.open(planDialog, planMenu.trigger, planMenu.triggerFocusReason)
            }
        }

        // Importing and exporting a plan go through the core (ConvertToPlan, ExportPlan).
        AppMenuItem {
            text: qsTr("导入…")
            visible: page.routing.coreAvailable
            enabled: !page.routing.transfer.busy
            onTriggered: {
                dialogOpener.from(planMenu.trigger, planMenu.triggerFocusReason)
                importDialog.open()
            }
        }

        AppMenuItem {
            text: qsTr("导出…")
            visible: page.routing.coreAvailable
            enabled: !page.routing.transfer.busy
            onTriggered: {
                dialogOpener.from(planMenu.trigger, planMenu.triggerFocusReason)
                page.routing.transfer.exportPlan()
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
            text: qsTr("查看生成的配置…")
            visible: page.routing.coreAvailable
            enabled: !page.routing.transfer.busy
            onTriggered: {
                dialogOpener.from(moreMenu.trigger, moreMenu.triggerFocusReason)
                page.routing.transfer.showGenerated()
            }
        }

        AppMenuItem {
            text: qsTr("节点池设置…")
            onTriggered: dialogOpener.open(infoDialog, moreMenu.trigger, moreMenu.triggerFocusReason)
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

    GroupDialog {
        id: groupDialog

        editor: page.routing.groupEditor
    }

    ConvertDialog {
        id: convertDialog

        transfer: page.routing.transfer
    }

    YamlDialog {
        id: yamlDialog

        transfer: page.routing.transfer
    }

    FileDialog {
        id: importDialog

        title: qsTr("导入方案")
        nameFilters: [qsTr("YAML 配置 (*.yaml *.yml)"), qsTr("所有文件 (*)")]
        onAccepted: page.routing.transfer.importFile(selectedFile.toString())
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
