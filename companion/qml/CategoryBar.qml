import QtQuick
import "."

// Categories as tabs: plain labels, with one accent underline that slides to the active
// one. No outlines -- the bar reads as part of the search surface, not a row of buttons.
Item {
    id: control

    property string activeCategory: "Todos"
    property string connectionState: "offline"
    signal categoryPicked(string category)

    // Internal keys, shared with the view-model and settings; shown translated.
    // Every filter the palette can be in needs a tab, or an active one (say from /trans)
    // is invisible -- the tests check this list against CATEGORY_TYPE_FILTERS.
    readonly property var categories: ["Todos", "Video", "Audio", "Transicoes", "Presets", "Projeto", "Favoritos"]
    readonly property int activeIndex: categories.indexOf(activeCategory)

    // Tab / Shift+Tab from the search field: the next or previous tab, wrapping around.
    // Goes through categoryPicked, the same path as a click.
    function cycle(step) {
        const count = categories.length
        const current = activeIndex >= 0 ? activeIndex : 0
        categoryPicked(categories[(current + step + count) % count])
    }

    implicitHeight: 40

    Row {
        id: chips
        anchors.left: parent.left
        // Each tab pads its label by spaceSm, so the first label lands on the spaceLg
        // content edge shared with the search icon and the footer.
        anchors.leftMargin: Theme.spaceLg - Theme.spaceSm
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        spacing: 0

        Repeater {
            id: chipRepeater
            model: control.categories
            delegate: Item {
                id: chip
                required property string modelData
                readonly property bool active: modelData === control.activeCategory
                readonly property string label: i18n.category(modelData) + i18n.retranslate

                objectName: "chip_" + modelData
                height: chips.height
                width: labelText.implicitWidth + Theme.spaceSm * 2

                Text {
                    id: labelText
                    objectName: "chipLabel"
                    anchors.centerIn: parent
                    text: chip.label
                    color: chip.active || chipArea.containsMouse ? Theme.text : Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeBody
                    // One weight for every state, so a tab never changes width.
                    font.weight: Font.Medium

                    Behavior on color {
                        enabled: Theme.animationsEnabled
                        ColorAnimation { duration: Theme.durFast }
                    }
                }

                MouseArea {
                    id: chipArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: control.categoryPicked(chip.modelData)
                }
            }
        }
    }

    // The one moving part: an underline that slides and resizes between tabs.
    Rectangle {
        id: activePill
        objectName: "activePill"

        // itemAt() is not a notifying property, so without `count` in the expression
        // the binding ran once, before the Repeater had built any tab, and stayed null.
        readonly property Item target: chipRepeater.count > 0 && control.activeIndex >= 0
                                       ? chipRepeater.itemAt(control.activeIndex) : null

        visible: target !== null
        height: 2
        radius: 1
        y: parent.height - height
        // Exactly under the label text, so it lines up with the text and with the
        // search icon above it. Spanning the whole tab, it stuck out into the padding.
        x: target ? chips.x + target.x + Theme.spaceSm : 0
        width: target ? target.width - Theme.spaceSm * 2 : 0
        color: Theme.accent

        Behavior on x {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeStandard }
        }
        Behavior on width {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeStandard }
        }
    }

    Rectangle {
        id: connectionDot
        objectName: "connectionDot"
        width: 8; height: 8
        radius: 4
        anchors.right: parent.right
        // Centred under the refresh button above it: one right-hand column.
        anchors.rightMargin: Theme.spaceLg + (Theme.iconButtonSize - width) / 2
        anchors.verticalCenter: parent.verticalCenter
        color: control.connectionState === "connected" ? Theme.success
             : control.connectionState === "problem"   ? Theme.warning
             : Theme.offline

        Behavior on color {
            enabled: Theme.animationsEnabled
            ColorAnimation { duration: Theme.durBase }
        }

        // A short pulse on every state change, so a connect/disconnect is noticeable
        // without the dot animating forever in the corner of the user's eye.
        SequentialAnimation {
            id: connectionPulse
            running: false
            NumberAnimation {
                target: connectionDot; property: "scale"
                to: 1.6; duration: Theme.durFast; easing.type: Theme.easeStandard
            }
            NumberAnimation {
                target: connectionDot; property: "scale"
                to: 1.0; duration: Theme.durBase; easing.type: Theme.easeOvershoot
            }
        }

        onColorChanged: if (Theme.animationsEnabled) connectionPulse.restart()
    }
}
