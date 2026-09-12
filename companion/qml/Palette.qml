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

    // The shadow and the shell move together, so nothing is on screen before the
    // content is. Hidden, the frame rests slideDistance lower and transparent: opening
    // slides it up into place while it fades in, closing slides it back down.
    Item {
        id: frame
        objectName: "shellFrame"
        x: root.shadowMargin
        width: metrics.windowWidth
        height: shell.height

        // Each direction has its own curve -- decelerate into place on open, accelerate
        // away on close -- so each gets its own transition. One Behavior with an easing
        // bound to shellVisible does not work: QML may start the animation before that
        // binding updates, and which one wins depends on the platform, so a direction
        // silently ran the other's curve (on Windows, opening started slow, then snapped).
        state: root.shellVisible ? "shown" : "hidden"
        states: [
            State {
                name: "hidden"
                PropertyChanges { target: frame; y: root.shadowMargin + Theme.slideDistance; opacity: 0 }
            },
            State {
                name: "shown"
                PropertyChanges { target: frame; y: root.shadowMargin; opacity: 1 }
            }
        ]
        transitions: [
            Transition {
                from: "hidden"; to: "shown"
                enabled: Theme.animationsEnabled
                NumberAnimation { property: "y"; duration: metrics.openAnimationMs; easing.type: Theme.easeDecel }
                NumberAnimation { property: "opacity"; duration: metrics.openAnimationMs; easing.type: Theme.easeStandard }
            },
            Transition {
                from: "shown"; to: "hidden"
                enabled: Theme.animationsEnabled
                NumberAnimation { property: "y"; duration: metrics.openAnimationMs; easing.type: Theme.easeAccel }
                NumberAnimation { property: "opacity"; duration: metrics.openAnimationMs; easing.type: Theme.easeStandard }
            }
        ]

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
                    onCycleCategory: (step) => categoryBar.cycle(step)
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
                    // With the strip hidden, listBottomSpacer below provides the gap.
                    bottomMargin: footer.shown ? Theme.spaceSm : 0
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
                        text: i18n.t("no_results_helper") + i18n.retranslate
                        color: Theme.textFaint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.sizeBody
                    }

                    Behavior on height {
                        enabled: Theme.animationsEnabled
                        NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
                    }
                }

                // Plain palette below the last band while the strip is hidden. The shell
                // clips to a rectangle, not its rounded shape, so nothing may reach the
                // bottom edge: scrolling rows, the list fade or the tab underline would
                // paint over the rounded corners and the border.
                Item {
                    objectName: "listBottomSpacer"
                    width: parent.width
                    height: footer.shown ? 0 : Theme.spaceSm
                }

                // Only the apply message, never the result count: the strip is empty,
                // and collapsed, unless an apply has something to say.
                Footer {
                    id: footer
                    objectName: "footer"
                    width: parent.width
                    status: vm.statusOverride
                    busy: applyState.busy
                    applyPhase: applyState.state
                }
            }
        }
    }
}
