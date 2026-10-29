// GENERATED from DESIGN.md by scripts/gen_theme.py. Do not edit; edit DESIGN.md.

pragma Singleton
import QtQuick

QtObject {
    // ---- colors ----
    readonly property color primary: "#1F5EFF"  // primary
    readonly property color primaryHover: "#1B53E0"  // primary-hover
    readonly property color primaryPressed: "#1749CC"  // primary-pressed
    readonly property color textOnPrimary: "#FFFFFF"  // on-primary
    readonly property color link: "#1846C7"  // link
    readonly property color linkHover: "#123A9E"  // link-hover
    readonly property color background: "#F3F4F6"  // background
    readonly property color sidebar: "#E9EBEF"  // sidebar
    readonly property color sidebarHover: "#E0E3E8"  // sidebar-hover
    readonly property color surface: "#FFFFFF"  // surface
    readonly property color surfaceSubtle: "#F7F8FA"  // surface-subtle
    readonly property color surfaceSelected: "#ECEEF1"  // surface-selected
    readonly property color controlTrack: "#E3E5E9"  // control-track
    readonly property color divider: "#ECEEF1"  // divider
    readonly property color border: "#DCDFE4"  // border
    readonly property color borderButton: "#C9CDD4"  // border-button
    readonly property color borderControl: "#828893"  // border-control
    readonly property color text: "#15171C"  // text
    readonly property color textSecondary: "#3D4350"  // text-secondary
    readonly property color textTertiary: "#4B5260"  // text-tertiary
    readonly property color textMuted: "#5F6673"  // text-muted
    readonly property color success: "#0A6B5B"  // success
    readonly property color warning: "#8A5300"  // warning
    readonly property color latencyPoor: "#9A3412"  // latency-poor
    readonly property color danger: "#B42318"  // danger
    readonly property color info: "#1846C7"  // info
    readonly property color chartUpload: "#C2410C"  // chart-upload
    readonly property color scrim: "#3315171C"  // scrim
    readonly property color shadowOverlay: "#1F15171C"  // shadow-overlay

    // ---- typography (fontSize px, fontWeight 100-900, lineHeight multiplier) ----
    component TypeRole: QtObject {
        property string fontFamily
        property int fontSize
        property int fontWeight
        property real lineHeight: 1.0
    }
    component TypeScale: QtObject {
        readonly property TypeRole title: TypeRole {  // title
            fontFamily: "Inter"
            fontSize: 18
            fontWeight: 600
            lineHeight: 1.4
        }
        readonly property TypeRole bodyMd: TypeRole {  // body-md
            fontFamily: "Inter"
            fontSize: 14
            fontWeight: 400
            lineHeight: 1.5
        }
        readonly property TypeRole bodyMdStrong: TypeRole {  // body-md-strong
            fontFamily: "Inter"
            fontSize: 14
            fontWeight: 600
            lineHeight: 1.5
        }
        readonly property TypeRole bodySm: TypeRole {  // body-sm
            fontFamily: "Inter"
            fontSize: 13
            fontWeight: 400
            lineHeight: 1.5
        }
        readonly property TypeRole labelMd: TypeRole {  // label-md
            fontFamily: "Inter"
            fontSize: 14
            fontWeight: 500
            lineHeight: 1.2
        }
        readonly property TypeRole labelSm: TypeRole {  // label-sm
            fontFamily: "Inter"
            fontSize: 13
            fontWeight: 500
            lineHeight: 1.2
        }
        readonly property TypeRole labelSmStrong: TypeRole {  // label-sm-strong
            fontFamily: "Inter"
            fontSize: 13
            fontWeight: 600
            lineHeight: 1.2
        }
        readonly property TypeRole caption: TypeRole {  // caption
            fontFamily: "Inter"
            fontSize: 12
            fontWeight: 400
            lineHeight: 1.4
        }
        readonly property TypeRole sectionLabel: TypeRole {  // section-label
            fontFamily: "Inter"
            fontSize: 12
            fontWeight: 600
            lineHeight: 1.4
        }
        readonly property TypeRole dataMd: TypeRole {  // data-md
            fontFamily: "JetBrains Mono"
            fontSize: 13
            fontWeight: 400
            lineHeight: 1.5
        }
        readonly property TypeRole dataSm: TypeRole {  // data-sm
            fontFamily: "JetBrains Mono"
            fontSize: 12
            fontWeight: 400
            lineHeight: 1.6
        }
    }
    readonly property TypeScale type: TypeScale {}

    // ---- rounded ----
    readonly property int radiusXs: 4  // rounded.xs
    readonly property int radiusSm: 6  // rounded.sm
    readonly property int radiusMd: 8  // rounded.md
    readonly property int radiusFull: 9999  // rounded.full

    // ---- spacing ----
    readonly property int base: 4  // spacing.base
    readonly property int space1: 4  // spacing.1
    readonly property int space2: 8  // spacing.2
    readonly property int space3: 12  // spacing.3
    readonly property int space4: 16  // spacing.4
    readonly property int space5: 20  // spacing.5
    readonly property int space6: 24  // spacing.6
    readonly property int space8: 32  // spacing.8
    readonly property int space10: 40  // spacing.10
    readonly property int pageX: 32  // spacing.page-x
    readonly property int pageTop: 20  // spacing.page-top
    readonly property int pageBottom: 40  // spacing.page-bottom
    readonly property int sidebarWidth: 216  // spacing.sidebar-width
    readonly property int groupColumnMin: 400  // spacing.group-column-min
    readonly property int windowDefaultWidth: 1280  // spacing.window-default-width
    readonly property int windowDefaultHeight: 800  // spacing.window-default-height
    readonly property int windowMinWidth: 960  // spacing.window-min-width
    readonly property int windowMinHeight: 640  // spacing.window-min-height
    readonly property int controlSm: 32  // spacing.control-sm
    readonly property int controlMd: 36  // spacing.control-md
    readonly property int rowSingle: 44  // spacing.row-single
    readonly property int rowDouble: 52  // spacing.row-double
    readonly property int rowHeader: 32  // spacing.row-header
    readonly property int overlayGap: 4  // spacing.overlay-gap
    readonly property int overlayMargin: 8  // spacing.overlay-margin
    readonly property int overlayShadowY: 4  // spacing.overlay-shadow-y
    readonly property int overlayShadowBlur: 16  // spacing.overlay-shadow-blur
    readonly property int menuMinWidth: 180  // spacing.menu-min-width
    readonly property int popoverMinWidth: 240  // spacing.popover-min-width
    readonly property int popoverMaxWidth: 360  // spacing.popover-max-width
    readonly property int dialogWidth: 400  // spacing.dialog-width
    readonly property int dialogWidthWide: 640  // spacing.dialog-width-wide
    readonly property int editorMinHeight: 240  // spacing.editor-min-height
}
