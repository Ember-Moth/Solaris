pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Solaris
import "../../components"

// The rule test, the search and the target filter above the plan's rules (routing.md §4.3).
ColumnLayout {
    id: tools

    required property RoutingViewModel routing
    readonly property PlanRules rules: routing.planRules

    spacing: Theme.space2

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.space2

        SearchField {
            id: testField

            Layout.fillWidth: true
            Layout.minimumWidth: 200
            implicitHeight: Theme.controlSm
            // MatchRule needs a core: on macOS, while the tunnel runs.
            enabled: tools.routing.coreAvailable
            placeholderText: enabled ? qsTr("输入域名或 IP 测试") : qsTr("连接后可测试域名或 IP")
            accessibleName: qsTr("测试域名或 IP")
            text: enabled ? tools.rules.testText : ""
            onTextEdited: tools.rules.setTestText(text)
            onAccepted: tools.rules.runTest()
        }

        SearchField {
            Layout.preferredWidth: Math.max(160, placeholderWidth)
            implicitHeight: Theme.controlSm
            placeholderText: qsTr("搜索规则")
            text: tools.rules.query
            onTextEdited: tools.rules.setQuery(text)
        }

        AppSelect {
            Layout.preferredWidth: 160
            options: tools.rules.targetFilterOptions
            current: tools.rules.targetFilter
            displayText: tools.rules.targetFilterLabel
            accessibleName: qsTr("按目标筛选")
            onPicked: value => tools.rules.setTargetFilter(value)
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: text !== ""
        text: tools.rules.testResult
        role: Theme.type.bodySm
        color: tools.rules.testFailed ? Theme.danger : Theme.textSecondary
        wrapMode: Text.Wrap
        Accessible.role: Accessible.StaticText
    }
}
