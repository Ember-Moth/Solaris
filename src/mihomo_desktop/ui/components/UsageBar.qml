import QtQuick

// progress-track with progress-fill: used quota. The number is written beside it.
Rectangle {
    property real fraction

    implicitHeight: 4
    radius: 2
    color: Theme.divider
    Accessible.role: Accessible.ProgressBar
    Accessible.name: qsTr("已用流量")
    Accessible.description: qsTr("已用 %1%").arg(Math.round(Math.max(0, Math.min(1, fraction)) * 100))

    Rectangle {
        width: parent.width * Math.max(0, Math.min(1, parent.fraction))
        height: parent.height
        radius: 2
        color: Theme.textTertiary
    }
}
