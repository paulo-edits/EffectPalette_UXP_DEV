import QtQuick
import QtQuick.Controls
import "."

Item {
    id: control

    property bool open: false
    property alias nestName: nameInput.text

    signal confirmed(string name)
    signal cancelled()

    function takeFocus() { nameInput.forceActiveFocus() }
    function confirm() { control.confirmed(nameInput.text.trim()); control.open = false }
    function cancel() { control.open = false; control.cancelled() }

    implicitHeight: open ? 128 : 0
    clip: true

    Behavior on implicitHeight {
        enabled: Theme.animationsEnabled
        NumberAnimation { duration: Theme.durBase; easing.type: Theme.easeDecel }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: Theme.spaceSm
        radius: Theme.radiusMd
        color: Theme.surfaceOverlay
        border.width: 1
        border.color: Theme.border
        opacity: control.open ? 1 : 0

        Behavior on opacity {
            enabled: Theme.animationsEnabled
            NumberAnimation { duration: Theme.durFast }
        }

        Column {
            anchors.fill: parent
            anchors.margins: Theme.spaceMd
            spacing: Theme.spaceSm

            Text {
                text: i18n.t("nest_dialog_question")
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.sizeTitle
                font.bold: true
            }

            Text {
                text: i18n.t("nest_name_label")
                color: Theme.textMuted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.sizeCaption
                font.bold: true
            }

            Rectangle {
                width: parent.width
                height: 30
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: nameInput.activeFocus ? Theme.accent : Theme.border

                Behavior on border.color {
                    enabled: Theme.animationsEnabled
                    ColorAnimation { duration: Theme.durFast }
                }

                TextInput {
                    id: nameInput
                    anchors.fill: parent
                    anchors.margins: Theme.spaceSm
                    verticalAlignment: TextInput.AlignVCenter
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sizeBody
                    onAccepted: control.confirm()
                    Keys.onEscapePressed: control.cancel()

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: i18n.t("nest_name_hint")
                        color: Theme.textFaint
                        font: nameInput.font
                        visible: nameInput.text.length === 0
                    }
                }
            }

            Row {
                anchors.right: parent.right
                spacing: Theme.spaceSm

                Rectangle {
                    width: cancelLabel.implicitWidth + Theme.spaceLg * 2
                    height: 28
                    radius: Theme.radiusSm
                    color: cancelArea.containsMouse ? Theme.surface : "transparent"
                    border.width: 1
                    border.color: Theme.border

                    Behavior on color {
                        enabled: Theme.animationsEnabled
                        ColorAnimation { duration: Theme.durFast }
                    }

                    Text {
                        id: cancelLabel
                        anchors.centerIn: parent
                        text: i18n.t("nest_cancel")
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.sizeCaption
                    }

                    MouseArea {
                        id: cancelArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: control.cancel()
                    }
                }

                Rectangle {
                    width: confirmLabel.implicitWidth + Theme.spaceLg * 2
                    height: 28
                    radius: Theme.radiusSm
                    color: confirmArea.containsMouse
                           ? Qt.lighter(Theme.accent, 1.15)
                           : Theme.accent

                    Behavior on color {
                        enabled: Theme.animationsEnabled
                        ColorAnimation { duration: Theme.durFast }
                    }

                    Text {
                        id: confirmLabel
                        anchors.centerIn: parent
                        text: i18n.t("nest_confirm")
                        color: "#FFFFFF"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.sizeCaption
                        font.bold: true
                    }

                    MouseArea {
                        id: confirmArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: control.confirm()
                    }
                }
            }
        }
    }
}
