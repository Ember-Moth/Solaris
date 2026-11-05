import QtQuick
import QtQuick.Controls.Basic

// tooltip (DESIGN.md "Components → 工具提示"): the whole of a text cut short with an ellipsis,
// above it after a moment's hover, in the menu's container. It wraps at popover-max-width,
// breaking a long host name anywhere, and stays inside the window.
ToolTip {
    id: tip

    x: Math.round((parent.width - width) / 2)
    y: -height - Theme.overlayGap
    width: Math.min(implicitWidth, Theme.popoverMaxWidth)
    margins: Theme.overlayMargin
    leftPadding: Theme.space2
    rightPadding: Theme.space2
    topPadding: Theme.space1
    bottomPadding: Theme.space1
    delay: 500
    closePolicy: Popup.NoAutoClose

    contentItem: Text {
        text: tip.text
        textFormat: Text.PlainText
        color: Theme.text
        font.family: Fonts.resolve(Theme.type.caption.fontFamily)
        font.pixelSize: Theme.type.caption.fontSize
        font.weight: Theme.type.caption.fontWeight
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        lineHeightMode: Text.FixedHeight
        lineHeight: Math.round(Theme.type.caption.fontSize * Theme.type.caption.lineHeight)
    }

    background: Item {
        OverlayShadow {
            anchors.fill: parent
            radius: Theme.radiusSm
        }

        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusSm
            color: Theme.surface
            border.width: 1
            border.color: Theme.border
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
}
