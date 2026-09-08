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
    signal nestConfirmed(string name)
    signal nestCancelled()

    property alias nestPanelOpen: nestPanel.open

    function openNestPanel() { nestPanel.open = true; nestPanel.takeFocus() }
    function closeNestPanel() { nestPanel.open = false }

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

            NestPanel {
                id: nestPanel
                objectName: "nestPanel"
                width: parent.width
                onConfirmed: (name) => root.nestConfirmed(name)
                onCancelled: root.nestCancelled()
            }

            ResultList {
                id: resultList
                objectName: "resultList"
                width: parent.width
                height: vm.viewState === "results" ? metrics.resultsHeight : 0
                visible: height > 0
                model: vm.results
                currentIndex: vm.selectedIndex
                onRowClicked: (index) => vm.set_selected_index(index)
                onRowActivated: (index) => { vm.set_selected_index(index); root.applyRequested() }

                Behavior on height {
                    enabled: Theme.animationsEnabled
                    NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
                }
            }

            Item {
                id: emptyState
                objectName: "emptyState"
                width: parent.width
                height: visible ? 84 : 0
                visible: vm.viewState === "message"

                Text {
                    anchors.centerIn: parent
                    text: i18n.t("no_results_helper")
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeBody
                }

                Behavior on height {
                    enabled: Theme.animationsEnabled
                    NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
                }
            }

            Footer {
                id: footer
                objectName: "footer"
                width: parent.width
                hint: nestPanel.open ? i18n.t("nest_footer_hint") : vm.footerHint
                status: vm.statusText
                busy: applyState.busy
                applyPhase: applyState.state
            }
        }
    }
}
