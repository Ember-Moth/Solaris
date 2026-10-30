pragma Singleton
import QtQuick

// Colors for levels the ViewModels report as strings.
QtObject {
    function latencyColor(level: string): color {
        if (level === "good")
            return Theme.success
        if (level === "fair")
            return Theme.warning
        if (level === "poor")
            return Theme.latencyPoor
        return Theme.textMuted
    }
}
