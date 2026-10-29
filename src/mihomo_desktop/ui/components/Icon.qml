import QtQuick
import QtQuick.Shapes

Item {
    id: icon

    property string name
    property color color: Theme.textMuted
    property real size: 18

    implicitWidth: size
    implicitHeight: size
    Accessible.ignored: true

    Shape {
        width: 24
        height: 24
        scale: icon.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            strokeColor: icon.color
            strokeWidth: 2
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin

            PathSvg {
                path: Icons.paths[icon.name] ?? ""
            }
        }
    }
}
