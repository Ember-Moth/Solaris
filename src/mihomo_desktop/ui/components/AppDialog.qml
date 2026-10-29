import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// dialog (DESIGN.md "Components → 对话框"): a modal box on the scrim with a one-line title,
// a body and a right-aligned button row in the platform's order (buttons are declared
// cancel first, as on macOS; Windows shows them mirrored).
//
// Esc rejects. Enter runs the focused button, or `defaultButton` when focus is elsewhere.
// Focus starts on `initialFocus` and goes back to `returnFocusTo` once the dialog closes.
Dialog {
    id: dialog

    property bool wide
    property Item defaultButton
    property Item initialFocus: defaultButton
    property Item returnFocusTo
    property int returnFocusReason: Qt.OtherFocusReason
    default property alias content: body.data
    property alias buttons: buttonRow.data

    // Opens the dialog; `from` gets focus back afterwards, with its focus ring if it had one.
    function show(from: Item) {
        returnFocusTo = from
        returnFocusReason = from && from.visualFocus ? Qt.TabFocusReason : Qt.OtherFocusReason
        open()
    }

    function activate(focused: Item) {
        if (focused && typeof focused.click === "function" && focused.enabled)
            focused.click()
        else if (defaultButton && defaultButton.enabled)
            defaultButton.click()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(wide ? Theme.dialogWidthWide : Theme.dialogWidth, parent.width - 2 * Theme.overlayMargin)
    height: Math.min(implicitHeight, parent.height - 2 * Theme.space10)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: Theme.space5
    header: null
    footer: null

    onAboutToShow: {
        if (initialFocus)
            initialFocus.forceActiveFocus()
    }
    // Qt may already have handed focus back to the window, but without the reason that
    // shows a focus ring, so focus is set again.
    onClosed: {
        if (!returnFocusTo)
            return
        returnFocusTo.focus = false
        returnFocusTo.forceActiveFocus(returnFocusReason)
    }

    contentItem: ColumnLayout {
        spacing: 0
        Keys.onReturnPressed: dialog.activate(Window.activeFocusItem)
        Keys.onEnterPressed: dialog.activate(Window.activeFocusItem)

        UiText {
            Layout.fillWidth: true
            text: dialog.title
            role: Theme.type.bodyMdStrong
            elide: Text.ElideRight
            Accessible.role: Accessible.Heading
        }

        ColumnLayout {
            id: body

            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: Theme.space2
            spacing: Theme.space2
        }

        RowLayout {
            id: buttonRow

            Layout.alignment: Qt.AlignRight
            Layout.topMargin: Theme.space5
            layoutDirection: Qt.platform.os === "windows" ? Qt.RightToLeft : Qt.LeftToRight
            spacing: Theme.space2
        }
    }

    background: Item {
        OverlayShadow {
            anchors.fill: parent
        }

        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusMd
            color: Theme.surface
            border.width: 1
            border.color: Theme.border
        }
    }

    // Qt shows and hides the dimmer by setting its opacity; this fades it with the dialog.
    // The animation reads the old value when it starts: 0 when showing, 1 when hiding.
    Overlay.modal: Rectangle {
        id: scrim

        color: Theme.scrim

        Behavior on opacity {
            NumberAnimation {
                duration: scrim.opacity < 0.5 ? Motion.dialogIn : Motion.dialogOut
                easing.type: scrim.opacity < 0.5 ? Easing.OutCubic : Easing.InCubic
            }
        }
    }

    enter: Transition {
        NumberAnimation {
            property: "opacity"
            from: 0
            to: 1
            duration: Motion.dialogIn
            easing.type: Easing.OutCubic
        }
        NumberAnimation {
            property: "scale"
            from: 0.98
            to: 1
            duration: Motion.dialogIn
            easing.type: Easing.OutCubic
        }
    }

    exit: Transition {
        NumberAnimation {
            property: "opacity"
            from: 1
            to: 0
            duration: Motion.dialogOut
            easing.type: Easing.InCubic
        }
    }
}
