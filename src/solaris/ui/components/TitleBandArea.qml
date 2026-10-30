import QtQuick

// An empty part of the title band (DESIGN.md "顶部区域"): dragging it moves the window and a
// double-click zooms it. It lies under the band's controls, which keep their own clicks.
Item {
    id: area

    TapHandler {
        onDoubleTapped: TitleBand.doubleClicked(area.Window.window)
    }

    // Past the drag threshold the system moves the window, with its snapping.
    DragHandler {
        target: null
        onActiveChanged: {
            if (active)
                area.Window.window.startSystemMove()
        }
    }
}
