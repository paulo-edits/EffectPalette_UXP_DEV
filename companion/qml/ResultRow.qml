import QtQuick
import "."

Item {
    id: row

    required property string title
    required property string subtitle
    required property string typeLabel
    required property string iconKind
    required property string accentKind
    required property var accentColor
    required property bool isFavorite
    required property bool selected

    signal clicked()
    signal activated()

    readonly property color accent: accentColor ? accentColor : metrics.accentFor(accentKind)

    height: metrics.rowHeight
    width: ListView.view ? ListView.view.width : 0

    Rectangle {
        id: surface
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceSm
        anchors.topMargin: 2
        anchors.bottomMargin: 2
        radius: Theme.radiusMd
        color: hoverArea.containsMouse && !row.selected
               ? Qt.rgba(row.accent.r, row.accent.g, row.accent.b, 0.10)
               : "transparent"

        Behavior on color {
            enabled: Theme.animationsEnabled
            ColorAnimation { duration: Theme.durFast }
        }

        Row {
            anchors.fill: parent
            anchors.leftMargin: Theme.spaceMd
            anchors.rightMargin: Theme.spaceMd
            spacing: Theme.spaceMd

            Rectangle {
                width: 26; height: 26
                anchors.verticalCenter: parent.verticalCenter
                radius: Theme.radiusSm
                color: Qt.rgba(row.accent.r, row.accent.g, row.accent.b, row.selected ? 0.35 : 0.18)

                Behavior on color {
                    enabled: Theme.animationsEnabled
                    ColorAnimation { duration: Theme.durFast }
                }

                Text {
                    anchors.centerIn: parent
                    text: row.iconKind === "preset"   ? "✎"
                        : row.iconKind === "project"  ? "▣"
                        : row.iconKind === "favorite" ? "★"
                        : "✦"
                    color: row.accent
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                }
            }

            Column {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - 26 - badge.width - Theme.spaceMd * 3
                spacing: 1

                Text {
                    width: parent.width
                    text: row.title
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeBody
                    font.bold: true
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    text: row.subtitle
                    color: row.selected ? Theme.textMuted : Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeCaption
                    elide: Text.ElideRight
                }
            }

            Rectangle {
                id: badge
                anchors.verticalCenter: parent.verticalCenter
                width: badgeLabel.implicitWidth + Theme.spaceMd
                height: 18
                radius: Theme.radiusSm
                color: Qt.rgba(row.accent.r, row.accent.g, row.accent.b, 0.22)

                Text {
                    id: badgeLabel
                    anchors.centerIn: parent
                    text: row.typeLabel
                    color: row.accent
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeCaption
                    font.bold: true
                }
            }
        }
    }

    MouseArea {
        id: hoverArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: row.clicked()
        onDoubleClicked: row.activated()
    }
}
