import QtQuick
import QtQuick.Controls

ApplicationWindow {
    width: 960
    height: 640
    visible: true
    title: Qt.application.name

    Label {
        anchors.centerIn: parent
        text: Qt.application.name + " " + Qt.application.version
    }
}
