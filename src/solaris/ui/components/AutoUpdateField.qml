pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts

// A form value: disabling updates keeps the draft interval until the form closes.
ColumnLayout {
    id: field

    required property int defaultHours
    property alias checked: toggle.checked
    property alias intervalText: interval.text
    readonly property bool acceptableInput: !checked || (interval.acceptableInput && Number.isInteger(Number(interval.text)))
    readonly property int hours: checked && acceptableInput ? Number(interval.text) : 0
    signal accepted

    spacing: Theme.space2

    function setInterval(hours: int) {
        checked = hours > 0
        intervalText = String(hours > 0 ? hours : defaultHours)
    }

    RowLayout {
        spacing: Theme.space3

        AppCheckBox {
            id: toggle
            text: qsTr("自动更新")
        }

        UiText {
            text: qsTr("每")
            role: Theme.type.bodySm
            color: Theme.textSecondary
            opacity: field.checked ? 1 : Theme.disabledOpacity
        }

        AppTextField {
            id: interval

            Layout.preferredWidth: 80
            text: String(field.defaultHours)
            enabled: field.checked
            inputMethodHints: Qt.ImhDigitsOnly
            validator: IntValidator { bottom: 1 }
            error: field.checked && !field.acceptableInput
            Accessible.name: qsTr("更新间隔（小时）")
            onAccepted: field.accepted()
        }

        UiText {
            text: qsTr("小时", "", Number(interval.text) === 1 ? 1 : 2)
            role: Theme.type.bodySm
            color: Theme.textSecondary
            opacity: field.checked ? 1 : Theme.disabledOpacity
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: !field.acceptableInput
        text: qsTr("更新间隔必须为正整数小时")
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }
}
