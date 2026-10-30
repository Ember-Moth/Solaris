pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// The third-party licenses (docs/release.md §5): every component with its version and
// license, grouped by kind, in a list box like a searchable list's (DESIGN.md "可搜索列表")
// without check boxes. The full texts open in the system's text viewer.
AppDialog {
    id: dialog

    required property AboutViewModel about

    title: qsTr("开源许可")
    wide: true
    defaultButton: closeButton
    initialFocus: search

    onAboutToShow: {
        search.text = ""
        about.setComponentQuery("")
    }

    AccessibleText {
        Layout.fillWidth: true
        text: qsTr("Solaris 以 GPL-3.0-only 发布，使用了下列第三方组件。源代码见各版本的发布页。")
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    SearchField {
        id: search

        Layout.fillWidth: true
        placeholderText: qsTr("搜索组件或许可证")
        onTextEdited: dialog.about.setComponentQuery(text)
        Keys.onDownPressed: list.forceActiveFocus(Qt.TabFocusReason)
    }

    FocusScope {
        id: box

        Layout.fillWidth: true
        Layout.preferredHeight: Theme.editorMinHeight + 80
        activeFocusOnTab: true
        Accessible.role: Accessible.List
        Accessible.name: dialog.title

        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusSm
            color: Theme.surface
            border.width: 1
            border.color: Theme.border

            FocusRing {
                targetRadius: Theme.radiusSm
                visible: box.activeFocus
            }
        }

        ListView {
            id: list

            anchors.fill: parent
            anchors.margins: 1
            clip: true
            focus: true
            boundsBehavior: Flickable.StopAtBounds
            keyNavigationEnabled: true
            highlightMoveDuration: 0
            model: dialog.about.components
            section.property: "category"
            section.criteria: ViewSection.FullString
            section.labelPositioning: ViewSection.InlineLabels | ViewSection.CurrentLabelAtStart
            section.delegate: Rectangle {
                id: header

                required property string section

                width: ListView.view.width
                height: Theme.listHeaderHeight
                color: Theme.surfaceSubtle
                Accessible.role: Accessible.Heading
                Accessible.name: section

                UiText {
                    anchors.verticalCenter: parent.verticalCenter
                    x: 10
                    text: header.section
                    role: Theme.type.sectionLabel
                    color: Theme.textMuted
                }
            }

            ScrollBar.vertical: ScrollBar {}

            delegate: Rectangle {
                id: row

                required property int index
                required property string name
                required property string version
                required property string license

                width: ListView.view.width
                height: Theme.controlSm
                color: ListView.isCurrentItem && box.activeFocus ? Theme.surfaceSelected : Theme.surface
                Accessible.role: Accessible.ListItem
                Accessible.name: name
                Accessible.description: version + " · " + license

                Rectangle {
                    visible: row.index > 0
                    width: parent.width
                    height: 1
                    color: Theme.divider
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 10

                    UiText {
                        Layout.fillWidth: true
                        text: row.name
                        role: Theme.type.bodySm
                        elide: Text.ElideRight
                    }

                    UiText {
                        Layout.maximumWidth: 120
                        text: row.version
                        role: Theme.type.dataSm
                        color: Theme.textTertiary
                        elide: Text.ElideRight
                    }

                    UiText {
                        Layout.preferredWidth: 200
                        text: row.license
                        role: Theme.type.caption
                        color: Theme.textMuted
                        horizontalAlignment: Text.AlignRight
                        elide: Text.ElideRight
                    }
                }
            }
        }

        UiText {
            visible: dialog.about.componentsEmpty
            x: 10
            y: 10
            width: parent.width - 20
            text: qsTr("没有匹配的组件")
            role: Theme.type.bodySm
            color: Theme.textMuted
            wrapMode: Text.Wrap
        }
    }

    UiText {
        Layout.fillWidth: true
        text: dialog.about.componentsSummary
        role: Theme.type.caption
        color: Theme.textMuted
    }

    buttons: [
        AppButton {
            text: qsTr("打开全文")
            onClicked: dialog.about.openNotices()
        },
        AppButton {
            id: closeButton

            text: qsTr("关闭")
            variant: "primary"
            onClicked: dialog.close()
        }
    ]
}
