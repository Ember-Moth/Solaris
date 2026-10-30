pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Solaris
import "../components"

ScrollView {
    id: page

    required property ProfilesViewModel profiles
    property bool importOpen: false
    property Item overrideTrigger  // gets focus back when the override editor closes
    property int overrideFocusReason: Qt.OtherFocusReason
    property string removingId  // the profile whose removal was confirmed

    readonly property int tableMinWidth: 680
    readonly property int usageWidth: 170
    readonly property int expiryWidth: 110
    readonly property int updatedWidth: 110

    function openImport() {
        importOpen = true
        urlField.forceActiveFocus()
    }

    // Closing the form hands its focus back to the button that opened it.
    function closeImport() {
        const focused = Window.activeFocusItem
        const refocus = Focus.within(focused, importForm)
        importOpen = false
        urlField.clear()
        nameField.clear()
        autoUpdate.checked = true
        page.profiles.clearImportError()
        if (refocus)
            linkButton.forceActiveFocus(Focus.reasonAfter(focused))
    }

    function rowAt(index: int): ProfileRow {
        return profileRepeater.itemAt(Math.max(0, Math.min(index, profileRepeater.count - 1))) as ProfileRow
    }

    // A removed row takes the focus with it, so the focus moves to the row that takes its
    // place, or the one above when it was the last; with no rows left, to the import button.
    // While the confirmation is still closing, it hands its focus there instead.
    function rowRemoved(index: int, row: ProfileRow) {
        if (row.profileId !== removingId)
            return
        removingId = ""
        const next = profileRepeater.count > 0 ? rowAt(index).moreButton : linkButton
        if (removeDialog.visible)
            removeDialog.returnFocusTo = next
        else if (row.moreButton.activeFocus)
            next.forceActiveFocus(removeDialog.returnFocusReason)
    }

    function submitImport() {
        if (urlField.text.trim() !== "" && !page.profiles.importing)
            page.profiles.importUrl(urlField.text, nameField.text, autoUpdate.checked)
    }

    contentWidth: availableWidth
    clip: true

    ScrollToFocus {
        flickable: page.contentItem as Flickable
    }

    ScrollToFocus {
        flickable: table
        vertical: false
    }

    Connections {
        target: page.profiles

        function onImported() {
            if (page.importOpen)
                page.closeImport()
        }

        function onRemovalRequested() {
            removeDialog.showFor(profileMenu.trigger, profileMenu.triggerFocusReason)
        }

        function onOverrideOpened() {
            overrideDialog.showFor(page.overrideTrigger, page.overrideFocusReason)
        }

        function onOverrideSaved() {
            overrideDialog.close()
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
            title: qsTr("订阅")
            detail: page.profiles.summary
            detailColor: page.profiles.hasError ? Theme.danger : Theme.textMuted

            AppButton {
                text: qsTr("导入文件")
                enabled: !page.profiles.importing
                onClicked: fileDialog.open()
            }

            // The page's one primary action; the form's own button takes over while it is open.
            AppButton {
                id: linkButton

                text: qsTr("从链接导入")
                variant: page.importOpen ? "secondary" : "primary"
                enabled: !page.importOpen
                onClicked: page.openImport()
            }
        }

        ColumnLayout {
            id: importForm

            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            visible: page.importOpen
            spacing: Theme.space2

            SectionLabel {
                text: qsTr("从链接导入")
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
                                text: qsTr("订阅链接")
                                role: Theme.type.caption
                                color: Theme.textTertiary
                            }

                            AppTextField {
                                id: urlField

                                Layout.fillWidth: true
                                placeholderText: "https://sub.example.com/…"
                                inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoAutoUppercase
                                enabled: !page.profiles.importing
                                Accessible.name: qsTr("订阅链接")
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
                                text: qsTr("名称")
                                role: Theme.type.caption
                                color: Theme.textTertiary
                            }

                            AppTextField {
                                id: nameField

                                Layout.fillWidth: true
                                placeholderText: qsTr("可选")
                                enabled: !page.profiles.importing
                                Accessible.name: qsTr("名称")
                                onAccepted: page.submitImport()
                                Keys.onEscapePressed: page.closeImport()
                            }
                        }

                        AppButton {
                            Layout.alignment: Qt.AlignBottom
                            text: qsTr("取消")
                            onClicked: page.closeImport()
                        }

                        AppButton {
                            Layout.alignment: Qt.AlignBottom
                            text: page.profiles.importing ? qsTr("正在导入…") : qsTr("导入")
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

                    AccessibleText {
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
            spacing: Theme.space2

            SectionLabel {
                text: qsTr("全部订阅")
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
                    Accessible.name: qsTr("全部订阅")

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

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("名称")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: page.usageWidth
                                    text: qsTr("用量")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: page.expiryWidth
                                    text: qsTr("到期")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                UiText {
                                    Layout.preferredWidth: page.updatedWidth
                                    text: qsTr("更新")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }

                                Item {
                                    implicitWidth: 28 - Theme.space1
                                }
                            }
                        }

                        Repeater {
                            id: profileRepeater

                            model: page.profiles.model
                            delegate: ProfileRow {}
                            onItemRemoved: (index, item) => page.rowRemoved(index, item as ProfileRow)
                        }

                        GroupRow {
                            visible: profileRepeater.count === 0
                            rowHeight: Theme.rowSingle

                            AccessibleText {
                                Layout.fillWidth: true
                                text: qsTr("尚未导入订阅")
                                role: Theme.type.bodySm
                                color: Theme.textMuted
                            }
                        }
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
                text: qsTr("覆写")
            }

            Group {
                Layout.fillWidth: true

                GroupRow {
                    divider: false

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        UiText {
                            Layout.fillWidth: true
                            text: qsTr("全局覆写")
                            role: Theme.type.labelMd
                        }

                        AccessibleText {
                            Layout.fillWidth: true
                            text: page.profiles.globalOverrideDetail
                            role: Theme.type.caption
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }

                    // The global override is checked with the routing in use, so it needs
                    // something to route with.
                    AppButton {
                        id: globalOverrideButton

                        variant: "compact"
                        text: qsTr("编辑")
                        enabled: page.profiles.canEditGlobalOverride
                        Accessible.name: qsTr("编辑全局覆写")
                        onClicked: {
                            page.overrideTrigger = globalOverrideButton
                            page.overrideFocusReason = Focus.reasonAfter(globalOverrideButton)
                            page.profiles.editGlobalOverride()
                        }
                    }
                }
            }
        }
    }

    // One subscription: its name and where it comes from, with its state in the caption
    // (docs/routing.md §4.2); its menu holds what can be done with it. The menu button is the
    // row's Tab stop, and the arrows move between the rows' buttons.
    component ProfileRow: Item {
        id: profileRow

        required property int index
        required property string profileId
        required property string name
        required property string source
        required property string note
        required property string usage
        required property bool hasQuota
        required property real usedFraction
        required property string expiry
        required property bool expired
        required property string updated
        required property bool updateFailed
        required property bool updating
        required property string refreshLabel
        required property bool isSubscription
        required property bool autoUpdate
        required property bool inPool
        required property bool hasRules
        readonly property alias moreButton: moreButton

        Layout.fillWidth: true
        implicitHeight: Theme.rowDouble
        Accessible.role: Accessible.ListItem
        Accessible.name: name
        Accessible.description: qsTr("%1。用量 %2，到期 %3，更新 %4").arg(note ? qsTr("%1 · %2").arg(source).arg(note) : source).arg(usage).arg(expiry).arg(updated)

        Rectangle {
            anchors.fill: parent
            bottomLeftRadius: profileRow.index === profileRepeater.count - 1 ? Theme.radiusMd - 1 : 0
            bottomRightRadius: bottomLeftRadius
            color: hover.hovered ? Theme.surfaceSubtle : "transparent"

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.divider
            }
        }

        HoverHandler {
            id: hover
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.space4
            anchors.rightMargin: Theme.space3
            spacing: Theme.space3

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                UiText {
                    Layout.fillWidth: true
                    text: profileRow.name
                    role: Theme.type.labelMd
                    elide: Text.ElideRight
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    UiText {
                        Layout.preferredWidth: Math.ceil(implicitWidth)
                        Layout.maximumWidth: Math.ceil(implicitWidth)
                        Layout.fillWidth: true
                        text: profileRow.source
                        role: Theme.type.caption
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }

                    UiText {
                        visible: profileRow.note !== ""
                        text: qsTr(" · %1").arg(profileRow.note)
                        role: Theme.type.caption
                        color: Theme.textSecondary
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                    }
                }
            }

            // Nested layouts fill by default; the data columns keep fixed widths.
            ColumnLayout {
                Layout.fillWidth: false
                Layout.preferredWidth: page.usageWidth
                spacing: 5

                UiText {
                    text: profileRow.usage
                    role: Theme.type.dataSm
                    color: Theme.textSecondary
                }

                UsageBar {
                    Layout.fillWidth: true
                    visible: profileRow.hasQuota
                    fraction: profileRow.usedFraction
                }
            }

            UiText {
                Layout.preferredWidth: page.expiryWidth
                text: profileRow.expiry
                role: Theme.type.bodySm
                color: profileRow.expired ? Theme.warning : Theme.textSecondary
                font.features: ({
                        "tnum": 1
                    })
            }

            UiText {
                Layout.preferredWidth: page.updatedWidth
                text: profileRow.updated
                role: Theme.type.bodySm
                color: profileRow.updateFailed ? Theme.warning : Theme.textMuted
                elide: Text.ElideRight
            }

            IconButton {
                id: moreButton

                iconName: "more"
                accessibleName: qsTr("%1 的更多操作").arg(profileRow.name)
                onClicked: profileMenu.openFor(profileRow, moreButton)
                Keys.onUpPressed: page.rowAt(profileRow.index - 1).moreButton.forceActiveFocus(Qt.TabFocusReason)
                Keys.onDownPressed: page.rowAt(profileRow.index + 1).moreButton.forceActiveFocus(Qt.TabFocusReason)
            }
        }
    }

    AppMenu {
        id: profileMenu

        // What the items act on, copied when the menu opens: the row may go meanwhile.
        property string profileId
        property bool isSubscription
        property bool updating
        property string refreshLabel
        property bool inPool
        property bool hasRules

        function openFor(row: ProfileRow, anchor: Item) {
            profileId = row.profileId
            isSubscription = row.isSubscription
            updating = row.updating
            refreshLabel = row.refreshLabel
            inPool = row.inPool
            hasRules = row.hasRules
            // Triggering toggles `checked` itself, so it is set here rather than bound.
            autoUpdateItem.checked = row.autoUpdate
            openBelow(anchor)
        }

        AppMenuItem {
            text: profileMenu.refreshLabel
            enabled: !profileMenu.updating
            onTriggered: page.profiles.updateProfile(profileMenu.profileId)
        }

        AppMenuItem {
            id: autoUpdateItem

            text: page.profiles.autoUpdateLabel
            checkable: true
            enabled: profileMenu.isSubscription
            onTriggered: page.profiles.setAutoUpdate(profileMenu.profileId, checked)
        }

        AppMenuItem {
            text: qsTr("复制订阅链接")
            enabled: profileMenu.isSubscription
            onTriggered: page.profiles.copyLink(profileMenu.profileId)
        }

        AppMenuItem {
            text: qsTr("编辑覆写…")
            onTriggered: {
                page.overrideTrigger = profileMenu.trigger
                page.overrideFocusReason = profileMenu.triggerFocusReason
                page.profiles.editOverride(profileMenu.profileId)
            }
        }

        // A link item: it goes to the routing page (DESIGN.md "链接型菜单项").
        AppMenuItem {
            text: qsTr("使用它的规则")
            enabled: profileMenu.hasRules
            onTriggered: page.profiles.useRules(profileMenu.profileId)
        }

        AppMenuItem {
            text: profileMenu.inPool ? qsTr("不加入节点池") : qsTr("加入节点池")
            onTriggered: page.profiles.setInPool(profileMenu.profileId, !profileMenu.inPool)
        }

        AppMenuSeparator {}

        AppMenuItem {
            text: qsTr("删除")
            destructive: true
            onTriggered: page.profiles.requestRemove(profileMenu.profileId)
        }
    }

    ConfirmDialog {
        id: removeDialog

        title: page.profiles.removalTitle
        message: page.profiles.removalText
        confirmText: qsTr("删除")
        destructive: true
        onAccepted: {
            page.removingId = profileMenu.profileId
            page.profiles.confirmRemove()
        }
        onRejected: page.profiles.cancelRemove()
    }

    OverrideDialog {
        id: overrideDialog

        profiles: page.profiles
    }

    FileDialog {
        id: fileDialog

        title: qsTr("导入订阅文件")
        nameFilters: [qsTr("YAML 配置 (*.yaml *.yml)"), qsTr("所有文件 (*)")]
        onAccepted: page.profiles.importFile(selectedFile.toString())
    }
}
