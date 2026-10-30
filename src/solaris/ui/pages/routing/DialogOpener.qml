import QtQuick
import "../../components"

// The control a dialog gives the focus back to, and the reason it gets it with. A control
// sets it as it asks a view model to open a dialog; the page shows the dialog on the view
// model's signal. The page and its sections share one.
QtObject {
    property Item trigger
    property int reason: Qt.OtherFocusReason

    function from(item: Item, focusReason: int) {
        trigger = item
        reason = focusReason
    }

    function show(dialog: AppDialog) {
        dialog.showFor(trigger, reason)
    }

    function open(dialog: AppDialog, item: Item, focusReason: int) {
        from(item, focusReason)
        show(dialog)
    }
}
