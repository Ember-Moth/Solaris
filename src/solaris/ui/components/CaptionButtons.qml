import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Shapes
import Solaris

// Windows' minimize, maximize and close buttons at the window's top right (DESIGN.md
// "顶部区域"). The system hit-tests the maximize button (desktop/titlebar.py), so hovering it
// opens Snap Layouts; its hover, press and clicks then come through the title bar.
Row {
    id: buttons

    required property TitleBar titleBar
    readonly property bool maximized: Window.window?.visibility === Window.Maximized

    component CaptionButton: AbstractButton {
        id: control

        // A 10 × 10 glyph of 1px lines.
        property string glyph
        property bool closes
        property bool systemHovered
        property bool systemDown
        readonly property bool lit: hovered || systemHovered
        readonly property bool held: down || systemDown

        width: Theme.captionButtonWidth
        height: Theme.titlebarHeight
        padding: 0
        // Tab reaches the buttons; a click leaves the focus where it was.
        focusPolicy: Qt.TabFocus
        Accessible.role: Accessible.Button
        Accessible.name: text

        contentItem: Item {
            Shape {
                anchors.centerIn: parent
                width: 10
                height: 10
                preferredRendererType: Shape.CurveRenderer

                ShapePath {
                    strokeColor: control.closes && (control.lit || control.held) ? "white" : Theme.text
                    strokeWidth: 1
                    fillColor: "transparent"

                    PathSvg {
                        path: control.glyph
                    }
                }
            }
        }

        background: Rectangle {
            color: {
                if (control.closes && (control.lit || control.held))
                    return Theme.captionClose
                if (control.held)
                    return Theme.captionPressed
                return control.lit ? Theme.captionHover : "transparent"
            }

            // Inside the button: the window's edge would cut a ring drawn outside.
            FocusRing {
                anchors.margins: 2
                radius: Theme.radiusXs
                visible: control.visualFocus
            }
        }
    }

    CaptionButton {
        text: qsTr("最小化")
        glyph: "M0 5.5h10"
        onClicked: buttons.Window.window.showMinimized()
    }

    CaptionButton {
        id: maximize

        text: buttons.maximized ? qsTr("还原") : qsTr("最大化")
        glyph: buttons.maximized
            ? "M2.5 2.5v-1a1 1 0 0 1 1-1h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-1M1.5 2.5h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-5a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1z"
            : "M1.5 0.5h7a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"
        systemHovered: buttons.titleBar.maximizeHovered
        systemDown: buttons.titleBar.maximizePressed
        onClicked: TitleBand.toggleMaximized(buttons.Window.window)
    }

    CaptionButton {
        text: qsTr("关闭")
        closes: true
        glyph: "M0.5 0.5l9 9M9.5 0.5l-9 9"
        onClicked: buttons.Window.window.close()
    }

    Binding {
        target: buttons.titleBar
        property: "maximizeArea"
        value: Qt.rect(buttons.x + maximize.x, buttons.y + maximize.y, maximize.width, maximize.height)
    }

    Connections {
        target: buttons.titleBar

        function onMaximizeClicked() {
            TitleBand.toggleMaximized(buttons.Window.window)
        }
    }
}
