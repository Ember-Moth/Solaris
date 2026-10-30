pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// A searchable list's box (DESIGN.md "可搜索列表"): rows with a check box, a name and a
// note, in a bordered box that scrolls; with `sectioned`, category titles stick to the top.
// One Tab stop: the arrows move the highlight and Space checks the row. The model's rows
// carry itemId, name, note, picked and added (in use already, so disabled), and `category`
// when sectioned.
FocusScope {
    id: root

    property alias model: list.model
    property bool sectioned
    property bool mono
    property string accessibleName
    property string emptyText
    property bool empty
    signal toggled(string id)

    activeFocusOnTab: true
    Accessible.role: Accessible.List
    Accessible.name: accessibleName

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSm
        color: Theme.surface
        border.width: 1
        border.color: Theme.border

        FocusRing {
            targetRadius: Theme.radiusSm
            visible: root.activeFocus
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
        section.property: root.sectioned ? "category" : ""
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
        Keys.onSpacePressed: {
            const row = currentItem as PickRow
            if (row && !row.added)
                root.toggled(row.rowId)
        }

        ScrollBar.vertical: ScrollBar {}

        delegate: PickRow {}
    }

    UiText {
        visible: root.empty
        x: 10
        y: 10
        width: parent.width - 20
        text: root.emptyText
        role: Theme.type.bodySm
        color: Theme.textMuted
        wrapMode: Text.Wrap
    }

    component PickRow: AbstractButton {
        id: row

        required property int index
        required property string itemId
        required property string name
        required property string note
        required property bool picked
        required property bool added
        readonly property string rowId: itemId

        width: ListView.view.width
        height: Theme.controlSm
        leftPadding: 10
        rightPadding: 10
        focusPolicy: Qt.NoFocus
        enabled: !added
        checkable: false
        checked: picked
        opacity: added ? Theme.disabledOpacity : 1
        Accessible.role: Accessible.CheckBox
        Accessible.name: name
        Accessible.description: note
        Accessible.checkable: true
        onClicked: {
            list.currentIndex = index
            root.toggled(itemId)
        }

        contentItem: RowLayout {
            spacing: 10

            Rectangle {
                implicitWidth: 16
                implicitHeight: 16
                radius: Theme.radiusXs
                color: row.checked ? Theme.primary : Theme.surface
                border.width: row.checked ? 0 : 1.5
                border.color: Theme.borderControl

                Icon {
                    anchors.centerIn: parent
                    visible: row.checked
                    name: "check"
                    size: 12
                    color: Theme.textOnPrimary
                }
            }

            UiText {
                Layout.fillWidth: true
                text: row.name
                role: root.mono ? Theme.type.dataSm : Theme.type.bodySm
                elide: Text.ElideRight
            }

            UiText {
                visible: row.note !== ""
                text: row.note
                role: Theme.type.caption
                color: Theme.textMuted
            }
        }

        background: Rectangle {
            color: row.ListView.isCurrentItem && root.activeFocus ? Theme.surfaceSelected : row.hovered ? Theme.surfaceSubtle : Theme.surface

            Rectangle {
                visible: row.index > 0
                width: parent.width
                height: 1
                color: Theme.divider
            }
        }
    }
}
