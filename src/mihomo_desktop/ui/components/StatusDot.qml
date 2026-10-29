import QtQuick

Rectangle {
    property bool running

    implicitWidth: 8
    implicitHeight: 8
    radius: 4
    color: running ? Theme.success : Theme.textMuted
    Accessible.ignored: true
}
