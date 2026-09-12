pragma Singleton
import QtQuick

QtObject {
    // ---- surfaces -------------------------------------------------------------
    readonly property color surface:        "#0D0C14"
    readonly property color surfaceRaised:  "#151420"
    readonly property color surfaceOverlay: "#1C1B2B"
    readonly property color border:         "#2A2A35"

    // ---- text -----------------------------------------------------------------
    readonly property color text:      "#E8E8F0"
    readonly property color textMuted: "#88889B"
    readonly property color textFaint: "#5E5E70"

    // ---- semantic -------------------------------------------------------------
    readonly property color accent:  "#7278F0"
    readonly property color success: "#3DD68C"
    readonly property color warning: "#F5A623"
    readonly property color offline: "#7E8698"

    // ---- spacing scale --------------------------------------------------------
    readonly property int spaceXs: 4
    readonly property int spaceSm: 8
    readonly property int spaceMd: 12
    readonly property int spaceLg: 16
    readonly property int spaceXl: 24

    // ---- controls -------------------------------------------------------------
    readonly property int iconButtonSize: 28

    // ---- radii ----------------------------------------------------------------
    readonly property int radiusSm:   6
    readonly property int radiusMd:   10
    readonly property int radiusLg:   16
    readonly property int radiusPill: 999

    // ---- elevation ------------------------------------------------------------
    // Drawn in QML, never by DWM: a DWM shadow spans the whole native window and shows
    // up at once, before the content has faded in. blur + offset must fit the margin.
    readonly property int   shadowMargin:  24
    readonly property int   shadowBlur:    20
    readonly property int   shadowOffsetY: 4
    readonly property color shadowColor:   "#8C000000"

    // ---- type -----------------------------------------------------------------
    readonly property string fontFamily: "Google Sans Flex"
    readonly property int sizeCaption: 11
    readonly property int sizeSmall:   12
    readonly property int sizeBody:    13
    readonly property int sizeInput:   18
    readonly property int sizeTitle:   15
    readonly property int sizeDisplay: 20

    // ---- motion ---------------------------------------------------------------
    // Every animation references these. No magic numbers in components.
    readonly property int durFast: 120
    readonly property int durBase: 180
    readonly property int durSlow: 260
    readonly property int easeStandard:  Easing.OutCubic
    readonly property int easeDecel:     Easing.OutQuint
    readonly property int easeOvershoot: Easing.OutBack
    readonly property int easeAccel:     Easing.InCubic

    // How far the palette travels as it slides in on open and out on close.
    readonly property int slideDistance: 12

    // Mirrors the user's "Use interface animations" preference. Every Behavior and
    // Transition gates on this.
    property bool animationsEnabled: true

    // The selection tint for an item colour: the item's hue at a fixed saturation and
    // lightness, so a pale pastel (the preset lavender) reads as clearly as a vivid one.
    function selectionTint(c, alpha) {
        const achromatic = c.hslHue < 0
        return Qt.hsla(achromatic ? 0 : c.hslHue, achromatic ? 0 : 0.65, 0.66, alpha)
    }

    // Linear colour mix, the QML counterpart of app.blend_colors.
    function mix(a, b, t) {
        return Qt.rgba(a.r + (b.r - a.r) * t,
                       a.g + (b.g - a.g) * t,
                       a.b + (b.b - a.b) * t,
                       a.a + (b.a - a.a) * t)
    }
}
