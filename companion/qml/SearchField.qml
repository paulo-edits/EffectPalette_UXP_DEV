import QtQuick
import QtQuick.Controls
import "."

Item {
    id: control

    property alias text: input.text
    property bool inputHasFocus: input.activeFocus

    signal accepted()
    signal moveSelection(int delta)
    signal dismissed()
    signal refreshRequested()

    function takeFocus() { input.forceActiveFocus() }

    implicitHeight: 52

    Row {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceLg
        anchors.rightMargin: Theme.spaceMd
        spacing: Theme.spaceSm

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: ">"
            color: Theme.accent
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeTitle
            font.bold: true
        }

        TextInput {
            id: input
            width: parent.width - 90
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeTitle
            selectionColor: Theme.accent
            clip: true

            Keys.onDownPressed: control.moveSelection(1)
            Keys.onUpPressed: control.moveSelection(-1)
            Keys.onEscapePressed: control.dismissed()
            onAccepted: control.accepted()
        }

        Rectangle {
            id: refreshButton
            width: 28; height: 28
            anchors.verticalCenter: parent.verticalCenter
            radius: Theme.radiusSm
            color: refreshArea.containsMouse ? Theme.surfaceOverlay : "transparent"
            border.width: 1
            border.color: Theme.border

            Behavior on color {
                enabled: Theme.animationsEnabled
                ColorAnimation { duration: Theme.durFast }
            }

            Text {
                anchors.centerIn: parent
                text: "↻"
                color: Theme.textMuted
                font.family: Theme.fontFamily
            }

            MouseArea {
                id: refreshArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: control.refreshRequested()
            }
        }
    }
}
