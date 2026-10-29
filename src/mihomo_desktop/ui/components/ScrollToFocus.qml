import QtQuick

// Scrolls `flickable` so that the control with keyboard focus inside it shows, with room for
// its focus ring: Tab can move focus to a control the page has scrolled away. A control wider
// than the view shows its leading edge.
QtObject {
    id: scroller

    required property Flickable flickable
    property bool horizontal: true
    property bool vertical: true
    property real margin: Theme.space3

    // The offset that shows [start, start + size] in a view of `view` over [origin, content].
    function reveal(start: real, size: real, offset: real, view: real, origin: real, content: real): real {
        const end = Math.max(origin, origin + content - view)
        let next = offset
        if (start - margin < offset || size + 2 * margin > view)
            next = start - margin
        else if (start + size + margin > offset + view)
            next = start + size + margin - view
        return Math.min(Math.max(next, origin), end)
    }

    function follow(item: Item) {
        const view = flickable
        if (!item || !Focus.within(item, view.contentItem))
            return
        const area = item.mapToItem(view.contentItem, 0, 0, item.width, item.height)
        if (horizontal)
            view.contentX = reveal(area.x, area.width, view.contentX, view.width, view.originX, view.contentWidth)
        if (vertical)
            view.contentY = reveal(area.y, area.height, view.contentY, view.height, view.originY, view.contentHeight)
    }

    readonly property Connections focusChanges: Connections {
        target: scroller.flickable.Window.window

        function onActiveFocusItemChanged() {
            scroller.follow(scroller.flickable.Window.activeFocusItem)
        }
    }
}
