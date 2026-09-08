import QtQuick
import "."

Item {
    id: control

    property string activeCategory: "Todos"
    property string connectionState: "offline"
    signal categoryPicked(string category)

    readonly property var categories: ["Todos", "Video", "Audio", "Presets", "Projeto", "Favoritos"]
    readonly property int activeIndex: categories.indexOf(activeCategory)

    implicitHeight: 40

    // One pill that slides between chips. The chips themselves stay transparent, so the
    // pill is the only thing carrying "active" state -- that is what makes it read as
    // movement rather than six independent colour changes.
    Rectangle {
        id: activePill

        readonly property Item target: control.activeIndex >= 0 ? chipRepeater.itemAt(control.activeIndex) : null

        visible: target !== null
        x: target ? chips.x + target.x : 0
        y: target ? chips.y + target.y : 0
        width: target ? target.width : 0
        height: target ? target.height : 0
        radius: Theme.radiusPill
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.20)
        border.width: 1
        border.color: Theme.accent

        Behavior on x {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeStandard }
        }
        Behavior on width {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeStandard }
        }
    }

    Row {
        id: chips
        anchors.left: parent.left
        anchors.leftMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.spaceSm

        Repeater {
            id: chipRepeater
            model: control.categories
            delegate: Rectangle {
                id: chip
                required property string modelData
                readonly property bool active: modelData === control.activeCategory

                height: 26
                width: label.implicitWidth + Theme.spaceMd * 2
                radius: Theme.radiusPill
                // Only hover paints here; "active" belongs to the sliding pill above.
                color: (!active && chipArea.containsMouse) ? Theme.surfaceOverlay : "transparent"
                border.width: 1
                border.color: active ? "transparent" : Theme.border

                Behavior on color {
                    enabled: Theme.animationsEnabled
                    ColorAnimation { duration: Theme.durFast }
                }

                Text {
                    id: label
                    anchors.centerIn: parent
                    text: chip.modelData
                    color: chip.active ? Theme.text : Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeCaption
                    font.bold: chip.active

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

    Rectangle {
        id: connectionDot
        objectName: "connectionDot"
        width: 10; height: 10
        radius: 5
        anchors.right: parent.right
        anchors.rightMargin: Theme.spaceLg
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
