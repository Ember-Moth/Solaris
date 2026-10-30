pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Solaris

// The connections page's popover for the selected connection: what the table leaves out
// or cuts short, kept up to date while it is open. A connection that ends meanwhile keeps
// its last values and says it has closed.
AppPopover {
    id: popover

    required property ConnectionsViewModel connections
    readonly property var info: connections.detail
    readonly property int labelWidth: 80

    contentItem: ColumnLayout {
        spacing: Theme.space2

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space2

            DetailText {
                Layout.fillWidth: true
                text: popover.info.host ?? ""
                role: Theme.type.labelSmStrong
            }

            DetailText {
                visible: popover.connections.detailClosed
                text: qsTr("已关闭")
                role: Theme.type.caption
                color: Theme.textMuted
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.space1

            Repeater {
                model: [
                    {
                        label: qsTr("网络"),
                        value: popover.info.protocol,
                        mono: false
                    },
                    {
                        label: qsTr("来源"),
                        value: popover.info.source,
                        mono: true
                    },
                    {
                        label: qsTr("目标"),
                        value: popover.info.destination,
                        mono: true
                    },
                    {
                        label: qsTr("进程"),
                        value: popover.info.process,
                        mono: false
                    },
                    {
                        label: qsTr("规则"),
                        value: popover.info.ruleName ? qsTr("%1（%2）").arg(popover.info.ruleName).arg(popover.info.rule) : popover.info.rule,
                        mono: !popover.info.ruleName
                    },
                    {
                        label: qsTr("链路"),
                        value: popover.info.chain,
                        mono: false
                    },
                    {
                        label: qsTr("下载"),
                        value: popover.info.download + " · " + popover.info.downRate,
                        mono: true
                    },
                    {
                        label: qsTr("上传"),
                        value: popover.info.upload + " · " + popover.info.upRate,
                        mono: true
                    },
                    {
                        label: qsTr("开始时间"),
                        value: popover.info.started,
                        mono: true
                    },
                    {
                        label: qsTr("时长"),
                        value: popover.info.duration,
                        mono: true
                    }
                ]

                RowLayout {
                    id: field

                    required property var modelData

                    Layout.fillWidth: true
                    spacing: Theme.space3

                    DetailText {
                        Layout.preferredWidth: popover.labelWidth
                        text: field.modelData.label
                        role: Theme.type.caption
                        color: Theme.textMuted
                        wrapMode: Text.Wrap
                    }

                    DetailText {
                        Layout.fillWidth: true
                        text: field.modelData.value ?? ""
                        role: field.modelData.mono ? Theme.type.dataSm : Theme.type.bodySm
                        color: field.modelData.mono ? Theme.textSecondary : Theme.text
                    }
                }
            }
        }

        AppButton {
            Layout.alignment: Qt.AlignRight
            Layout.topMargin: Theme.space1
            variant: "compact"
            text: qsTr("关闭连接")
            enabled: !popover.connections.detailClosed
            onClicked: {
                popover.connections.closeConnection(popover.info.id)
                popover.close()
            }
        }
    }

    component DetailText: AccessibleText {
        Layout.alignment: Qt.AlignTop
        wrapMode: Text.WrapAnywhere
    }
}
