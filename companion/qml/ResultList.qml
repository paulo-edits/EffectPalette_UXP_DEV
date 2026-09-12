import QtQuick
import "."

ListView {
    id: list

    signal rowActivated(int index)
    signal rowClicked(int index)

    clip: true
    // Breathing room above the first row and below the last. Palette.qml drops the
    // bottom margin while the status strip is hidden and puts plain palette there instead.
    topMargin: Theme.spaceSm
    bottomMargin: Theme.spaceSm
    boundsBehavior: Flickable.StopAtBounds
    cacheBuffer: metrics.rowHeight * 6
    highlightMoveDuration: Theme.animationsEnabled ? Theme.durFast : 0
    highlightResizeDuration: 0
    highlightFollowsCurrentItem: true

    // Keep a row of context beyond the selection while it moves, so reaching the edge
    // never looks like the end of the list. Only the real first and last rows sit
    // against the edge.
    highlightRangeMode: ListView.ApplyRange
    preferredHighlightBegin: metrics.rowHeight
    preferredHighlightEnd: height - metrics.rowHeight

    // One highlight that slides between rows, instead of restyling every row.
    // The view positions the highlight item itself -- x included -- so the visible
    // rectangle is inset inside a plain wrapper, matching the rows' own inset.
    highlight: Item {
        Rectangle {
            objectName: "selectionHighlight"
            x: Theme.spaceSm
            width: parent.width - 2 * Theme.spaceSm
            height: parent.height
            radius: Theme.radiusMd
            // In the selected row's own hue -- the colour of its icon and badge -- at a
            // fixed strength, blended as the selection moves between rows of different types.
            property color accent: list.currentItem ? list.currentItem.accent : Theme.accent
            Behavior on accent {
                enabled: Theme.animationsEnabled
                ColorAnimation { duration: Theme.durFast }
            }
            color: Theme.selectionTint(accent, 0.24)
            border.width: 1
            border.color: Theme.selectionTint(accent, 0.6)
        }
    }

    // Soft edges: rows fade out as they scroll under the tabs or toward the bottom --
    // but only where more rows continue past that edge. A fade over the real first or
    // last row made the end of the list look cut off.
    // `parent: list` keeps them on the view itself; declared plainly inside a ListView
    // they would be moved into the scrolling content and scroll away with the rows.
    // Each is as tall as the padding and inset by the shell's 1 px border, so it never
    // paints over the palette's outline.
    Rectangle {
        objectName: "listTopFade"
        parent: list
        z: 2
        opacity: list.atYBeginning ? 0 : 1
        Behavior on opacity {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durFast }
        }
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 1
        anchors.rightMargin: 1
        height: Theme.spaceSm
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.surface }
            GradientStop { position: 1.0; color: Qt.rgba(Theme.surface.r, Theme.surface.g, Theme.surface.b, 0) }
        }
    }

    Rectangle {
        objectName: "listBottomFade"
        parent: list
        z: 2
        opacity: list.atYEnd ? 0 : 1
        Behavior on opacity {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durFast }
        }
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 1
        anchors.rightMargin: 1
        height: Theme.spaceSm
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(Theme.surface.r, Theme.surface.g, Theme.surface.b, 0) }
            GradientStop { position: 1.0; color: Theme.surface }
        }
    }

    // Staggered entry for a new result set.
    add: Transition {
        enabled: Theme.animationsEnabled
        NumberAnimation {
            property: "opacity"
            from: 0; to: 1
            duration: Theme.durBase
            easing.type: Theme.easeStandard
        }
        NumberAnimation {
            property: "y"
            from: 8
            duration: Theme.durBase
            easing.type: Theme.easeDecel
        }
    }

    displaced: Transition {
        enabled: Theme.animationsEnabled
        NumberAnimation {
            properties: "x,y"
            duration: Theme.durFast
            easing.type: Theme.easeStandard
        }
    }

    delegate: ResultRow {
        // ResultRow declares required properties, which switches the view to
        // required-property mode: roles fill the same-named properties directly, and
        // `model`/`index` are no longer injected -- so `index` must be declared here.
        required property int index
        selected: index === list.currentIndex
        onClicked: list.rowClicked(index)
        onActivated: list.rowActivated(index)
    }
}
