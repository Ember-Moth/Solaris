pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic

// 2–4 mutually exclusive options: segmented-track and segmented-item(-selected).
Rectangle {
    id: root

    property var options: []  // [{value, label}]
    property string current
    property string accessibleName
    signal picked(string value)

    implicitWidth: row.implicitWidth + 4
    implicitHeight: Theme.controlSm + 4
    radius: Theme.radiusSm
    color: Theme.controlTrack
    opacity: enabled ? 1 : Theme.disabledOpacity
    Accessible.role: Accessible.Grouping
    Accessible.name: accessibleName

    Row {
        id: row
        anchors.centerIn: parent

        Repeater {
            model: root.options

            delegate: AbstractButton {
                id: segment

                required property var modelData
                readonly property bool selected: modelData.value === root.current

                implicitHeight: Theme.controlSm
                leftPadding: Theme.space3
                rightPadding: Theme.space3
                Accessible.role: Accessible.RadioButton
                Accessible.name: modelData.label
                Accessible.checked: selected
                onClicked: {
                    if (!selected)
                        root.picked(modelData.value)
                }

                contentItem: UiText {
                    text: segment.modelData.label
                    role: segment.selected ? Theme.type.labelSmStrong : Theme.type.labelSm
                    color: segment.selected ? Theme.text : Theme.textTertiary
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Item {
                    Rectangle {
                        anchors.fill: parent
                        anchors.topMargin: 1
                        anchors.bottomMargin: -1
                        radius: Theme.radiusXs
                        color: Theme.shadowSegment
                        visible: segment.selected
                    }
                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radiusXs
                        color: segment.selected ? Theme.surface : "transparent"
                    }
                    FocusRing {
                        targetRadius: Theme.radiusXs
                        visible: segment.visualFocus
                    }
                }
            }
        }
    }
}
