"""Иконка в системном трее и меню управления."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon, QWidget

from fly_pet import __version__
from fly_pet.paths import resource_dir
from fly_pet.phrases import pick

if TYPE_CHECKING:
    from fly_pet.locomotion import Locomotion
    from fly_pet.needs import Needs
    from fly_pet.state import PetState
    from fly_pet.window import FlyWindow

logger = logging.getLogger("fly_pet")


class TrayController(QObject):
    """QSystemTrayIcon с русским меню; без трея — только предупреждение в лог."""

    def __init__(
        self,
        *,
        window: FlyWindow,
        needs: Needs,
        state: PetState,
        on_quit: Callable[[], None],
        on_demo_eat: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._needs = needs
        self._state = state
        self._on_quit = on_quit
        self._on_demo_eat = on_demo_eat
        self._tray: QSystemTrayIcon | None = None
        self._act_hide: QAction | None = None
        self._act_hold: QAction | None = None
        self._act_sleep: QAction | None = None
        self._act_no_scare: QAction | None = None

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("системный трей недоступен — работаем без иконки в трее")
            return

        icon_path = resource_dir() / "assets" / "fly_pet.png"
        icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
        self._tray = QSystemTrayIcon(icon, parent)
        self._tray.setToolTip("Муха-питомец")

        menu = QMenu(parent)
        menu.aboutToShow.connect(self._sync_labels)
        self._act_hide = menu.addAction("Спрятать муху")
        self._act_hide.triggered.connect(self._toggle_hide)

        self._act_hold = menu.addAction("Стоять на месте")
        self._act_hold.setCheckable(True)
        self._act_hold.triggered.connect(self._toggle_hold)

        self._act_sleep = menu.addAction("Спать")
        self._act_sleep.triggered.connect(self._toggle_sleep)

        self._act_no_scare = menu.addAction("Не бояться курсора")
        self._act_no_scare.setCheckable(True)
        loco = self._locomotion()
        if loco is not None:
            self._act_no_scare.setChecked(not loco.scare_cursor_enabled())
        self._act_no_scare.triggered.connect(self._toggle_scare)

        menu.addSeparator()
        act_demo = menu.addAction("Тест: создать файл и съесть")
        act_demo.triggered.connect(self._demo_eat)
        act_report = menu.addAction("Отчёт о работе")
        act_report.triggered.connect(self._report)
        act_about = menu.addAction("О программе")
        act_about.triggered.connect(self._about)
        menu.addSeparator()
        act_quit = menu.addAction("Выход")
        act_quit.triggered.connect(self._quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()
        self._sync_labels()

    def _locomotion(self) -> Locomotion | None:
        return self._window._locomotion

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_hide()

    def _toggle_hide(self) -> None:
        hidden = not self._window.is_user_hidden()
        self._window.set_user_hidden(hidden)
        self._sync_labels()

    def _toggle_hold(self) -> None:
        loco = self._locomotion()
        if loco is None or self._act_hold is None:
            return
        hold = self._act_hold.isChecked()
        loco.set_hold_still(hold)
        if hold and self._state.mode not in {"sleep", "eat"}:
            self._window.set_state("idle")
        self._sync_labels()

    def _toggle_sleep(self) -> None:
        if self._needs.mode == "sleep":
            self._needs.force_wake()
            self._state.mode = "idle"
            self._window.set_state("idle")
        else:
            self._needs.force_sleep()
            self._state.mode = "sleep"
            self._window.set_state("sleep")
        self._sync_labels()

    def _toggle_scare(self) -> None:
        loco = self._locomotion()
        if loco is None or self._act_no_scare is None:
            return
        # Галочка «Не бояться» = scare выключен
        loco.set_scare_cursor(not self._act_no_scare.isChecked())

    def _demo_eat(self) -> None:
        if self._on_demo_eat is not None:
            self._on_demo_eat()

    def _report(self) -> None:
        phrase = pick("report")
        if phrase:
            self._window.say(phrase)

    def _about(self) -> None:
        QMessageBox.about(
            None,
            "О программе",
            (
                f"Муха-питомец (fly-pet) {__version__}\n\n"
                "Прозрачный питомец на рабочем столе. "
                "Управление — через иконку в трее."
            ),
        )

    def _quit(self) -> None:
        self._on_quit()

    def _sync_labels(self) -> None:
        if self._act_hide is not None:
            self._act_hide.setText(
                "Показать муху" if self._window.is_user_hidden() else "Спрятать муху"
            )
        if self._act_sleep is not None:
            self._act_sleep.setText(
                "Проснуться" if self._needs.mode == "sleep" else "Спать"
            )
        loco = self._locomotion()
        if self._act_hold is not None and loco is not None:
            self._act_hold.setChecked(loco.is_hold_still())
        if self._act_no_scare is not None and loco is not None:
            self._act_no_scare.setChecked(not loco.scare_cursor_enabled())

    def hide(self) -> None:
        if self._tray is not None:
            self._tray.hide()
