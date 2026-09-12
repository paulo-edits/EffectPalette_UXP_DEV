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

            // One line icon per item type, drawn in the item's colour. Drawn in place,
            // so there is no icon font or image asset to ship.
            Canvas {
                id: icon
                objectName: "rowIcon"

                readonly property var glyphs: ["fx", "audio", "transition", "preset",
                                               "project", "favorite", "layers", "label"]
                readonly property string glyph: glyphs.indexOf(row.iconKind) >= 0 ? row.iconKind : "fx"

                width: 18; height: 18
                anchors.verticalCenter: parent.verticalCenter
                onGlyphChanged: requestPaint()

                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    const colour = "" + row.accent
                    ctx.strokeStyle = colour
                    ctx.fillStyle = colour
                    ctx.lineWidth = 1.6
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
                    switch (glyph) {
                    case "fx":
                        ctx.font = "italic bold 13px '" + Theme.fontFamily + "'"
                        ctx.fillText("fx", 2.5, 13.5)
                        break
                    case "audio": {
                        const bars = [5, 10, 14, 8, 12, 6]
                        for (let i = 0; i < bars.length; ++i) {
                            const x = 2.5 + i * 2.6
                            ctx.beginPath(); ctx.moveTo(x, 9 - bars[i] / 2); ctx.lineTo(x, 9 + bars[i] / 2); ctx.stroke()
                        }
                        break
                    }
                    case "transition":
                        ctx.strokeRect(1.8, 2.8, 9.5, 8.5)
                        ctx.strokeRect(6.7, 6.7, 9.5, 8.5)
                        break
                    case "preset": {
                        const knobs = [6, 12, 8]
                        for (let i = 0; i < 3; ++i) {
                            const y = 4 + i * 5
                            ctx.beginPath(); ctx.moveTo(2, y); ctx.lineTo(16, y); ctx.stroke()
                            ctx.beginPath(); ctx.arc(knobs[i], y, 2.2, 0, Math.PI * 2); ctx.fill()
                        }
                        break
                    }
                    case "project":
                        ctx.strokeRect(2.5, 3.5, 13, 11)
                        ctx.beginPath(); ctx.moveTo(5.5, 3.5); ctx.lineTo(5.5, 14.5); ctx.stroke()
                        ctx.beginPath(); ctx.moveTo(12.5, 3.5); ctx.lineTo(12.5, 14.5); ctx.stroke()
                        ctx.beginPath(); ctx.moveTo(2.5, 9); ctx.lineTo(5.5, 9); ctx.stroke()
                        ctx.beginPath(); ctx.moveTo(12.5, 9); ctx.lineTo(15.5, 9); ctx.stroke()
                        break
                    case "favorite": {
                        ctx.beginPath()
                        for (let i = 0; i < 10; ++i) {
                            const radius = i % 2 === 0 ? 7.2 : 3.1
                            const angle = -Math.PI / 2 + i * Math.PI / 5
                            const x = 9 + radius * Math.cos(angle)
                            const y = 9.6 + radius * Math.sin(angle)
                            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                        }
                        ctx.closePath(); ctx.stroke()
                        break
                    }
                    case "layers":
                        ctx.beginPath(); ctx.moveTo(2, 6.5); ctx.lineTo(9, 3); ctx.lineTo(16, 6.5); ctx.lineTo(9, 10); ctx.closePath(); ctx.stroke()
                        ctx.beginPath(); ctx.moveTo(2, 10); ctx.lineTo(9, 13.5); ctx.lineTo(16, 10); ctx.stroke()
                        ctx.beginPath(); ctx.moveTo(2, 13.5); ctx.lineTo(9, 17); ctx.lineTo(16, 13.5); ctx.stroke()
                        break
                    case "label":
                        ctx.beginPath(); ctx.arc(9, 9, 5.5, 0, Math.PI * 2); ctx.fill()
                        break
                    }
                }
            }

            Column {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - icon.width - badge.width - Theme.spaceMd * 3
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
