import pytest
from PySide6.QtCore import QCoreApplication, Qt
from pytestqt.qtbot import QtBot

from mihomo_desktop.viewmodels.appearance import APPEARANCE_ENV, Appearance, resolve_mode
from tests.support.appearance import SystemColorScheme

LIGHT, DARK = Qt.ColorScheme.Light, Qt.ColorScheme.Dark


@pytest.mark.parametrize(
    ("preference", "environ", "mode"),
    [
        ("", {}, "system"),
        ("dark", {}, "dark"),
        ("", {APPEARANCE_ENV: "light"}, "light"),
        ("dark", {APPEARANCE_ENV: "light"}, "dark"),
        (" Dark ", {}, "dark"),
        ("sepia", {APPEARANCE_ENV: "dim"}, "system"),
    ],
)
def test_mode_comes_from_the_preference_then_the_environment(
    preference: str, environ: dict[str, str], mode: str
) -> None:
    assert resolve_mode(preference, environ) == mode


def test_system_mode_follows_the_system_while_running(qapp: QCoreApplication, qtbot: QtBot) -> None:
    system = SystemColorScheme(LIGHT)
    appearance = Appearance(hints=system)
    assert (appearance.property("mode"), appearance.property("dark")) == ("system", False)
    assert system.requested is None

    with qtbot.waitSignal(appearance.darkChanged):
        system.switch(DARK)
    assert appearance.property("dark")
    with qtbot.waitSignal(appearance.darkChanged):
        system.switch(LIGHT)
    assert not appearance.property("dark")


@pytest.mark.parametrize(("mode", "scheme"), [("light", LIGHT), ("dark", DARK)])
def test_a_fixed_mode_ignores_the_system(
    qapp: QCoreApplication, qtbot: QtBot, mode: str, scheme: Qt.ColorScheme
) -> None:
    system = SystemColorScheme(DARK if scheme == LIGHT else LIGHT)
    appearance = Appearance(mode, hints=system)
    assert appearance.property("dark") == (mode == "dark")
    assert system.requested == scheme  # the native frame follows the fixed mode
    with qtbot.assertNotEmitted(appearance.darkChanged):
        system.switch(LIGHT)
        system.switch(DARK)


def test_switching_modes_at_runtime(qapp: QCoreApplication, qtbot: QtBot) -> None:
    system = SystemColorScheme(DARK)
    appearance = Appearance("light", hints=system)
    assert not appearance.property("dark")

    with qtbot.waitSignals([appearance.modeChanged, appearance.darkChanged]):
        appearance.setMode("system")
    assert (appearance.property("mode"), appearance.property("dark")) == ("system", True)
    assert system.requested is None

    with qtbot.waitSignal(appearance.modeChanged), qtbot.assertNotEmitted(appearance.darkChanged):
        appearance.setMode("dark")
    with qtbot.assertNotEmitted(appearance.modeChanged):
        appearance.setMode("dark")


def test_unknown_modes_are_rejected(qapp: QCoreApplication) -> None:
    with pytest.raises(ValueError, match="sepia"):
        Appearance("sepia", hints=SystemColorScheme())
    appearance = Appearance(hints=SystemColorScheme())
    with pytest.raises(ValueError, match="auto"):
        appearance.setMode("auto")
    assert appearance.property("mode") == "system"
