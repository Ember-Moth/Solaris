pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts

// A labelled group of check boxes that wraps over lines: the regions, subscriptions or other
// members of a group. The model's rows carry itemId, name, note (a count, written after the
// name) and picked; `added` disables a row.
ColumnLayout {
    id: root

    property string label
    property alias model: repeater.model
    signal toggled(string id)

    spacing: 6
    Accessible.role: Accessible.Grouping
    Accessible.name: label

    UiText {
        text: root.label
        role: Theme.type.caption
        color: Theme.textTertiary
    }

    Flow {
        id: flow

        Layout.fillWidth: true
        spacing: Theme.space2

        Repeater {
            id: repeater

            AppCheckBox {
                id: box

                required property string itemId
                required property string name
                required property string note
                required property bool picked
                required property bool added

                // A long name takes a line at most and loses its middle, so the count stays.
                width: Math.min(Math.max(implicitWidth, 112), flow.width)
                implicitHeight: Theme.controlSm
                text: note !== "" ? name + "  " + note : name
                elide: Text.ElideMiddle
                checked: picked
                enabled: !added
                Accessible.name: name
                Accessible.description: note
                onToggled: root.toggled(itemId)
            }
        }
    }
}
