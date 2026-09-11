import QtQuick
import QtQuick.Controls
import QtQuick.Effects
import "."

Window {
    id: root

    // Transparent room on every side for the QML-drawn shadow. QuickWindowAdapter
    // subtracts it, so the window controller still places the visible palette.
    readonly property int shadowMargin: Theme.shadowMargin

    width: metrics.windowWidth + 2 * shadowMargin
    height: frame.height + 2 * shadowMargin
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

    property bool shellVisible: false
    signal closeFinished()

    function playOpen() { closeTimer.stop(); shellVisible = true }
    function playClose() { shellVisible = false; closeTimer.restart() }

    // Tells Python when it is safe to actually hide the window. A Timer rather than the
    // fade's onFinished: an animation inside a Behavior does not reliably emit finished,
    // and with animations disabled the Behavior is skipped altogether -- either way the
    // window would never hide. interval 0 fires on the next event-loop pass.
    Timer {
        id: closeTimer
        interval: Theme.animationsEnabled ? metrics.openAnimationMs : 0
        repeat: false
        onTriggered: root.closeFinished()
    }

    function focusSearch() { searchField.takeFocus() }
    readonly property bool searchHasFocus: searchField.inputHasFocus

    Component.onCompleted: Theme.animationsEnabled = metrics.animationsEnabled

    // The shadow and the shell fade and scale together, so nothing is on screen
    // before the content is.
    Item {
        id: frame
        objectName: "shellFrame"
        x: root.shadowMargin
        y: root.shadowMargin
        width: metrics.windowWidth
        height: shell.height

        opacity: root.shellVisible ? 1 : 0
        scale: root.shellVisible ? 1 : 0.97
        transformOrigin: Item.Center

        Behavior on opacity {
            enabled: Theme.animationsEnabled
            NumberAnimation {
                duration: metrics.openAnimationMs
                easing.type: Theme.easeStandard
            }
        }
        Behavior on scale {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: metrics.openAnimationMs; easing.type: Theme.easeOvershoot }
        }

        RectangularShadow {
            objectName: "shellShadow"
            anchors.fill: shell
            radius: shell.radius
            blur: Theme.shadowBlur
            offset.y: Theme.shadowOffsetY
            color: Theme.shadowColor
        }

        Rectangle {
            id: shell
            width: parent.width
            height: content.implicitHeight
            radius: Theme.radiusLg
            color: Theme.surface
            border.width: 1
            border.color: Theme.border
            clip: true

            Behavior on height {
                enabled: Theme.animationsEnabled
                NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
            }

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
}
