import QtQuick
import "."

ListView {
    id: list

    signal rowActivated(int index)
    signal rowClicked(int index)

    clip: true
    boundsBehavior: Flickable.StopAtBounds
    cacheBuffer: metrics.rowHeight * 6
    highlightMoveDuration: Theme.animationsEnabled ? Theme.durFast : 0
    highlightResizeDuration: 0
    highlightFollowsCurrentItem: true

    // One highlight that slides between rows, instead of restyling every row.
    highlight: Rectangle {
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceSm
        radius: Theme.radiusMd
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.16)
        border.width: 1
        border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.55)
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
        // `index` and `model` come from the ListView delegate context.
        title: model.title
        subtitle: model.subtitle
        typeLabel: model.typeLabel
        iconKind: model.iconKind
        accentKind: model.accentKind
        accentColor: model.accentColor
        isFavorite: model.isFavorite
        selected: index === list.currentIndex
        onClicked: list.rowClicked(index)
        onActivated: list.rowActivated(index)
    }
}
