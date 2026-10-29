import QtQuick
import QtQuick.Controls.Basic

// menu (DESIGN.md "Components → 菜单"): AppMenuItems on a white overlay with the overlay
// shadow. openBelow() places it under its trigger, right edges aligned, as a "more" menu;
// openDropdown() left-aligns it, as a dropdown's list. Either goes above the trigger when
// the window has no room below; when neither side has room it takes the roomier one and
// stays inside the window.
Menu {
    id: menu

    property Item trigger
    property int triggerFocusReason: Qt.OtherFocusReason
    property real minimumWidth: 0  // a dropdown's list is at least as wide as the dropdown

    function openBelow(anchor: Item) {
        openAt(anchor, anchor.width - width)
    }

    function openDropdown(anchor: Item) {
        openAt(anchor, 0)
    }

    // popup() makes the trigger the menu's parent, so the menu's own window goes with a
    // trigger that is destroyed, such as a removed row's button; the new trigger's counts.
    function openAt(anchor: Item, x: real) {
        trigger = anchor
        triggerFocusReason = Focus.reasonAfter(anchor)
        const overlay = anchor.Overlay.overlay
        const top = anchor.mapToItem(overlay, 0, 0).y
        const roomBelow = overlay.height - Theme.overlayMargin - (top + anchor.height + Theme.overlayGap)
        const roomAbove = top - Theme.overlayGap - Theme.overlayMargin
        const below = implicitHeight <= roomBelow || (implicitHeight > roomAbove && roomBelow >= roomAbove)
        popup(anchor, x, below ? anchor.height + Theme.overlayGap : -Theme.overlayGap - implicitHeight)
    }

    // At least menu-min-width, and as wide as the longest item: the ListView that holds the
    // items reports no width of its own.
    implicitWidth: {
        let widest = Math.max(implicitBackgroundWidth + leftInset + rightInset, minimumWidth)
        for (let i = 0; i < count; i++) {
            const item = itemAt(i)
            if (item)
                widest = Math.max(widest, item.implicitWidth + leftPadding + rightPadding)
        }
        return widest
    }
    margins: Theme.overlayMargin
    padding: Theme.space1

    // A focused menu hands focus back to the window as it closes, which returns it to a
    // trigger that had it, but without the reason that shows the focus ring. A dialog that
    // an item opened keeps its focus.
    onClosed: {
        if (trigger && trigger.activeFocus && triggerFocusReason !== Qt.OtherFocusReason) {
            trigger.focus = false
            trigger.forceActiveFocus(triggerFocusReason)
        }
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
        implicitWidth: Theme.menuMinWidth

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
