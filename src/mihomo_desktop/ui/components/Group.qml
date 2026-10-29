import QtQuick
import QtQuick.Layouts

// A white settings-style group; its children are GroupRows stacked with hairline dividers.
Rectangle {
    default property alias content: column.data

    implicitHeight: column.implicitHeight + 2
    implicitWidth: column.implicitWidth + 2
    color: Theme.surface
    radius: Theme.radiusMd
    border.width: 1
    border.color: Theme.border

    ColumnLayout {
        id: column
        x: 1
        y: 1
        width: parent.width - 2
        spacing: 0
    }
}
