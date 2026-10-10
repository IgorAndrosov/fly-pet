"""Демо-поедание тестового файла на рабочем столе (кнопка трея / --demo-eat)."""

from __future__ import annotations

import logging
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer

from fly_pet.desktop_icons import (
    find_icon_by_filename,
    icon_center_screen,
    notify_shell_create,
    user_desktop_path,
)
from fly_pet.phrases import pick

if TYPE_CHECKING:
    from fly_pet.locomotion import Locomotion
    from fly_pet.state import PetState, StateStore
    from fly_pet.window import FlyWindow

logger = logging.getLogger("fly_pet")

DEMO_FILENAME = "муха-демо.txt"
DEMO_CONTENT = (
    "Это тестовый файл для демо поедания мухой.\n"
    "Можно спокойно переносить в карантин — не жалко.\n"
)
_CHEW_SEC = (1.5, 2.5)
_ICON_WAIT_MS = 200
_ICON_WAIT_MAX_MS = 5000
_LAND_ABOVE_PX = 12


def quarantine_path(data_dir: Path, filename: str) -> Path:
    """Путь в ``_data/trash/``; при коллизии — суффикс с UTC-меткой."""
    trash = Path(data_dir) / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return trash / f"{stem}_{stamp}{suffix}"


def move_to_quarantine(src: Path, data_dir: Path) -> Path:
    """Перенести файл в карантин (без удаления)."""
    dest = quarantine_path(data_dir, src.name)
    shutil.move(str(src), str(dest))
    return dest


def create_demo_file(desktop: Path) -> Path:
    """Создать ``муха-демо.txt`` на рабочем столе."""
    desktop.mkdir(parents=True, exist_ok=True)
    path = desktop / DEMO_FILENAME
    path.write_text(DEMO_CONTENT, encoding="utf-8")
    return path


class DemoEatController(QObject):
    """Один код-путь: трей и ``--demo-eat``."""

    def __init__(
        self,
        *,
        window: FlyWindow,
        state: PetState,
        state_store: StateStore,
        data_dir: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._state = state
        self._store = state_store
        self._data_dir = Path(data_dir)
        self._busy = False
        self._demo_path: Path | None = None
        self._poll: QTimer | None = None
        self._waited_ms = 0
        self._chew_timer: QTimer | None = None

    def start(self) -> None:
        """Запуск демо (пользовательская кнопка — не dry_run)."""
        if self._busy:
            logger.info("demo eat уже идёт — пропуск")
            return
        self._busy = True
        logger.info("demo eat (по кнопке)")
        try:
            desktop = user_desktop_path()
        except Exception as exc:  # noqa: BLE001
            logger.warning("не удалось получить путь Desktop: %s", exc)
            self._say("eat_error")
            self._busy = False
            return

        existing = desktop / DEMO_FILENAME
        if existing.is_file():
            try:
                moved = move_to_quarantine(existing, self._data_dir)
                logger.info("старый демо-файл убран в карантин: %s", moved)
            except OSError as exc:
                logger.warning("не удалось убрать старый демо-файл: %s", exc)

        try:
            path = create_demo_file(desktop)
        except OSError as exc:
            logger.warning("не удалось создать демо-файл: %s", exc)
            self._say("eat_error")
            self._busy = False
            return

        self._demo_path = path
        notify_shell_create(path)
        logger.info("создал файл %s", path)
        self._say("eat_found")
        self._waited_ms = 0
        self._poll = QTimer(self)
        self._poll.setInterval(_ICON_WAIT_MS)
        self._poll.timeout.connect(self._poll_icon)
        self._poll.start()

    def _locomotion(self) -> Locomotion | None:
        return self._window._locomotion

    def _say(self, event: str) -> None:
        phrase = pick(event)
        if phrase:
            self._window.say(phrase)

    def _poll_icon(self) -> None:
        self._waited_ms += _ICON_WAIT_MS
        found = None
        try:
            found = find_icon_by_filename(DEMO_FILENAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("опрос иконки: %s", exc)
        if found is not None:
            if self._poll is not None:
                self._poll.stop()
                self._poll = None
            self._on_icon_found(found)
            return
        if self._waited_ms >= _ICON_WAIT_MAX_MS:
            if self._poll is not None:
                self._poll.stop()
                self._poll = None
            logger.warning("иконка демо-файла не найдена за %s мс", self._waited_ms)
            self._say("eat_no_icon")
            self._busy = False

    def _on_icon_found(self, icon: dict) -> None:
        cx, cy = icon_center_screen(icon)
        logger.info(
            "нашёл иконку «%s» экран=(%s, %s) клетка=%s центр=(%s, %s)",
            icon.get("name"),
            icon.get("screen_x"),
            icon.get("screen_y"),
            icon.get("cell"),
            cx,
            cy,
        )
        loco = self._locomotion()
        if loco is None:
            logger.warning("локомоция выключена — жевание на месте")
            self._begin_chew()
            return
        pet_w = int(self._window.width())
        pet_h = int(self._window.height())
        tx = int(cx - pet_w // 2)
        ty = int(cy - pet_h // 2 - _LAND_ABOVE_PX)
        loco.fly_to(tx, ty, on_arrive=self._begin_chew)

    def _begin_chew(self) -> None:
        logger.info("прожевываю демо-файл")
        self._state.mode = "eat"
        self._window.set_state("chew")
        self._say("eat_chew")
        chew_ms = int(round(random.uniform(*_CHEW_SEC) * 1000))
        self._chew_timer = QTimer(self)
        self._chew_timer.setSingleShot(True)
        self._chew_timer.timeout.connect(self._finish_chew)
        self._chew_timer.start(chew_ms)

    def _finish_chew(self) -> None:
        self._chew_timer = None
        path = self._demo_path
        if path is not None and path.is_file():
            try:
                dest = move_to_quarantine(path, self._data_dir)
                logger.info("перенёс в карантин: %s", dest)
            except OSError as exc:
                logger.warning("не удалось перенести в карантин: %s", exc)
                self._say("eat_error")
                self._restore_life()
                self._busy = False
                return
        else:
            logger.warning("демо-файл уже исчез до переноса")

        self._state.eaten = int(self._state.eaten) + 1
        self._state.stats["eaten"] = int(self._state.stats.get("eaten", 0)) + 1
        self._state.stats["actions_run"] = int(self._state.stats.get("actions_run", 0)) + 1
        self._state.last_action = {
            "name": "demo_eat",
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dry_run": False,
            "ok": True,
        }
        self._say("eat_done")
        logger.info(
            "демо съедено: eaten=%s stats.eaten=%s",
            self._state.eaten,
            self._state.stats.get("eaten"),
        )
        self._restore_life()
        try:
            self._store.save(self._state)
        except OSError as exc:
            logger.warning("не удалось сохранить state после демо: %s", exc)
        self._busy = False
        self._demo_path = None

    def _restore_life(self) -> None:
        loco = self._locomotion()
        if loco is not None:
            self._state.mode = loco.mode.value
            self._window.set_state("idle")
        else:
            self._state.mode = "idle"
            self._window.set_state("idle")


def wire_demo_eat(
    *,
    window: FlyWindow,
    state: PetState,
    state_store: StateStore,
    data_dir: Path,
    parent: QObject | None = None,
) -> DemoEatController:
    """Создать контроллер демо-поедания."""
    return DemoEatController(
        window=window,
        state=state,
        state_store=state_store,
        data_dir=data_dir,
        parent=parent,
    )
