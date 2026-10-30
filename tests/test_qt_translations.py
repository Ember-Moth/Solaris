"""Chinese for Qt's own text that Qt's catalogs lack: the text fields' context menu
(docs/ui.md §5)."""

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import PySide6
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine
from pytestqt.qtbot import QtBot

from scripts.gen_i18n import tool
from solaris.i18n import QT_CATALOGS, TRANSLATIONS_DIR
from tests.support.i18n import ui_language

CATALOG = TRANSLATIONS_DIR / f"{QT_CATALOGS['zh']}.ts"
QT_QML = Path(PySide6.__file__).parent / "Qt" / "qml" / "QtQuick" / "Controls"
MENU = """
import QtQuick
import QtQuick.Controls.impl

Item {
    id: root

    readonly property var texts: [undo.text, redo.text, cut.text, copy.text, paste.text,
        remove.text, all.text]

    UndoAction { id: undo; editor: root }
    RedoAction { id: redo; editor: root }
    CutAction { id: cut; editor: root }
    CopyAction { id: copy; editor: root }
    PasteAction { id: paste; editor: root }
    DeleteAction { id: remove; editor: root }
    SelectAllAction { id: all; editor: root }
}
"""


def entries() -> dict[str, tuple[str, str]]:
    root = ET.parse(CATALOG).getroot()
    return {
        context.findtext("name", ""): (
            context.findtext("message/source", ""),
            context.findtext("message/translation", ""),
        )
        for context in root.iter("context")
    }


def menu_texts() -> list[str]:
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(MENU.encode(), QUrl("qrc:/menu.qml"))
    menu = component.create()
    assert menu is not None, component.errorString()
    texts = menu.property("texts")
    return [texts.property(i).toString() for i in range(texts.property("length").toInt())]


def test_the_catalog_names_qts_own_contexts() -> None:
    """Each entry is a qsTr of QtQuick.Controls.impl, whose context is the file's name, and
    the Basic style's menu uses every one of them: a Qt upgrade that changes either fails."""
    for context, (source, translation) in entries().items():
        qml = (QT_QML / "impl" / f"{context}.qml").read_text(encoding="utf-8")
        assert re.search(rf'qsTr\("{re.escape(source)}"\)', qml), context
        assert translation
    menu = (QT_QML / "Basic" / "impl" / "TextEditingContextMenu.qml").read_text(encoding="utf-8")
    assert set(re.findall(r"(\w+Action) \{", menu)) == set(entries())


def test_the_compiled_catalog_matches(tmp_path: Path) -> None:
    compiled = tmp_path / "qt.qm"
    command = [
        tool("pyside6-lrelease"),
        "-nounfinished",
        "-silent",
        str(CATALOG),
        "-qm",
        str(compiled),
    ]
    subprocess.run(command, check=True)
    assert compiled.read_bytes() == CATALOG.with_suffix(".qm").read_bytes()


def test_the_text_fields_menu_speaks_the_ui_language(qtbot: QtBot) -> None:
    with ui_language("zh"):
        assert menu_texts() == ["撤销", "重做", "剪切", "复制", "粘贴", "删除", "全选"]
        assert QCoreApplication.translate("CutAction", "Cut") == "剪切"
    with ui_language("en"):
        assert menu_texts() == ["Undo", "Redo", "Cut", "Copy", "Paste", "Delete", "Select All"]
