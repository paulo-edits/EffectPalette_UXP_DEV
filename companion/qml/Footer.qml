import QtQuick
import QtQuick.Controls
import "."

Item {
    id: control

    property string hint: ""
    property string status: ""
    property bool busy: false
    // Named applyPhase, not applyState: `applyState` is the context property holding
    // the ApplyController, and shadowing it here would make the binding ambiguous.
    property string applyPhase: "idle"

    implicitHeight: 36

    Rectangle {
        anchors.fill: parent
        color: Theme.surfaceOverlay
        opacity: 0.5
    }

    Text {
        anchors.left: parent.left
        anchors.leftMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        text: control.hint
        color: Theme.textFaint
        font.family: Theme.fontFamily
        font.pixelSize: Theme.sizeCaption
    }

    Row {
        anchors.right: parent.right
        anchors.rightMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.spaceSm

        ProgressBar {
            id: progress
            width: 72
            anchors.verticalCenter: parent.verticalCenter
            indeterminate: true
            visible: control.busy
            opacity: control.busy ? 1 : 0

            Behavior on opacity {
                enabled: Theme.animationsEnabled
                NumberAnimation { duration: Theme.durFast }
            }
        }

        Text {
            id: statusLabel
            anchors.verticalCenter: parent.verticalCenter
            text: control.status
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeCaption
            color: control.applyPhase === "success" ? Theme.success
                 : control.applyPhase === "error"   ? Theme.warning
                 : Theme.textMuted

            Behavior on color {
                enabled: Theme.animationsEnabled
                ColorAnimation { duration: Theme.durBase }
            }
        }
    }
}
