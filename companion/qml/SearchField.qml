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
    signal cycleCategory(int step)

    function takeFocus() { input.forceActiveFocus() }

    implicitHeight: 56

    // A magnifier drawn in place: no icon font or image asset to ship.
    Canvas {
        id: searchIcon
        objectName: "searchIcon"
        width: 18; height: 18
        anchors.left: parent.left
        anchors.leftMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = "" + Theme.textMuted
            ctx.lineWidth = 1.8
            ctx.lineCap = "round"
            ctx.beginPath(); ctx.arc(7.5, 7.5, 5.5, 0, Math.PI * 2); ctx.stroke()
            ctx.beginPath(); ctx.moveTo(11.6, 11.6); ctx.lineTo(16, 16); ctx.stroke()
        }
    }

    TextInput {
        id: input
        objectName: "searchInput"
        // Anchored between the icon and the refresh button, so the button keeps its
        // place in the right-hand column above the connection dot.
        anchors.left: searchIcon.right
        anchors.leftMargin: Theme.spaceMd
        anchors.right: refreshButton.left
        anchors.rightMargin: Theme.spaceMd
        anchors.verticalCenter: parent.verticalCenter
        color: Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.sizeInput
        selectionColor: Theme.accent
        clip: true

        // Tab / Shift+Tab switch category. Accepting the event keeps the caret here:
        // left unhandled, Qt Quick would move focus along the tab chain instead.
        // Windows delivers Shift+Tab as Key_Backtab; Tab with Shift is handled too.
        Keys.onPressed: (event) => {
            if (event.key === Qt.Key_Backtab
                    || (event.key === Qt.Key_Tab && (event.modifiers & Qt.ShiftModifier))) {
                control.cycleCategory(-1)
                event.accepted = true
            } else if (event.key === Qt.Key_Tab) {
                control.cycleCategory(1)
                event.accepted = true
            }
        }
        Keys.onDownPressed: control.moveSelection(1)
        Keys.onUpPressed: control.moveSelection(-1)
        Keys.onEscapePressed: control.dismissed()
        onAccepted: control.accepted()
    }

    Text {
        objectName: "searchPlaceholder"
        anchors.left: input.left
        anchors.right: input.right
        anchors.verticalCenter: input.verticalCenter
        visible: input.text.length === 0
        text: i18n.t("search_placeholder") + i18n.retranslate
        color: Theme.textFaint
        font.family: Theme.fontFamily
        font.pixelSize: Theme.sizeInput
        elide: Text.ElideRight
    }

    // An icon, not a boxed button: only hover gives it a surface.
    Rectangle {
        id: refreshButton
        objectName: "refreshButton"
        width: Theme.iconButtonSize; height: Theme.iconButtonSize
        anchors.right: parent.right
        anchors.rightMargin: Theme.spaceLg
        anchors.verticalCenter: parent.verticalCenter
        radius: Theme.radiusSm
        color: refreshArea.containsMouse ? Theme.surfaceOverlay : "transparent"
        border.width: 0

        Behavior on color {
            enabled: Theme.animationsEnabled
            ColorAnimation { duration: Theme.durFast }
        }

        Text {
            objectName: "refreshGlyph"
            anchors.centerIn: parent
            text: "↻"
            color: Theme.textMuted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeBody
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
