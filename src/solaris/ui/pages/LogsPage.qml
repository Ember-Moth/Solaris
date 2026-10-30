pragma ComponentBehavior: Bound
import QtCore
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Solaris
import "../components"

Item {
    id: page

    required property LogsViewModel logs

    // The search and the level filter share the header row while it has room for them
    // beside the title and status; otherwise they get a row of their own under it.
    readonly property bool filtersInHeader: header.width >= filters.implicitWidth + actions.implicitWidth + 280
    readonly property var levelColors: ({
            "debug": Theme.textMuted,
            "info": Theme.info,
            "warning": Theme.warning,
            "error": Theme.danger
        })

    function scrollBy(delta: real) {
        const top = list.originY
        const bottom = Math.max(top, list.originY + list.contentHeight - list.height)
        list.contentY = Math.min(Math.max(list.contentY + delta, top), bottom)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: Theme.pageTop
        anchors.leftMargin: Theme.pageX
        anchors.rightMargin: Theme.pageX
        anchors.bottomMargin: Theme.pageBottom
        spacing: Theme.space5

        PageHeader {
            id: header

            Layout.fillWidth: true
            title: qsTr("日志")
            detail: page.logs.status
            detailColor: page.logs.statusTone === "danger" ? Theme.danger : page.logs.statusTone === "warning" ? Theme.warning : Theme.textMuted

            Item {
                id: headerSlot

                visible: page.filtersInHeader
                implicitWidth: filters.implicitWidth
                implicitHeight: filters.implicitHeight
            }

            RowLayout {
                id: actions

                spacing: Theme.space2

                AppButton {
                    id: pauseButton

                    // As wide as its longer label, so pausing does not move the others.
                    Layout.preferredWidth: Math.max(implicitWidth, resumeMetrics.width + leftPadding + rightPadding)
                    text: page.logs.paused ? qsTr("继续") : qsTr("暂停")
                    onClicked: page.logs.togglePause()

                    TextMetrics {
                        id: resumeMetrics

                        font: pauseButton.font
                        text: page.logs.paused ? qsTr("暂停") : qsTr("继续")
                    }
                }

                AppButton {
                    text: qsTr("清空")
                    onClicked: page.logs.clear()
                }

                AppButton {
                    id: exportButton

                    text: qsTr("导出")
                    enabled: page.logs.count > 0
                    onClicked: {
                        exportDialog.selectedFile = exportDialog.currentFolder + "/" + page.logs.exportName
                        exportDialog.open()
                    }
                }
            }
        }

        Item {
            id: rowSlot

            Layout.fillWidth: true
            visible: !page.filtersInHeader
            implicitHeight: filters.implicitHeight
        }

        RowLayout {
            id: filters

            parent: page.filtersInHeader ? headerSlot : rowSlot
            spacing: Theme.space2

            SearchField {
                Layout.preferredWidth: Math.max(200, placeholderWidth)
                placeholderText: qsTr("搜索日志")
                text: page.logs.query
                onTextEdited: page.logs.setQuery(text)
            }

            SegmentedControl {
                options: page.logs.levelOptions
                current: page.logs.level
                accessibleName: qsTr("最低日志级别")
                onPicked: value => page.logs.setLevel(value)
            }
        }

        // log-line: time, level and message in three columns. The view follows new lines
        // while it is scrolled to the end and stays put once the reader scrolls up.
        Control {
            id: panel

            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 1
            focusPolicy: Qt.StrongFocus
            Accessible.role: Accessible.List
            Accessible.name: qsTr("日志内容")

            Keys.onPressed: event => {
                const line = Math.round(Theme.type.dataSm.fontSize * Theme.type.dataSm.lineHeight)
                if (event.key === Qt.Key_Up)
                    page.scrollBy(-line)
                else if (event.key === Qt.Key_Down)
                    page.scrollBy(line)
                else if (event.key === Qt.Key_PageUp)
                    page.scrollBy(-(list.height - line))
                else if (event.key === Qt.Key_PageDown || event.key === Qt.Key_Space)
                    page.scrollBy(list.height - line)
                else if (event.key === Qt.Key_Home)
                    list.positionViewAtBeginning()
                else if (event.key === Qt.Key_End)
                    list.positionViewAtEnd()
                else
                    return
                event.accepted = true
            }

            background: Rectangle {
                radius: Theme.radiusMd
                color: Theme.surface
                border.width: 1
                border.color: Theme.border

                FocusRing {
                    targetRadius: Theme.radiusMd
                    visible: panel.visualFocus
                }
            }

            contentItem: ListView {
                id: list

                property bool following: true
                property bool positioning: false

                clip: true
                topMargin: 6
                bottomMargin: 6
                model: page.logs.model
                boundsBehavior: Flickable.StopAtBounds
                reuseItems: true

                onContentYChanged: {
                    if (!positioning)
                        following = atYEnd
                }
                onCountChanged: {
                    if (count === 0)
                        following = true
                    if (!following)
                        return
                    positioning = true
                    positionViewAtEnd()
                    positioning = false
                }

                ScrollBar.vertical: ScrollBar {}

                delegate: Item {
                    id: line

                    required property string time
                    required property string level
                    required property string label
                    required property string message

                    width: list.width
                    implicitHeight: columns.implicitHeight + 6
                    Accessible.role: Accessible.ListItem
                    Accessible.name: line.time + " " + line.label + " " + line.message

                    RowLayout {
                        id: columns

                        x: Theme.space4
                        y: 3
                        width: parent.width - 2 * Theme.space4
                        spacing: Theme.space3

                        LogText {
                            Layout.preferredWidth: 64
                            text: line.time
                            color: Theme.textMuted
                        }

                        LogText {
                            Layout.preferredWidth: 48
                            text: line.label
                            color: page.levelColors[line.level]
                        }

                        LogText {
                            Layout.fillWidth: true
                            text: line.message
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }

            AccessibleText {
                parent: panel
                x: panel.leftPadding + Theme.space4
                y: panel.topPadding + Theme.space6
                visible: list.count === 0
                text: page.logs.emptyText
                role: Theme.type.bodySm
                color: Theme.textMuted
            }
        }
    }

    // data-sm at DESIGN.md's log line height, 1.6 times the font size, in all three columns:
    // the time and level do not wrap, but their first line lines up with the message's.
    component LogText: UiText {
        Layout.alignment: Qt.AlignTop
        role: Theme.type.dataSm
        lineHeightMode: Text.FixedHeight
        lineHeight: Math.round(Theme.type.dataSm.fontSize * Theme.type.dataSm.lineHeight)
    }

    FileDialog {
        id: exportDialog

        title: qsTr("导出日志")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "log"
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        nameFilters: [qsTr("日志文件 (*.log)"), qsTr("文本文件 (*.txt)")]
        onAccepted: page.logs.exportTo(selectedFile.toString())
    }
}
