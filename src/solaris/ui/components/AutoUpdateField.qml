pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts

// A form value: turning updates off keeps the interval typed, in the form and once saved.
ColumnLayout {
    id: field

    required property int defaultHours
    property bool allowSuggestion: false
    readonly property bool suggested: allowSuggestion && interval.text === ""
    property alias checked: toggle.checked
    property alias intervalText: interval.text
    readonly property bool acceptableInput: !checked || suggested || (interval.acceptableInput && Number.isInteger(Number(interval.text)))
    // The interval typed, 0 when it is not a whole number of hours; kept while updates are
    // off, so that turning them on again brings it back (Profile.auto_update_hours).
    readonly property int hours: interval.acceptableInput && Number.isInteger(Number(interval.text)) ? Number(interval.text) : 0
    signal accepted

    spacing: Theme.space2

    function setInterval(enabled: bool, hours: int) {
        checked = enabled
        intervalText = allowSuggestion && hours === 0 ? "" : String(hours > 0 ? hours : defaultHours)
    }

    RowLayout {
        spacing: Theme.space3

        AppCheckBox {
            id: toggle
            text: qsTr("自动更新")
        }

        UiText {
            text: field.allowSuggestion ? qsTr("更新间隔（小时）") : qsTr("每")
            role: Theme.type.bodySm
            color: Theme.textSecondary
            opacity: field.checked ? 1 : Theme.disabledOpacity
        }

        AppTextField {
            id: interval

            Layout.preferredWidth: 80
            text: field.allowSuggestion ? "" : String(field.defaultHours)
            placeholderText: field.allowSuggestion ? qsTr("自动") : ""
            enabled: field.checked
            inputMethodHints: Qt.ImhDigitsOnly
            validator: IntValidator { bottom: 1 }
            error: field.checked && !field.acceptableInput
            Accessible.name: qsTr("更新间隔（小时）")
            onAccepted: field.accepted()
        }

        UiText {
            text: qsTr("小时", "", Number(interval.text) === 1 ? 1 : 2)
            visible: !field.allowSuggestion
            role: Theme.type.bodySm
            color: Theme.textSecondary
            opacity: field.checked ? 1 : Theme.disabledOpacity
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: field.checked && field.suggested
        text: qsTr("使用订阅建议的间隔，未提供时每 %1 小时更新").arg(field.defaultHours)
        role: Theme.type.caption
        color: Theme.textSecondary
        wrapMode: Text.Wrap
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
