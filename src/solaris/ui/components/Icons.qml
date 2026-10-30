pragma Singleton
import QtQuick

// Stroke icons on a 24-unit grid, drawn by Icon.qml (DESIGN.md "Shapes"). `fills` holds the
// filled parts of the few that have one: the corona mark's core.
QtObject {
    readonly property var paths: ({
        "overview": "M4.5 3h4A1.5 1.5 0 0 1 10 4.5v4A1.5 1.5 0 0 1 8.5 10h-4A1.5 1.5 0 0 1 3 8.5v-4A1.5 1.5 0 0 1 4.5 3zM15.5 3h4A1.5 1.5 0 0 1 21 4.5v4A1.5 1.5 0 0 1 19.5 10h-4A1.5 1.5 0 0 1 14 8.5v-4A1.5 1.5 0 0 1 15.5 3zM4.5 14h4A1.5 1.5 0 0 1 10 15.5v4A1.5 1.5 0 0 1 8.5 21h-4A1.5 1.5 0 0 1 3 19.5v-4A1.5 1.5 0 0 1 4.5 14zM15.5 14h4A1.5 1.5 0 0 1 21 15.5v4A1.5 1.5 0 0 1 19.5 21h-4A1.5 1.5 0 0 1 14 19.5v-4A1.5 1.5 0 0 1 15.5 14z",
        "proxies": "M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0zM3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z",
        "profiles": "M12 3L3 8l9 5 9-5-9-5zM3 13l9 5 9-5",
        "routing": "M16 3h5v5M8 3H3v5M12 22v-8.3a4 4 0 0 0-1.172-2.828L3 3M15 9l6-6",
        "grip": "M8 5a1 1 0 1 0 2 0a1 1 0 1 0-2 0zM14 5a1 1 0 1 0 2 0a1 1 0 1 0-2 0zM8 12a1 1 0 1 0 2 0a1 1 0 1 0-2 0zM14 12a1 1 0 1 0 2 0a1 1 0 1 0-2 0zM8 19a1 1 0 1 0 2 0a1 1 0 1 0-2 0zM14 19a1 1 0 1 0 2 0a1 1 0 1 0-2 0z",
        "connections": "M4 8h14l-4-4M20 16H6l4 4",
        "logs": "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01",
        "settings": "M4 7h9M17 7h3M4 17h3M11 17h9M17 7a2 2 0 1 1-4 0a2 2 0 1 1 4 0zM11 17a2 2 0 1 1-4 0a2 2 0 1 1 4 0z",
        "mark": "M18.13 6.86A8 8 0 1 1 9.26 4.48",
        "chevron": "M9 6l6 6-6 6",
        "chevronDown": "M6 9l6 6 6-6",
        "check": "M5 12l5 5 9-10",
        "more": "M11 12a1 1 0 1 0 2 0a1 1 0 1 0-2 0zM18 12a1 1 0 1 0 2 0a1 1 0 1 0-2 0zM4 12a1 1 0 1 0 2 0a1 1 0 1 0-2 0z",
        "search": "M18 11a7 7 0 1 1-14 0a7 7 0 1 1 14 0zM20 20l-3.5-3.5",
        "close": "M6 6l12 12M18 6L6 18",
        "arrowDown": "M12 5v14M19 12l-7 7-7-7",
        "arrowUp": "M12 19V5M5 12l7-7 7 7"
    })
    readonly property var fills: ({
        "mark": "M15.6 12a3.6 3.6 0 1 1-7.2 0a3.6 3.6 0 1 1 7.2 0z"
    })
}
