import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import MihomoDesktop

// The override editor (DESIGN.md "对话框 → 编辑对话框") for the global override or one
// profile's. Saving has the host validate the override first: a rejection stays here, in
// the editor's error state; the page closes the dialog once the override is stored.
AppDialog {
    id: dialog

    required property ProfilesViewModel profiles

    function save() {
        if (!profiles.overrideSaving)
            profiles.saveOverride(editor.text)
    }

    title: profiles.overrideTitle
    wide: true
    defaultButton: saveButton
    initialFocus: editor

    onAboutToShow: editor.text = profiles.overrideText

    CodeEditor {
        id: editor

        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredHeight: Theme.editorMinHeight
        error: dialog.profiles.overrideError !== ""
        readOnly: dialog.profiles.overrideSaving
        placeholderText: "# 例：在规则最前面加一条直连规则\nprepend-rules:\n  - DOMAIN-SUFFIX,example.com,DIRECT"
        Accessible.name: dialog.title
        onSubmitted: dialog.save()
    }

    UiText {
        Layout.fillWidth: true
        visible: text !== ""
        text: dialog.profiles.overrideError
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }

    buttons: [
        AppButton {
            text: "取消"
            onClicked: dialog.reject()
        },
        AppButton {
            id: saveButton

            text: dialog.profiles.overrideSaving ? "正在保存…" : "保存"
            variant: "primary"
            enabled: !dialog.profiles.overrideSaving
            onClicked: dialog.save()
        }
    ]
}
