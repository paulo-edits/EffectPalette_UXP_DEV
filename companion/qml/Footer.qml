import QtQuick
import QtQuick.Controls
import "."

// A status strip rather than a footer: it exists only while an apply has something to
// say ("Applying...", "Applied", "Select a clip first") and collapses otherwise. The key
// hints and the result count it used to carry were removed as noise.
Item {
    id: control

    property string status: ""
    property bool busy: false
    // Named applyPhase, not applyState: `applyState` is the context property holding
    // the ApplyController, and shadowing it here would make the binding ambiguous.
    property string applyPhase: "idle"

    readonly property bool shown: status.length > 0

    implicitHeight: shown ? 32 : 0
    clip: true

    Behavior on implicitHeight {
        enabled: Theme.animationsEnabled
        NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
    }

    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: Theme.border
    }

    Text {
        id: statusLabel
        anchors.left: parent.left
        anchors.leftMargin: Theme.spaceLg
        anchors.right: progress.left
        anchors.rightMargin: Theme.spaceMd
        anchors.verticalCenter: parent.verticalCenter
        text: control.status
        elide: Text.ElideRight
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

    ProgressBar {
        id: progress
        width: 72
        anchors.right: parent.right
        anchors.rightMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        indeterminate: true
        visible: control.busy
        opacity: control.busy ? 1 : 0

        Behavior on opacity {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durFast }
        }
    }
}
