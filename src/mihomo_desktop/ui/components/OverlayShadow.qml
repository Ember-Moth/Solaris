pragma ComponentBehavior: Bound
import QtQuick

// overlay-shadow (DESIGN.md "Elevation & Depth"): 0 4px 16px in shadow-overlay, under a
// menu, popover or dialog. A CSS blur of 16px is a Gaussian with sigma 8px; eight rings at
// its quantiles, each a share of the colour's alpha, approximate it without a shader effect.
Item {
    id: shadow

    property real radius: Theme.radiusMd

    readonly property real sigma: Theme.overlayShadowBlur / 2
    readonly property var quantiles: [-1.534, -0.887, -0.489, -0.157, 0.157, 0.489, 0.887, 1.534]
    readonly property color ring: Qt.rgba(Theme.shadowOverlay.r, Theme.shadowOverlay.g, Theme.shadowOverlay.b, 1 - Math.pow(1 - Theme.shadowOverlay.a, 1 / quantiles.length))

    Repeater {
        model: shadow.quantiles

        Rectangle {
            required property real modelData
            readonly property real spread: modelData * shadow.sigma

            x: -spread
            y: -spread + Theme.overlayShadowY
            width: shadow.width + 2 * spread
            height: shadow.height + 2 * spread
            radius: Math.max(shadow.radius + spread, 0)
            color: shadow.ring
        }
    }
}
