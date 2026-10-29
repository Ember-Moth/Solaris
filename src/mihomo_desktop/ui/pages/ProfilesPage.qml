pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import MihomoDesktop
import "../components"

ScrollView {
    id: page

    required property ProfilesViewModel profiles
    property bool importOpen: false

    readonly property int tableMinWidth: 760
    readonly property int usageWidth: 180
    readonly property int expiryWidth: 110
    readonly property int updatedWidth: 100
    readonly property int actionsWidth: 120

    function openImport() {
        importOpen = true
        urlField.forceActiveFocus()
    }

    function closeImport() {
        importOpen = false
        urlField.clear()
        nameField.clear()
        autoUpdate.checked = true
        page.profiles.clearImportError()
    }

    function submitImport() {
        if (urlField.text.trim() !== "" && !page.profiles.importing)
            page.profiles.importUrl(urlField.text, nameField.text, autoUpdate.checked)
    }

    contentWidth: availableWidth
    clip: true

    Connections {
        target: page.profiles

        function onImported() {
            if (page.importOpen)
                page.closeImport()
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
            title: "配置"
            detail: page.profiles.summary
            detailColor: page.profiles.hasError ? Theme.danger : Theme.textMuted

            AppButton {
                text: "导入文件"
                enabled: !page.profiles.importing
                onClicked: fileDialog.open()
            }

            // The page's one primary action; the form's own button takes over while it is open.
            AppButton {
                text: "从链接导入"
                variant: page.importOpen ? "secondary" : "primary"
                enabled: !page.importOpen
                onClicked: page.openImport()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            visible: page.importOpen
            spacing: Theme.space2

            SectionLabel {
                text: "从链接导入"
            }

            Group {
                Layout.fillWidth: true

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.space4
                    Layout.rightMargin: Theme.space4
                    Layout.topMargin: 14
                    Layout.bottomMargin: 14
                    spacing: Theme.space3

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.space3

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 320
                            spacing: 6

                            UiText {
                                text: "订阅链接"
                                role: Theme.type.caption
                                color: Theme.textTertiary
                            }

                            AppTextField {
                                id: urlField

                                Layout.fillWidth: true
                                placeholderText: "https://sub.example.com/…"
                                inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoAutoUppercase
                                enabled: !page.profiles.importing
                                Accessible.name: "订阅链接"
                                onAccepted: page.submitImport()
                                Keys.onEscapePressed: page.closeImport()
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 160
                            Layout.maximumWidth: 240
                            spacing: 6

                            UiText {
                                text: "名称"
                                role: Theme.type.caption
                                color: Theme.textTertiary
                            }

                            AppTextField {
                                id: nameField

                                Layout.fillWidth: true
                                placeholderText: "可选"
                                enabled: !page.profiles.importing
                                Accessible.name: "名称"
                                onAccepted: page.submitImport()
                                Keys.onEscapePressed: page.closeImport()
                            }
                        }

                        AppButton {
                            Layout.alignment: Qt.AlignBottom
                            text: "取消"
                            onClicked: page.closeImport()
                        }

                        AppButton {
                            Layout.alignment: Qt.AlignBottom
                            text: page.profiles.importing ? "正在导入…" : "导入"
                            variant: "primary"
                            enabled: urlField.text.trim() !== "" && !page.profiles.importing
                            onClicked: page.submitImport()
                        }
                    }

                    AppCheckBox {
                        id: autoUpdate

                        text: page.profiles.autoUpdateLabel
                        checked: true
                        enabled: !page.profiles.importing
                    }

                    UiText {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: page.profiles.importError
                        role: Theme.type.caption
                        color: Theme.danger
                        wrapMode: Text.Wrap
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            Layout.bottomMargin: Theme.pageBottom
            spacing: Theme.space2

            SectionLabel {
                text: "全部配置"
            }

            Group {
                Layout.fillWidth: true

                // Columns keep their widths; a narrow window scrolls the table inside its group.
                Flickable {
                    id: table

                    Layout.fillWidth: true
                    implicitHeight: rows.implicitHeight + (horizontalBar.visible ? horizontalBar.height : 0)
                    contentWidth: Math.max(width, page.tableMinWidth)
                    contentHeight: rows.implicitHeight
                    flickableDirection: Flickable.HorizontalFlick
                    boundsBehavior: Flickable.StopAtBounds
                    clip: true
                    Accessible.role: Accessible.List
                    Accessible.name: "全部配置"

                    ScrollBar.horizontal: ScrollBar {
                        id: horizontalBar

                        policy: table.contentWidth > table.width ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                    }

                    ColumnLayout {
                        id: rows

                        width: table.contentWidth
                        spacing: 0

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

                                Item {
                                    implicitWidth: 18
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    text: "名称"
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: page.usageWidth
                                    text: "用量"
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: page.expiryWidth
                                    text: "到期"
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: page.updatedWidth
                                    text: "更新"
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                Item {
                                    implicitWidth: page.actionsWidth
                                }
                            }
                        }

                        Repeater {
                            id: profileRepeater

                            model: page.profiles.profiles

                            AbstractButton {
                                id: profileRow

                                required property var modelData
                                required property int index

                                Layout.fillWidth: true
                                implicitHeight: Theme.rowDouble
                                leftPadding: Theme.space4
                                rightPadding: Theme.space4
                                Accessible.role: Accessible.RadioButton
                                Accessible.name: "使用 " + modelData.name
                                Accessible.checked: modelData.active
                                onClicked: page.profiles.use(modelData.id)

                                contentItem: RowLayout {
                                    spacing: Theme.space3

                                    RadioMark {
                                        checked: profileRow.modelData.active
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 0

                                        UiText {
                                            Layout.fillWidth: true
                                            text: profileRow.modelData.name
                                            role: profileRow.modelData.active ? Theme.type.bodyMdStrong : Theme.type.labelMd
                                            elide: Text.ElideRight
                                        }

                                        UiText {
                                            Layout.fillWidth: true
                                            text: profileRow.modelData.source
                                            role: Theme.type.caption
                                            color: Theme.textMuted
                                            elide: Text.ElideRight
                                        }
                                    }

                                    // Nested layouts fill by default; the data columns keep fixed widths.
                                    ColumnLayout {
                                        Layout.fillWidth: false
                                        Layout.preferredWidth: page.usageWidth
                                        spacing: 5

                                        UiText {
                                            text: profileRow.modelData.usage
                                            role: Theme.type.dataSm
                                            color: Theme.textSecondary
                                        }

                                        UsageBar {
                                            Layout.fillWidth: true
                                            visible: profileRow.modelData.hasQuota
                                            fraction: profileRow.modelData.usedFraction
                                        }
                                    }

                                    UiText {
                                        Layout.preferredWidth: page.expiryWidth
                                        text: profileRow.modelData.expiry
                                        role: Theme.type.bodySm
                                        color: profileRow.modelData.expired ? Theme.warning : Theme.textSecondary
                                        font.features: ({
                                                "tnum": 1
                                            })
                                    }

                                    UiText {
                                        Layout.preferredWidth: page.updatedWidth
                                        text: profileRow.modelData.updated
                                        role: Theme.type.bodySm
                                        color: profileRow.modelData.updateFailed ? Theme.warning : Theme.textMuted
                                        elide: Text.ElideRight
                                    }

                                    RowLayout {
                                        Layout.fillWidth: false
                                        Layout.preferredWidth: page.actionsWidth
                                        spacing: 6

                                        Item {
                                            Layout.fillWidth: true
                                        }

                                        AppButton {
                                            variant: "compact"
                                            text: profileRow.modelData.updating ? "更新中…" : profileRow.modelData.refreshLabel
                                            enabled: !profileRow.modelData.updating
                                            Accessible.name: text + " " + profileRow.modelData.name
                                            onClicked: page.profiles.updateProfile(profileRow.modelData.id)
                                        }

                                        IconButton {
                                            id: moreButton

                                            iconName: "more"
                                            accessibleName: profileRow.modelData.name + " 的更多操作"
                                            onClicked: profileMenu.openFor(profileRow.modelData, moreButton)
                                        }
                                    }
                                }

                                background: Rectangle {
                                    bottomLeftRadius: profileRow.index === profileRepeater.count - 1 ? Theme.radiusMd - 1 : 0
                                    bottomRightRadius: bottomLeftRadius
                                    color: profileRow.modelData.active ? Theme.surfaceSelected : profileRow.hovered ? Theme.surfaceSubtle : "transparent"

                                    Rectangle {
                                        width: parent.width
                                        height: 1
                                        color: Theme.divider
                                    }

                                    FocusRing {
                                        anchors.margins: 1
                                        targetRadius: 4
                                        visible: profileRow.visualFocus
                                    }
                                }
                            }
                        }

                        GroupRow {
                            visible: profileRepeater.count === 0
                            rowHeight: Theme.rowSingle

                            UiText {
                                Layout.fillWidth: true
                                text: "尚未导入配置"
                                role: Theme.type.bodySm
                                color: Theme.textMuted
                            }
                        }
                    }
                }
            }
        }
    }

    AppMenu {
        id: profileMenu

        property var profile: ({})

        function openFor(profile: var, anchor: Item) {
            this.profile = profile
            // Triggering toggles `checked` itself, so it is set here rather than bound.
            autoUpdateItem.checked = profile.autoUpdate
            popup(anchor, anchor.width - width, anchor.height + Theme.space1)
        }

        AppMenuItem {
            id: autoUpdateItem

            text: page.profiles.autoUpdateLabel
            checkable: true
            enabled: profileMenu.profile.isSubscription ?? false
            onTriggered: page.profiles.setAutoUpdate(profileMenu.profile.id, checked)
        }

        AppMenuItem {
            text: "复制订阅链接"
            enabled: profileMenu.profile.isSubscription ?? false
            onTriggered: page.profiles.copyLink(profileMenu.profile.id)
        }

        AppMenuItem {
            text: "删除"
            destructive: true
            onTriggered: page.profiles.removeProfile(profileMenu.profile.id)
        }
    }

    FileDialog {
        id: fileDialog

        title: "导入配置文件"
        nameFilters: ["YAML 配置 (*.yaml *.yml)", "所有文件 (*)"]
        onAccepted: page.profiles.importFile(selectedFile.toString())
    }
}
