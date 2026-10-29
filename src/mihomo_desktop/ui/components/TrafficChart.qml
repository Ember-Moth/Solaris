pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Shapes

// Two 1.5px lines over faint reference lines; values are 0–1, oldest first.
Item {
    id: chart

    property var down: []
    property var up: []

    implicitHeight: 112
    Accessible.role: Accessible.Graphic
    Accessible.name: "近 40 秒下载与上传速率"

    function points(values: var): var {
        const count = values.length
        const result = []
        for (let i = 0; i < count; i++) {
            const x = count > 1 ? i * width / (count - 1) : 0
            result.push(Qt.point(x, height - 4 - values[i] * (height - 8)))
        }
        return result
    }

    Repeater {
        model: 3

        Rectangle {
            required property int index
            width: chart.width
            height: 1
            y: Math.round(chart.height * (index + 1) / 4)
            color: Theme.divider
        }
    }

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            strokeColor: Theme.primary
            strokeWidth: 1.5
            fillColor: "transparent"
            joinStyle: ShapePath.RoundJoin
            capStyle: ShapePath.RoundCap

            PathPolyline {
                path: chart.points(chart.down)
            }
        }

        ShapePath {
            strokeColor: Theme.chartUpload
            strokeWidth: 1.5
            fillColor: "transparent"
            joinStyle: ShapePath.RoundJoin
            capStyle: ShapePath.RoundCap

            PathPolyline {
                path: chart.points(chart.up)
            }
        }
    }
}
