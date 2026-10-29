import QtQuick
import QtQuick.Layouts

// A page whose screen is not built yet; the prototype canvas shows its design.
Item {
    id: page

    property string title

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.pageX
        anchors.rightMargin: Theme.pageX
        anchors.topMargin: Theme.pageTop
        spacing: Theme.space5

        PageHeader {
            Layout.fillWidth: true
            title: page.title
        }

        UiText {
            text: qsTr("尚未实现")
            role: Theme.type.bodySm
            color: Theme.textMuted
        }

        Item {
            Layout.fillHeight: true
        }
    }
}
