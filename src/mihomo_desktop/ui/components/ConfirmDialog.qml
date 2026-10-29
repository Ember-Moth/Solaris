import QtQuick
import QtQuick.Layouts

// A confirmation: the title asks, `message` states the consequences. The confirm button is
// the default; a destructive confirmation gives it danger text and makes Cancel the
// default instead, so Enter and the initial focus stay on the safe choice.
AppDialog {
    id: dialog

    property string message
    property string confirmText
    property bool destructive

    defaultButton: destructive ? cancelButton : confirmButton

    UiText {
        Layout.fillWidth: true
        text: dialog.message
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    buttons: [
        AppButton {
            id: cancelButton

            text: qsTr("取消")
            variant: dialog.destructive ? "primary" : "secondary"
            onClicked: dialog.reject()
        },
        AppButton {
            id: confirmButton

            text: dialog.confirmText
            variant: dialog.destructive ? "destructive" : "primary"
            onClicked: dialog.accept()
        }
    ]
}
