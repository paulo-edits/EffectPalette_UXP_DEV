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

    property alias searchText: searchField.text

    signal dismissed()
    signal refreshRequested()
    signal applyRequested()

    function focusSearch() { searchField.takeFocus() }
    readonly property bool searchHasFocus: searchField.inputHasFocus

    Component.onCompleted: Theme.animationsEnabled = metrics.animationsEnabled

    Rectangle {
        id: shell
        anchors.fill: parent
        implicitHeight: content.implicitHeight
        radius: Theme.radiusLg
        color: Theme.surface
        border.width: 1
        border.color: Theme.border
        clip: true

        Column {
            id: content
            width: parent.width

            SearchField {
                id: searchField
                objectName: "searchField"
                width: parent.width
                onAccepted: root.applyRequested()
                onMoveSelection: (delta) => vm.move_selection(delta)
                onDismissed: root.dismissed()
                onRefreshRequested: root.refreshRequested()
                onTextChanged: vm.set_query(text)
            }

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.border
            }

            CategoryBar {
                id: categoryBar
                objectName: "categoryBar"
                width: parent.width
                activeCategory: vm.activeCategory ? vm.activeCategory : "Todos"
                connectionState: vm.connectionState
                onCategoryPicked: (category) => vm.select_category(category)
            }
        }
    }
}
