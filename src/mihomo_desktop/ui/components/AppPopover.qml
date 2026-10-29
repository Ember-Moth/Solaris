import QtQuick
import QtQuick.Controls.Basic

// popover (DESIGN.md "Components → 弹出层"): a little non-modal content in the menu's
// container, 12px inside. openBelow() puts it under its trigger, left edges aligned, or
// above the trigger when the window has no room below; when neither side has room it
// takes the roomier one and stays inside the window. Clicking outside, Esc or the
// trigger again (toggle()) closes it; focus then goes back to `returnFocusTo`, or to the
// trigger, with its focus ring when the popover was opened from the keyboard.
Popup {
    id: popover

    property Item trigger
    property Item returnFocusTo
    property int returnFocusReason: Qt.OtherFocusReason
    property real closedAt: 0

    function openBelow(anchor: Item) {
        trigger = anchor
        returnFocusReason = anchor.visualFocus ? Qt.TabFocusReason : Qt.OtherFocusReason
        const top = anchor.mapToItem(Overlay.overlay, 0, 0)
        const roomBelow = Overlay.overlay.height - Theme.overlayMargin - (top.y + anchor.height + Theme.overlayGap)
        const roomAbove = top.y - Theme.overlayGap - Theme.overlayMargin
        const below = implicitHeight <= roomBelow || (implicitHeight > roomAbove && roomBelow >= roomAbove)
        x = top.x
        y = below ? top.y + anchor.height + Theme.overlayGap : top.y - Theme.overlayGap - implicitHeight
        open()
    }

    // The press that reaches the trigger has already closed the popover by the time the
    // trigger's click arrives, so a click within that moment does not open it again.
    function toggle(anchor: Item) {
        if (visible && trigger === anchor)
            close()
        else if (visible || trigger !== anchor || Date.now() - closedAt > 300)
            openBelow(anchor)
    }

    parent: Overlay.overlay
    width: Math.min(Math.max(implicitWidth, Theme.popoverMinWidth), Theme.popoverMaxWidth)
    margins: Theme.overlayMargin
    padding: Theme.space3
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    // Runs before a press outside reaches what was pressed, so a click into another field
    // keeps the focus there. Focus left outside the popover stays where it is.
    onAboutToHide: {
        closedAt = Date.now()
        const target = returnFocusTo ?? trigger
        if (!target || !activeFocus)
            return
        target.focus = false
        target.forceActiveFocus(returnFocusReason)
    }

    enter: Transition {
        NumberAnimation {
            property: "opacity"
            from: 0
            to: 1
            duration: Motion.popupIn
            easing.type: Easing.OutCubic
        }
    }

    exit: Transition {
        NumberAnimation {
            property: "opacity"
            from: 1
            to: 0
            duration: Motion.popupOut
            easing.type: Easing.InCubic
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
}
