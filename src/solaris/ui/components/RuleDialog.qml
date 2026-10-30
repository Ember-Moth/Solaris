pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// The add-rule dialog (docs/routing.md §4.4; DESIGN.md "条件编辑表单", "可搜索列表"): what to
// match, as services, GEO categories, domains or IP ranges, and the target. The dialog stays
// open with the core's error when the change is refused.
AppDialog {
    id: dialog

    required property RuleEditor editor
    readonly property string kind: editor.kind
    readonly property bool textKind: kind === "domain" || kind === "ip"

    function save() {
        if (editor.canSave)
            editor.save()
    }

    title: editor.title
    wide: true
    defaultButton: saveButton
    initialFocus: kind === "service" ? serviceSearch : kind === "category" ? categorySearch : textBox

    onAboutToShow: {
        serviceSearch.text = ""
        categorySearch.text = ""
        textBox.text = kind === "ip" ? editor.ipText : editor.domainText
    }
    // Each kind keeps its own text until the dialog closes.
    onKindChanged: {
        if (textKind)
            textBox.text = kind === "ip" ? editor.ipText : editor.domainText
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        spacing: Theme.space4

        SegmentedControl {
            options: dialog.editor.kindOptions
            current: dialog.kind
            accessibleName: qsTr("规则类型")
            onPicked: value => dialog.editor.setKind(value)
        }

        // -- services ----------------------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            visible: dialog.kind === "service"
            spacing: Theme.space2

            SearchField {
                id: serviceSearch

                Layout.fillWidth: true
                placeholderText: qsTr("搜索服务")
                onTextEdited: dialog.editor.setServiceQuery(text)
                Keys.onDownPressed: serviceList.forceActiveFocus(Qt.TabFocusReason)
            }

            PickList {
                id: serviceList

                Layout.fillWidth: true
                implicitHeight: 256
                model: dialog.editor.services
                sectioned: true
                accessibleName: qsTr("服务")
                emptyText: qsTr("没有匹配的服务，可以改用“分类”或“域名”")
                empty: dialog.editor.servicesEmpty
                onToggled: id => dialog.editor.toggleService(id)
            }

            AccessibleText {
                Layout.fillWidth: true
                text: dialog.editor.serviceSummary
                role: Theme.type.caption
                color: Theme.textMuted
                elide: Text.ElideRight
            }
        }

        // -- GEO categories ----------------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            visible: dialog.kind === "category"
            spacing: Theme.space2

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.space2

                SegmentedControl {
                    options: dialog.editor.geoOptions
                    current: dialog.editor.geo
                    accessibleName: qsTr("分类数据")
                    onPicked: value => {
                        categorySearch.text = ""
                        dialog.editor.setGeo(value)
                    }
                }

                SearchField {
                    id: categorySearch

                    Layout.fillWidth: true
                    placeholderText: qsTr("搜索分类")
                    onTextEdited: dialog.editor.setCategoryQuery(text)
                    Keys.onDownPressed: categoryList.forceActiveFocus(Qt.TabFocusReason)
                }
            }

            PickList {
                id: categoryList

                Layout.fillWidth: true
                implicitHeight: 224
                model: dialog.editor.categories
                mono: true
                accessibleName: qsTr("分类")
                emptyText: qsTr("没有匹配的分类")
                empty: dialog.editor.categoriesEmpty
                onToggled: id => dialog.editor.toggleCategory(id)
            }

            AccessibleText {
                Layout.fillWidth: true
                text: dialog.editor.categorySummary
                role: Theme.type.caption
                color: Theme.textMuted
                elide: Text.ElideRight
            }
        }

        // -- domains and IP ranges ---------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            visible: dialog.textKind
            spacing: 6

            CodeEditor {
                id: textBox

                Layout.fillWidth: true
                Layout.preferredHeight: 168
                implicitHeight: 168
                error: dialog.editor.textError !== ""
                placeholderText: dialog.kind === "ip" ? "10.0.0.0/8\n2001:db8::/32" : "example.com\n=www.example.org\n~example"
                Accessible.name: dialog.kind === "ip" ? qsTr("IP 段") : qsTr("域名")
                Accessible.description: dialog.editor.textSummary
                onTextChanged: dialog.editor.setText(text)
                onSubmitted: dialog.save()
            }

            AccessibleText {
                Layout.fillWidth: true
                text: dialog.editor.textSummary
                role: Theme.type.caption
                color: Theme.textMuted
                wrapMode: Text.Wrap
            }

            AccessibleText {
                Layout.fillWidth: true
                visible: text !== ""
                text: dialog.editor.textError
                role: Theme.type.caption
                color: Theme.danger
                elide: Text.ElideRight
            }
        }

        AppCheckBox {
            visible: dialog.kind === "ip"
            text: qsTr("不解析域名（no-resolve）")
            checked: dialog.editor.noResolve
            onToggled: dialog.editor.setNoResolve(checked)
        }

        // -- the target --------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space3

            UiText {
                text: qsTr("目标")
                role: Theme.type.bodySm
                color: Theme.textSecondary
            }

            AppSelect {
                Layout.preferredWidth: 200
                options: dialog.editor.targetOptions
                current: dialog.editor.target
                displayText: dialog.editor.targetLabel
                accessibleName: qsTr("目标")
                onPicked: value => dialog.editor.setTarget(value)
            }

            AccessibleText {
                Layout.fillWidth: true
                text: dialog.editor.targetHint
                role: Theme.type.caption
                color: Theme.textMuted
                elide: Text.ElideRight
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
}
