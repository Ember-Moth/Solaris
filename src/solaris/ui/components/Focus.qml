pragma Singleton
import QtQuick
import QtQuick.Templates as T

// Keyboard focus helpers for overlays and pages that hand focus on (DESIGN.md "Components →
// 浮层的键盘行为").
QtObject {
    // Whether `item` is `container` or lies inside it.
    function within(item: Item, container: Item): bool {
        for (let it = item; it; it = it.parent) {
            if (it === container)
                return true
        }
        return false
    }

    // The reason to give with focus that moves on from `item`: one that shows the focus ring
    // when `item` showed it, so focus the keyboard moved stays visible, and none otherwise.
    // A text field shows its ring whenever it has focus.
    function reasonAfter(item: Item): int {
        if ((item as T.Control)?.visualFocus || (item as TextInput)?.activeFocus)
            return Qt.TabFocusReason
        return Qt.OtherFocusReason
    }
}
