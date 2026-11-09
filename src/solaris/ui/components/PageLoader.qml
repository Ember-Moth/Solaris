import QtQuick

// A page of the main window in its StackLayout, made while `active` (Main.qml) and destroyed
// when it is not. A plain item rather than a Loader: a Loader is a focus scope, and focus that
// Tab or Shift+Tab moves into a page through one arrives without its reason, which the pages
// read (ProxiesPage.enter). The page is made at once, as the window's own items are.
Item {
    id: holder

    property Component sourceComponent
    property bool active
    property Item item: null  // the page, while it is made

    function sync() {
        if (active && !item) {
            item = sourceComponent.createObject(holder) as Item
            item.anchors.fill = holder
        } else if (!active && item) {
            item.destroy()
            item = null
        }
    }

    implicitWidth: item ? item.implicitWidth : 0
    implicitHeight: item ? item.implicitHeight : 0
    onActiveChanged: sync()
    Component.onCompleted: sync()
}
