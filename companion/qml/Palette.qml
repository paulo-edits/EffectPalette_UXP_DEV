import QtQuick
import QtQuick.Controls
import "."

Window {
    id: root

    width: metrics.windowWidth
    height: shell.implicitHeight
    color: "transparent"
    // MUST be WindowStaysOnTopHint. The short "Qt.WindowStaysOnTop" parses without a
    // warning and silently does nothing, which drops the palette behind Premiere.
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint

    // Lets the Python tests read the singleton's tokens.
    readonly property var themeProbe: Theme

    function focusSearch() { searchInput.forceActiveFocus() }
    readonly property bool searchHasFocus: searchInput.activeFocus

    Component.onCompleted: Theme.animationsEnabled = metrics.animationsEnabled

    Rectangle {
        id: shell
        anchors.fill: parent
        implicitHeight: 120
        radius: Theme.radiusLg
        color: Theme.surface
        border.width: 1
        border.color: Theme.border

        TextInput {
            id: searchInput
            anchors.centerIn: parent
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.sizeBody
            text: vm.query
        }
    }
}
