"""Демо-поедание тестового файла на рабочем столе (кнопка трея / --demo-eat)."""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import QObject, QRect, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication

from fly_pet.desktop_icons import (
    find_icon_by_filename,
    notify_shell_create,
    notify_shell_delete,
    user_desktop_path,
)
from fly_pet.eat_overlay import (
    HWND_TOP,
    HWND_TOPMOST,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    Bite,
    IconEatOverlay,
    _crop_from_full,
    bite_interval_sec,
    count_opaque,
    extract_icon_layers_from_cells,
    grab_icon_cell,
    grab_screen,
    icon_cell_rect,
    plan_bites,
    remaining_ratio,
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
_CELL_CLEAR_MS = 80
_CELL_CLEAR_MAX_MS = 2000
HEAD_OFFSET_PX = 32.0
_SWP_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE


def facing_angle_deg(head_x: float, head_y: float, bite_x: float, bite_y: float) -> float:
    """Угол носа на точку укуса: 0° вправо, 90° вниз (ось y экрана)."""
    return math.degrees(math.atan2(bite_y - head_y, bite_x - head_x))


def head_delta(angle_deg: float) -> tuple[float, float]:
    """Смещение головы от центра спрайта на ``HEAD_OFFSET_PX`` по взгляду."""
    rad = math.radians(angle_deg)
    return (
        HEAD_OFFSET_PX * math.cos(rad),
        HEAD_OFFSET_PX * math.sin(rad),
    )


def window_origin_for_head(
    bite_x: float,
    bite_y: float,
    angle_deg: float,
    width: int,
    height: int,
) -> tuple[int, int]:
    """Top-left окна так, чтобы голова (центр + 32 px по углу) попала в точку укуса."""
    dx, dy = head_delta(angle_deg)
    cx = bite_x - dx
    cy = bite_y - dy
    return (
        int(round(cx - width / 2.0)),
        int(round(cy - height / 2.0)),
    )


def eaten_percents(left_ratio: float) -> tuple[int, int]:
    """``(съедено %, остаток маски %)`` из доли остатка. Сумма = 100."""
    left_pct = int(round(max(0.0, min(1.0, float(left_ratio))) * 100))
    return 100 - left_pct, left_pct


def cell_clear_status(
    is_empty: Callable[[], bool],
    elapsed_ms: int,
    timeout_ms: int = _CELL_CLEAR_MAX_MS,
) -> str:
    """Один опрос: ``empty`` / ``timeout`` / ``wait``."""
    if is_empty():
        return "empty"
    if int(elapsed_ms) >= int(timeout_ms):
        return "timeout"
    return "wait"


def run_cell_clear_wait(
    is_empty: Callable[[], bool],
    *,
    interval_ms: int = _CELL_CLEAR_MS,
    timeout_ms: int = _CELL_CLEAR_MAX_MS,
    sleep_ms: Callable[[int], None] | None = None,
) -> str:
    """Ждать пустую клетку. ``empty`` сразу — без паузы; по таймауту — ``timeout``."""
    elapsed = 0
    while True:
        status = cell_clear_status(is_empty, elapsed, timeout_ms)
        if status != "wait":
            return status
        step = min(int(interval_ms), int(timeout_ms) - elapsed)
        if step <= 0:
            return "timeout"
        if sleep_ms is not None:
            sleep_ms(step)
        elapsed += step


def _set_window_pos(hwnd: int, insert_after: int) -> bool:
    """``SetWindowPos`` без сдвига, размера и активации."""
    if sys.platform != "win32" or not hwnd:
        return False
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    return bool(
        user32.SetWindowPos(
            int(hwnd),
            int(insert_after),
            0,
            0,
            0,
            0,
            _SWP_FLAGS,
        )
    )


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
        icon_on_screen: Callable[[dict], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._state = state
        self._store = state_store
        self._data_dir = Path(data_dir)
        self._icon_on_screen = icon_on_screen
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
        self._phase = "idle"  # locate | clear_old | appear | eat
        self._bg_full: QPixmap | None = None
        self._clear_timer: QTimer | None = None
        self._clear_elapsed = 0
        self._clear_probing = False
        self._cell_empty_fn: Callable[[], bool] | None = None
        self._finish_pending = False

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
            self._phase = "clear_old"
            self._gone_waited = 0
            self._poll = QTimer(self)
            self._poll.setInterval(_GONE_WAIT_MS)
            self._poll.timeout.connect(self._wait_old_gone_then_create)
            self._poll.start()
            self._wait_old_gone_then_create()
            return

        self._capture_and_create()

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

    def _wait_old_gone_then_create(self) -> None:
        """Старая иконка должна уйти с экрана до снимка фона."""
        if not self._busy:
            return
        still = None
        try:
            still = find_icon_by_filename(DEMO_FILENAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ожидание старой иконки: %s", exc)
        if still is not None:
            self._gone_waited += _GONE_WAIT_MS
            if self._gone_waited < _GONE_WAIT_MAX_MS:
                return
            logger.info("старая иконка не исчезла — снимаю фон как есть")
        if self._poll is not None:
            self._poll.stop()
            self._poll = None
        self._capture_and_create()

    def _capture_and_create(self) -> None:
        """Снимок фона, пока демо-файла нет, затем одно создание файла."""
        if not self._busy:
            return
        try:
            self._bg_full = grab_screen()
        except Exception as exc:  # noqa: BLE001
            logger.warning("снимок фона до файла: %s", exc)
            self._bg_full = QPixmap()
        full = self._bg_full
        logger.info(
            "фон снят до создания файла %sx%s",
            0 if full is None else full.width(),
            0 if full is None else full.height(),
        )
        try:
            desktop = user_desktop_path()
            path = create_demo_file(desktop)
        except OSError as exc:
            logger.warning("не удалось создать демо-файл: %s", exc)
            self._say("eat_error")
            self._busy = False
            return
        self._demo_path = path
        notify_shell_create(path)
        logger.info("создал файл %s (фаза: иконка)", path)
        self._say("eat_found")
        self._phase = "appear"
        self._start_icon_poll(self._on_icon_for_cell)

    def _on_icon_for_cell(self, icon: dict) -> None:
        """Иконка на месте. Фон клетки уже снят до создания файла."""
        if self._icon_on_screen is not None and not self._icon_on_screen(icon):
            self._abort_offscreen()
            return
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
        self._apply_precaptured_bg(cell)
        self._phase = "appear"
        self._diff_tries = 0
        QTimer.singleShot(_PAINT_WAIT_MS, self._try_build_overlay)

    def _apply_precaptured_bg(self, cell: QRect) -> None:
        full = self._bg_full
        if full is not None and not full.isNull():
            self._bg_cell = _crop_from_full(full, cell)
            logger.info(
                "фон клетки из снимка до файла %sx%s",
                self._bg_cell.width(),
                self._bg_cell.height(),
            )
            return
        logger.warning("нет снимка фона до создания файла")
        self._bg_cell = QImage()

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
        self._bind_z_keeper()
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
        tx, ty, _angle = self._pose_for_bite(cell.x() + first.x, cell.y() + first.y)
        loco.fly_to(tx, ty, on_arrive=self._begin_eat_loop)

    def _locomotion(self) -> Locomotion | None:
        return self._window._locomotion

    def _abort_offscreen(self) -> None:
        """Иконка не на выбранном мониторе: реплика, карантин, без оверлея."""
        logger.warning("иконка демо-файла не на выбранном экране")
        self._say_text("файл не на моём экране")
        path = self._demo_path
        if path is not None and path.is_file():
            try:
                moved = move_to_quarantine(path, self._data_dir)
                logger.info("демо-файл убран в карантин: %s", moved)
            except OSError as exc:
                logger.warning("не удалось убрать демо-файл: %s", exc)
        self._demo_path = None
        self._teardown_overlay()
        self._busy = False
        self._phase = "idle"

    def _say(self, event: str) -> None:
        phrase = pick(event)
        if phrase:
            self._window.say(phrase)

    def _say_text(self, text: str) -> None:
        if text:
            self._window.say(text)

    def _bind_z_keeper(self) -> None:
        bind = getattr(self._window, "set_z_order_keeper", None)
        if callable(bind):
            bind(self._raise_z)

    def _unbind_z_keeper(self) -> None:
        bind = getattr(self._window, "set_z_order_keeper", None)
        if callable(bind):
            bind(None)

    def _raise_z(self) -> None:
        """Оверлей сразу под мухой, муха — HWND_TOP или HWND_TOPMOST."""
        if self._overlay is None:
            return
        fly_hwnd = int(self._window.winId() or 0)
        if fly_hwnd == 0:
            logger.warning("hwnd мухи = 0 — порядок окон не трогаю")
            return
        overlay_hwnd = int(self._overlay.winId() or 0)
        if overlay_hwnd == 0:
            logger.warning("hwnd оверлея = 0 — порядок окон не трогаю")
            return
        topmost = bool(getattr(self._window, "_desktop_topmost", False))
        insert = HWND_TOPMOST if topmost else HWND_TOP
        _set_window_pos(overlay_hwnd, fly_hwnd)
        _set_window_pos(fly_hwnd, int(insert))

    def _reset_eat_visuals(self) -> None:
        window = self._window
        if hasattr(window, "set_sprite_angle_override"):
            window.set_sprite_angle_override(None)
        if hasattr(window, "set_readability_outline"):
            window.set_readability_outline(False)

    def _begin_eat_loop(self) -> None:
        logger.info("прожевываю демо-файл укусами")
        self._phase = "eat"
        self._state.mode = "eat"
        self._window.set_state("chew")
        if hasattr(self._window, "set_readability_outline"):
            self._window.set_readability_outline(True)
        self._bite_i = 0
        self._eat_t0 = time.monotonic()
        self._raise_z()
        if not self._bites:
            QTimer.singleShot(400, self._finish_eat)
            return
        self._do_bite()

    def _window_head(self) -> tuple[float, float]:
        window = self._window
        angle = float(window.sprite_angle_deg()) if hasattr(window, "sprite_angle_deg") else 0.0
        dx, dy = head_delta(angle)
        cx = float(window.x()) + float(window.width()) / 2.0
        cy = float(window.y()) + float(window.height()) / 2.0
        return cx + dx, cy + dy

    def _pose_for_bite(self, bite_x: float, bite_y: float) -> tuple[int, int, float]:
        head_x, head_y = self._window_head()
        if abs(bite_x - head_x) < 0.5 and abs(bite_y - head_y) < 0.5:
            angle = (
                float(self._window.sprite_angle_deg())
                if hasattr(self._window, "sprite_angle_deg")
                else 0.0
            )
        else:
            angle = facing_angle_deg(head_x, head_y, bite_x, bite_y)
        origin = window_origin_for_head(
            bite_x,
            bite_y,
            angle,
            int(self._window.width()),
            int(self._window.height()),
        )
        return origin[0], origin[1], angle

    def _place_fly_on_bite(self, bite: Bite) -> None:
        if self._cell is None:
            return
        tx, ty, angle = self._pose_for_bite(
            self._cell.x() + bite.x, self._cell.y() + bite.y
        )
        if hasattr(self._window, "set_sprite_angle_override"):
            self._window.set_sprite_angle_override(angle)
        self._window.move(tx, ty)
        bubble = getattr(self._window, "_bubble", None)
        if bubble is not None and hasattr(bubble, "follow_anchor"):
            bubble.follow_anchor()

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
        self._raise_z()

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
        if self._bite_timer is not None:
            self._bite_timer.stop()
        self._bite_timer = QTimer(self)
        self._bite_timer.setSingleShot(True)
        self._bite_timer.timeout.connect(self._do_bite)
        self._bite_timer.start(max(1, delay_ms))

    def _mask_left_ratio(self) -> float:
        if self._overlay is None:
            return 0.0
        return remaining_ratio(self._overlay.mask_image(), self._overlay.icon_image())

    def _finish_eat(self) -> None:
        if self._finish_pending:
            return
        self._finish_pending = True
        if self._bite_timer is not None:
            self._bite_timer.stop()
            self._bite_timer = None
        self._reset_eat_visuals()

        if self._overlay is not None and hasattr(self._overlay, "clear_mask"):
            self._overlay.clear_mask()
        elapsed = time.monotonic() - self._eat_t0 if self._eat_t0 else 0.0
        eaten_pct, left_pct = eaten_percents(self._mask_left_ratio())
        logger.info(
            "съедено %s%% за %.1f с (%s укусов, остаток маски %s%%)",
            eaten_pct,
            elapsed,
            len(self._bites),
            left_pct,
        )

        # Оверлей уже залит непрозрачным фоном. Карантин, затем ждём,
        # пока элемент пропадёт из списка explorer, и только потом снимаем.
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
                self._finish_pending = False
                return
        else:
            logger.warning("демо-файл уже исчез до переноса")

        if self._overlay is None:
            self._after_cell_clear("empty")
            return
        self._arm_cell_clear()

    def _probe_cell_empty(self) -> bool:
        if self._cell_empty_fn is not None:
            return bool(self._cell_empty_fn())
        return self._cell_cleared_on_screen()

    def _cell_cleared_on_screen(self) -> bool:
        """Элемента демо-файла больше нет в SysListView32."""
        if self._icon is None:
            return True
        try:
            still = find_icon_by_filename(DEMO_FILENAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("опрос списка после карантина: %s", exc)
            return False
        return still is None

    def _arm_cell_clear(self) -> None:
        self._clear_elapsed = 0
        status = cell_clear_status(self._probe_cell_empty, 0, _CELL_CLEAR_MAX_MS)
        if status != "wait":
            self._after_cell_clear(status)
            return
        self._clear_timer = QTimer(self)
        self._clear_timer.setInterval(_CELL_CLEAR_MS)
        self._clear_timer.timeout.connect(self._on_cell_clear_tick)
        self._clear_timer.start()

    def _on_cell_clear_tick(self) -> None:
        if self._clear_probing or not self._finish_pending:
            return
        self._clear_probing = True
        try:
            self._on_cell_clear_tick_body()
        finally:
            self._clear_probing = False

    def _on_cell_clear_tick_body(self) -> None:
        step = min(_CELL_CLEAR_MS, _CELL_CLEAR_MAX_MS - self._clear_elapsed)
        if step > 0:
            self._clear_elapsed += step
        status = cell_clear_status(
            self._probe_cell_empty, self._clear_elapsed, _CELL_CLEAR_MAX_MS
        )
        if status == "wait":
            return
        if self._clear_timer is not None:
            self._clear_timer.stop()
            self._clear_timer = None
        self._after_cell_clear(status)

    def _after_cell_clear(self, status: str) -> None:
        if status == "timeout":
            logger.warning(
                "иконка не исчезла за %s мс — снимаю оверлей",
                _CELL_CLEAR_MAX_MS,
            )
        elif status == "empty":
            logger.info("клетка очищена за %s мс", self._clear_elapsed)
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
        self._finish_pending = False

    def _teardown_overlay(self) -> None:
        if self._bite_timer is not None:
            self._bite_timer.stop()
            self._bite_timer = None
        if self._clear_timer is not None:
            self._clear_timer.stop()
            self._clear_timer = None
        self._reset_eat_visuals()
        self._unbind_z_keeper()
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
    icon_on_screen: Callable[[dict], bool] | None = None,
) -> DemoEatController:
    """Создать контроллер демо-поедания."""
    return DemoEatController(
        window=window,
        state=state,
        state_store=state_store,
        data_dir=data_dir,
        parent=parent,
        icon_on_screen=icon_on_screen,
    )
