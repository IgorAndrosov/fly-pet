"""Демо-поедание тестового файла на рабочем столе (кнопка трея / --demo-eat)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QRect, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from fly_pet.desktop_icons import (
    find_icon_by_filename,
    notify_shell_create,
    notify_shell_delete,
    user_desktop_path,
)
from fly_pet.eat_overlay import (
    Bite,
    IconEatOverlay,
    bite_interval_sec,
    count_opaque,
    extract_icon_layers_from_cells,
    grab_icon_cell,
    icon_cell_rect,
    plan_bites,
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
_ICON_WAIT_MS = 200
_ICON_WAIT_MAX_MS = 5000
_GONE_WAIT_MS = 150
_GONE_WAIT_MAX_MS = 3000
_DIFF_RETRY_MS = 350
_DIFF_RETRY_MAX = 12
_MIN_OPAQUE = 200
_PAINT_WAIT_MS = 700


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
    import shutil

    shutil.move(str(src), str(dest))
    notify_shell_delete(src)
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
        self._bite_timer: QTimer | None = None
        self._bg_cell: QImage | None = None
        self._overlay: IconEatOverlay | None = None
        self._bites: list[Bite] = []
        self._bite_i = 0
        self._cell: QRect | None = None
        self._icon: dict | None = None
        self._eat_t0 = 0.0
        self._interval_sec = 0.3
        self._diff_tries = 0
        self._gone_waited = 0
        self._phase = "idle"  # locate | empty_bg | appear | eat

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._teardown_overlay)

    def start(self) -> None:
        """Запуск демо (пользовательская кнопка — не dry_run)."""
        if self._busy:
            logger.info("demo eat уже идёт — пропуск")
            return
        self._busy = True
        self._teardown_overlay()
        self._phase = "locate"
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
        logger.info("создал файл %s (фаза: клетка)", path)
        self._say("eat_found")
        self._start_icon_poll(self._on_icon_for_cell)

    def _start_icon_poll(self, callback) -> None:
        self._waited_ms = 0
        self._icon_cb = callback
        if self._poll is not None:
            self._poll.stop()
        self._poll = QTimer(self)
        self._poll.setInterval(_ICON_WAIT_MS)
        self._poll.timeout.connect(self._poll_icon)
        self._poll.start()

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
            self._icon_cb(found)
            return
        if self._waited_ms >= _ICON_WAIT_MAX_MS:
            if self._poll is not None:
                self._poll.stop()
                self._poll = None
            logger.warning("иконка демо-файла не найдена за %s мс", self._waited_ms)
            self._say("eat_no_icon")
            self._teardown_overlay()
            self._busy = False

    def _on_icon_for_cell(self, icon: dict) -> None:
        """Фаза 1: знаем клетку → убрать файл → снять пустой фон → вернуть файл."""
        cell = icon_cell_rect(icon)
        self._cell = cell
        self._icon = dict(icon)
        logger.info(
            "нашёл иконку «%s» экран=(%s, %s) клетка=%sx%s client=(%s,%s)",
            icon.get("name"),
            cell.x(),
            cell.y(),
            cell.width(),
            cell.height(),
            icon.get("client_x"),
            icon.get("client_y"),
        )
        path = self._demo_path
        if path is not None and path.is_file():
            try:
                move_to_quarantine(path, self._data_dir)
            except OSError as exc:
                logger.warning("не удалось временно убрать файл для фона: %s", exc)
                self._say("eat_error")
                self._busy = False
                return
        self._phase = "empty_bg"
        self._gone_waited = 0
        self._poll = QTimer(self)
        self._poll.setInterval(_GONE_WAIT_MS)
        self._poll.timeout.connect(self._wait_gone_then_grab_bg)
        self._poll.start()
        self._wait_gone_then_grab_bg()

    def _wait_gone_then_grab_bg(self) -> None:
        if not self._busy:
            return
        still = None
        try:
            still = find_icon_by_filename(DEMO_FILENAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ожидание пустой клетки: %s", exc)
        if still is not None:
            self._gone_waited += _GONE_WAIT_MS
            if self._gone_waited < _GONE_WAIT_MAX_MS:
                return
            logger.info("иконка не исчезла вовремя — снимаю фон поверх")
        if self._poll is not None:
            self._poll.stop()
            self._poll = None

        if self._cell is None or self._icon is None:
            self._say("eat_error")
            self._busy = False
            return
        # Пауза: explorer перерисует пустую клетку.
        QTimer.singleShot(_PAINT_WAIT_MS, self._grab_bg_and_recreate)

    def _grab_bg_and_recreate(self) -> None:
        if not self._busy or self._icon is None:
            return
        try:
            self._bg_cell = grab_icon_cell(self._icon)
        except Exception as exc:  # noqa: BLE001
            logger.warning("grab пустой клетки: %s", exc)
            self._bg_cell = QImage()
        logger.info(
            "фон клетки снят %sx%s",
            0 if self._bg_cell is None else self._bg_cell.width(),
            0 if self._bg_cell is None else self._bg_cell.height(),
        )

        try:
            desktop = user_desktop_path()
            path = create_demo_file(desktop)
        except OSError as exc:
            logger.warning("не удалось вернуть демо-файл: %s", exc)
            self._say("eat_error")
            self._busy = False
            return
        self._demo_path = path
        notify_shell_create(path)
        logger.info("вернул файл %s (фаза: иконка)", path)
        self._phase = "appear"
        self._start_icon_poll(self._on_icon_for_eat)

    def _on_icon_for_eat(self, icon: dict) -> None:
        """Фаза 2: иконка снова на месте — diff и оверлей."""
        # клетка могла чуть сдвинуться
        self._cell = icon_cell_rect(icon)
        self._icon = dict(icon)
        self._diff_tries = 0
        # explorer дорисовывает глиф после появления в ListView
        QTimer.singleShot(_PAINT_WAIT_MS, self._try_build_overlay)

    def _try_build_overlay(self) -> None:
        if not self._busy or self._cell is None or self._icon is None:
            return
        cell = self._cell
        try:
            fg = grab_icon_cell(self._icon)
        except Exception as exc:  # noqa: BLE001
            logger.warning("grab клетки с иконкой: %s", exc)
            fg = QImage()

        bg = self._bg_cell if self._bg_cell is not None else QImage()
        bg_out, icon_img = extract_icon_layers_from_cells(bg, fg)
        opaque = count_opaque(icon_img)
        self._diff_tries += 1
        if opaque < _MIN_OPAQUE and self._diff_tries < _DIFF_RETRY_MAX:
            logger.info(
                "diff иконки слабый (%s px) — повтор %s/%s",
                opaque,
                self._diff_tries,
                _DIFF_RETRY_MAX,
            )
            QTimer.singleShot(_DIFF_RETRY_MS, self._try_build_overlay)
            return

        if opaque < _MIN_OPAQUE or icon_img.isNull() or bg_out.isNull():
            logger.warning("иконку не выделил (opaque=%s) — жевание без оверлея", opaque)
            self._bites = [
                Bite(x=cell.width() // 2, y=max(1, cell.height() // 3), r=12, angle_deg=90.0)
            ]
            self._overlay = None
            self._fly_to_and_eat()
            return

        self._bites = plan_bites(icon_img)
        if not self._bites:
            self._bites = [
                Bite(x=cell.width() // 2, y=cell.height() // 2, r=12, angle_deg=0.0)
            ]
        self._overlay = IconEatOverlay(cell, bg_out, icon_img)
        self._overlay.show_click_through()
        self._raise_z()
        logger.info(
            "оверлей показан, укусов=%s, opaque=%s",
            len(self._bites),
            opaque,
        )
        self._fly_to_and_eat()

    def _fly_to_and_eat(self) -> None:
        self._interval_sec = bite_interval_sec(len(self._bites) or 1)
        first = self._bites[0] if self._bites else None
        loco = self._locomotion()
        cell = self._cell
        if first is None or loco is None or cell is None:
            self._begin_eat_loop()
            return
        pet_w = int(self._window.width())
        pet_h = int(self._window.height())
        tx = int(cell.x() + first.x - pet_w // 2)
        ty = int(cell.y() + first.y - pet_h // 2)
        loco.fly_to(tx, ty, on_arrive=self._begin_eat_loop)

    def _locomotion(self) -> Locomotion | None:
        return self._window._locomotion

    def _say(self, event: str) -> None:
        phrase = pick(event)
        if phrase:
            self._window.say(phrase)

    def _say_text(self, text: str) -> None:
        if text:
            self._window.say(text)

    def _raise_z(self) -> None:
        if self._overlay is None:
            return
        fly_hwnd = int(self._window.winId() or 0)
        topmost = bool(getattr(self._window, "_desktop_topmost", False))
        self._overlay.raise_below(fly_hwnd, topmost=topmost)
        self._window.raise_()

    def _begin_eat_loop(self) -> None:
        logger.info("прожевываю демо-файл укусами")
        self._phase = "eat"
        self._state.mode = "eat"
        self._window.set_state("chew")
        self._bite_i = 0
        self._eat_t0 = time.monotonic()
        self._raise_z()
        if not self._bites:
            QTimer.singleShot(400, self._finish_eat)
            return
        self._do_bite()

    def _place_fly_on_bite(self, bite: Bite) -> None:
        if self._cell is None:
            return
        pet_w = int(self._window.width())
        pet_h = int(self._window.height())
        tx = int(self._cell.x() + bite.x - pet_w // 2)
        ty = int(self._cell.y() + bite.y - pet_h // 2)
        self._window.move(tx, ty)
        bubble = getattr(self._window, "_bubble", None)
        if bubble is not None and hasattr(bubble, "follow_anchor"):
            bubble.follow_anchor()
        self._raise_z()

    def _do_bite(self) -> None:
        if not self._busy:
            return
        if self._bite_i >= len(self._bites):
            self._finish_eat()
            return

        bite = self._bites[self._bite_i]
        self._place_fly_on_bite(bite)
        self._window.set_state("chew")

        if self._overlay is not None:
            left = self._overlay.apply_bite_and_repaint(bite)
        else:
            left = max(0.0, 1.0 - (self._bite_i + 1) / max(1, len(self._bites)))

        n = len(self._bites)
        i = self._bite_i + 1
        logger.info(
            "укус %s/%s: центр=(%s,%s) r=%s, осталось %s%%",
            i,
            n,
            bite.x,
            bite.y,
            bite.r,
            int(round(left * 100)),
        )

        if i == 1:
            self._say_text("Хрум!")
        elif i == n:
            self._say_text("Всё, доела")

        self._bite_i = i
        delay_ms = int(round(self._interval_sec * 1000))
        self._bite_timer = QTimer(self)
        self._bite_timer.setSingleShot(True)
        self._bite_timer.timeout.connect(self._do_bite)
        self._bite_timer.start(max(1, delay_ms))

    def _finish_eat(self) -> None:
        if self._bite_timer is not None:
            self._bite_timer.stop()
            self._bite_timer = None

        elapsed = time.monotonic() - self._eat_t0 if self._eat_t0 else 0.0
        left_pct = 0
        if self._overlay is not None:
            from fly_pet.eat_overlay import remaining_ratio

            left_pct = int(
                round(
                    remaining_ratio(
                        self._overlay.mask_image(), self._overlay.icon_image()
                    )
                    * 100
                )
            )
        logger.info(
            "съедено 100%% за %.1f с (%s укусов, остаток маски %s%%)",
            elapsed,
            len(self._bites),
            left_pct,
        )

        # Сначала карантин (настоящая иконка исчезает), потом снять оверлей.
        path = self._demo_path
        if path is not None and path.is_file():
            try:
                dest = move_to_quarantine(path, self._data_dir)
                logger.info("перенёс в карантин: %s", dest)
            except OSError as exc:
                logger.warning("не удалось перенести в карантин: %s", exc)
                self._say("eat_error")
                self._teardown_overlay()
                self._restore_life()
                self._busy = False
                return
        else:
            logger.warning("демо-файл уже исчез до переноса")

        self._teardown_overlay()

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
        self._phase = "idle"

    def _teardown_overlay(self) -> None:
        if self._bite_timer is not None:
            self._bite_timer.stop()
            self._bite_timer = None
        if self._overlay is not None:
            try:
                self._overlay.hide()
                self._overlay.close()
                self._overlay.deleteLater()
            except Exception as exc:  # noqa: BLE001
                logger.debug("снятие оверлея: %s", exc)
            self._overlay = None
            logger.info("оверлей снят")

    def _restore_life(self) -> None:
        loco = self._locomotion()
        pos = self._window.pos()
        if loco is not None:
            loco.resume_from_desktop(float(pos.x()), float(pos.y()))
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
