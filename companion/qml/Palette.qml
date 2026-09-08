import QtQuick
import QtQuick.Controls
import "."

Window {
    id: root

    width: 760
    height: 120
    color: "transparent"
    // MUST be WindowStaysOnTopHint. The short "Qt.WindowStaysOnTop" parses without a
    // warning and silently does nothing, which drops the palette behind Premiere.
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint

    // Called by QuickWindowAdapter (Task 2).
    function focusSearch() { searchInput.forceActiveFocus() }
    readonly property bool searchHasFocus: searchInput.activeFocus

    Rectangle {
        anchors.fill: parent
        radius: 16
        color: Theme.surface
        TextInput {
            id: searchInput
            anchors.centerIn: parent
            color: Theme.text
            text: vm.query
        }
    }
}
