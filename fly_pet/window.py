"""Прозрачное frameless-окно питомца со спрайтом."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QMouseEvent, QPaintEvent, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QApplication, QWidget

from fly_pet.animation import AnimationPlayer
from fly_pet.bubble import SpeechBubble

if TYPE_CHECKING:
    from fly_pet.config import Config
    from fly_pet.locomotion import Locomotion
    from fly_pet.state import PetState, StateStore

logger = logging.getLogger("fly_pet")

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
GA_ROOT = 2
GW_HWNDNEXT = 2
GW_HWNDPREV = 3
# Порог z-order Progman'а для «Show Desktop активен» (как в замерах).
_PROGMAN_NEAR_TOP_MAX = 40
_MARGIN_PX = 24
_DESKTOP_POINT_CLASSES = frozenset(
    {"Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32"}
)
_DESKTOP_ROOT_CLASSES = frozenset({"Progman", "WorkerW"})
# Подтверждений подряд до topmost / отрицаний до снятия (тик ≈ desktop_reassert_ms).
_TOPMOST_CONFIRM_TICKS = 2
_RELEASE_CONFIRM_TICKS = 3
HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)

_MODE_TO_ANIM = {
    "idle": "idle",
    "walk": "walk",
    "sleep": "sleep",
    "eat": "chew",
    "fly": "fly",
    "land": "land",
    "rub": "rub",
    "desktop": "idle",
    "window": "idle",
}


def rotate_sprite_frame(
    frame: QPixmap,
    angle_deg: float,
    *,
    frame_name: str = "",
    cache: dict[tuple[str, int], QPixmap] | None = None,
) -> QPixmap:
    """Повернуть кадр; при угле 0 (после округления до 5°) — исходник без transform."""
    quantized = int(round(float(angle_deg) / 5.0) * 5)
    quantized = ((quantized + 180) % 360) - 180
    if quantized == 0:
        return frame
    key = (frame_name or str(frame.cacheKey()), quantized)
    if cache is not None and key in cache:
        return cache[key]
    transform = QTransform().rotate(float(quantized))
    rotated = frame.transformed(
        transform, Qt.TransformationMode.SmoothTransformation
    )
    if cache is not None:
        cache[key] = rotated
    return rotated


class FlyWindow(QWidget):
    """Окно спрайта: прозрачный фон, перетаскивание, опциональный click-through."""

    def __init__(
        self,
        config: Config,
        state_store: StateStore,
        state: PetState,
        frames: dict[str, list[QPixmap]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._state_store = state_store
        self._state = state
        self._player = AnimationPlayer(frames, config.tick.animation_fps)
        mode = state.mode
        anim = _MODE_TO_ANIM.get(mode, "idle")
        if anim in frames:
            self._player.set_state(anim)
        else:
            self._player.set_state("idle")
        self._current_frame = self._player.current_frame()
        self._dragging = False
        self._drag_offset = QPoint()
        self._click_through = False
        self._user_hidden = False  # спрятана через трей — сторож не показывает
        self._facing = 1
        self._sprite_angle_deg = 0.0
        self._rotated_cache: dict[tuple[str, int], QPixmap] = {}
        self._locomotion: Locomotion | None = None
        self._loco_timer: QTimer | None = None
        self._loco_last_mono: float | None = None
        self._desktop_topmost = False  # временный HWND_TOPMOST, пока стол над мухой
        self._desktop_cover_hits = 0
        self._desktop_cover_misses = 0
        self._desktop_last_probe = ""  # последний наблюдаемый класс для INFO-лога

        flags = Qt.WindowType.FramelessWindowHint
        if config.window.tool_window:
            flags |= Qt.WindowType.Tool
        if config.window.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(config.window.title)
        self.resize(config.window.width, config.window.height)
        self.move(self._resolve_start_pos())

        self._bubble = SpeechBubble(config.bubble)
        self._bubble.attach_to(self)

        interval_ms = max(1, int(round(1000 / config.tick.animation_fps)))
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        self._pending_click_through = bool(config.window.click_through)
        self._desktop_reassert_timer: QTimer | None = None
        if config.window.desktop_reassert:
            self._desktop_reassert_timer = QTimer(self)
            self._desktop_reassert_timer.setInterval(
                max(1, int(config.window.desktop_reassert_ms))
            )
            self._desktop_reassert_timer.timeout.connect(self._on_desktop_reassert)
            self._desktop_reassert_timer.start()

    def say(self, text: str, ttl_ms: int | None = None) -> None:
        """Показать реплику в облачке рядом с питомцем."""
        self._bubble.say(text, ttl_ms=ttl_ms)
        if text and text.strip():
            self._bubble.place_near(self)
            # raise_() в SpeechBubble.say может сбросить topmost — повторить после.
            if self._desktop_topmost:
                self._sync_bubble_topmost()
                QTimer.singleShot(0, self._sync_bubble_topmost)

    def set_user_hidden(self, hidden: bool) -> None:
        """Спрятать/показать по запросу трея (сторож не отменяет)."""
        self._user_hidden = bool(hidden)
        if self._user_hidden:
            self._bubble.hide()
            self.hide()
        else:
            self.show()

    def is_user_hidden(self) -> bool:
        return self._user_hidden

    def set_state(self, name: str) -> None:
        """Переключает набор кадров анимации (mode или имя анимации)."""
        # Пока активна локомоция (не сон/еда) — кадры задаёт она.
        if (
            self._locomotion is not None
            and not self._dragging
            and name in {"idle", "walk", "rub"}
            and self._state.mode not in {"sleep", "eat"}
        ):
            return
        anim = _MODE_TO_ANIM.get(name, name)
        self._player.set_state(anim)
        self._sync_anim_fps(anim)
        self._current_frame = self._player.current_frame()
        self.update()

    def set_locomotion(self, loc: Locomotion | None) -> None:
        """Подключает локомоцию и таймер шага; None — отключить."""
        if self._loco_timer is not None:
            self._loco_timer.stop()
            self._loco_timer.deleteLater()
            self._loco_timer = None
        self._locomotion = loc
        self._loco_last_mono = None
        if loc is None or not self._config.walk.enabled:
            return
        self._loco_timer = QTimer(self)
        self._loco_timer.setInterval(max(1, int(self._config.walk.tick_ms)))
        self._loco_timer.timeout.connect(self._on_locomotion_tick)
        self._loco_timer.start()

    def _on_locomotion_tick(self) -> None:
        if self._locomotion is None or self._dragging:
            return
        if self._state.mode in {"sleep", "eat"}:
            return

        now = time.monotonic()
        if self._loco_last_mono is None:
            dt = self._config.walk.tick_ms / 1000.0
        else:
            dt = max(0.0, now - self._loco_last_mono)
        self._loco_last_mono = now
        pose = self._locomotion.step(dt)
        self._facing = int(pose.facing)
        self._sprite_angle_deg = float(self._locomotion.sprite_angle_deg())
        if self._state.mode not in {"sleep", "eat"}:
            if self._locomotion.is_hold_still():
                self._state.mode = "idle"
            else:
                self._state.mode = self._locomotion.mode.value
        if not self._locomotion.is_hold_still():
            self.move(int(round(pose.x)), int(round(pose.y)))
        anim = _MODE_TO_ANIM.get(pose.anim, pose.anim)
        self._player.set_state(anim)
        self._sync_anim_fps(anim)
        self._current_frame = self._player.current_frame()
        self._bubble.follow_anchor()
        if self._desktop_topmost:
            self._sync_bubble_topmost()
        self.update()

    def _sync_anim_fps(self, anim: str) -> None:
        """Во время рывка — dash_animation_fps, иначе обычный tick.animation_fps."""
        if anim == "walk":
            fps = int(getattr(self._config.walk, "dash_animation_fps", 0)) or int(
                self._config.tick.animation_fps
            )
        else:
            fps = int(self._config.tick.animation_fps)
        interval_ms = max(1, int(round(1000 / max(1, fps))))
        if self._timer.interval() != interval_ms:
            self._timer.setInterval(interval_ms)

    def _on_tick(self) -> None:
        self._current_frame = self._player.advance()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        if self._current_frame is None or self._current_frame.isNull():
            return
        frame = self._rotated_frame(
            self._current_frame,
            self._sprite_angle_deg,
            frame_name=self._player.state,
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        x = (self.width() - frame.width()) // 2
        y = (self.height() - frame.height()) // 2
        painter.drawPixmap(x, y, frame)

    def _rotated_frame(
        self,
        frame: QPixmap,
        angle_deg: float,
        *,
        frame_name: str = "",
    ) -> QPixmap:
        """Повернуть кадр вокруг центра; угол 0 — пиксель-в-пиксель без transform."""
        return rotate_sprite_frame(
            frame,
            angle_deg,
            frame_name=frame_name,
            cache=self._rotated_cache,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            if self._locomotion is not None:
                self._locomotion.pause()
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._bubble.follow_anchor()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._persist_pose()
            if self._locomotion is not None:
                pos = self.pos()
                self._locomotion.resume_from_desktop(float(pos.x()), float(pos.y()))
                self._loco_last_mono = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # HWND появляется после создания нативного окна
        self.set_click_through(self._pending_click_through)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if not self._config.window.never_minimize:
            return
        if event.type() != QEvent.Type.WindowStateChange:
            return
        if not (self.windowState() & Qt.WindowState.WindowMinimized):
            return
        pos = self.pos()
        QTimer.singleShot(0, lambda p=pos: self._restore_from_minimize(p))

    def _restore_from_minimize(self, pos: QPoint) -> None:
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.move(pos)
        self.show()
        logger.debug("окно восстановлено из свёрнутого состояния (never_minimize)")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._bubble.hide()
        self.persist_state()
        super().closeEvent(event)

    def persist_state(self) -> None:
        """Сохраняет текущую позицию и состояние в StateStore."""
        self._persist_pose()

    def _persist_pose(self) -> None:
        pos = self.pos()
        self._state.pose = {
            **dict(self._state.pose),
            "x": int(pos.x()),
            "y": int(pos.y()),
        }
        self._state_store.save(self._state)

    def _resolve_start_pos(self) -> QPoint:
        screen = QApplication.primaryScreen()
        if screen is None:
            return QPoint(0, 0)
        geo = screen.availableGeometry()
        pose = self._state.pose or {}
        x_raw, y_raw = pose.get("x"), pose.get("y")
        if isinstance(x_raw, (int, float)) and isinstance(y_raw, (int, float)):
            x, y = int(x_raw), int(y_raw)
            candidate = QRect(x, y, self.width(), self.height())
            if geo.intersects(candidate) and geo.contains(QPoint(x, y)):
                return QPoint(x, y)
        x = geo.x() + geo.width() - self.width() - _MARGIN_PX
        y = geo.y() + geo.height() - self.height() - _MARGIN_PX
        return QPoint(x, y)

    def set_click_through(self, enabled: bool) -> None:
        """Включает/выключает прохождение кликов сквозь окно (Win32)."""
        enabled = bool(enabled)
        self._pending_click_through = enabled
        self._click_through = enabled
        if sys.platform != "win32":
            logger.warning(
                "set_click_through недоступен на платформе %s — пропуск",
                sys.platform,
            )
            return
        hwnd = int(self.winId())
        if hwnd == 0:
            return
        user32 = ctypes.windll.user32
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
            get_long.restype = ctypes.c_longlong
            set_long.restype = ctypes.c_longlong
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
        else:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
        style = int(get_long(hwnd, GWL_EXSTYLE))
        style |= WS_EX_LAYERED
        if enabled:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        set_long(hwnd, GWL_EXSTYLE, style)

    def _on_desktop_reassert(self) -> None:
        """Тик сторожа: видимость + временный topmost при Show Desktop."""
        if self._user_hidden:
            return
        if self.isMinimized():
            self.setWindowState(Qt.WindowState.WindowNoState)
        if not self.isVisible():
            self.show()

        covered = self._is_desktop_on_top()
        if covered:
            self._desktop_cover_hits += 1
            self._desktop_cover_misses = 0
        else:
            self._desktop_cover_misses += 1
            self._desktop_cover_hits = 0

        loco_blocks = (
            self._locomotion is not None
            and self._locomotion.blocks_desktop_reassert()
        )

        if self._desktop_topmost:
            # Снимаем только когда стол реально не над мухой — не из-за полёта.
            if self._desktop_cover_misses >= _RELEASE_CONFIRM_TICKS:
                self._clear_desktop_topmost()
                self._reset_desktop_cover_counters()
                return
            # Qt raise_/show облачка может сбросить topmost — вернуть.
            self._sync_bubble_topmost()
            return

        # На чужом окне (не Show Desktop) — не поднимать.
        if loco_blocks and not covered:
            self._reset_desktop_cover_counters()
            return

        if self._desktop_cover_hits < _TOPMOST_CONFIRM_TICKS:
            return

        self._raise_without_activate()
        self._reset_desktop_cover_counters()

    def _reset_desktop_cover_counters(self) -> None:
        self._desktop_cover_hits = 0
        self._desktop_cover_misses = 0

    def _probe_point(self) -> QPoint:
        """Центр окна; если вне экрана — ближайшая внутренняя точка."""
        center = self.frameGeometry().center()
        screen = QApplication.primaryScreen()
        if screen is None:
            return center
        geo = screen.geometry()
        x = max(geo.left(), min(center.x(), geo.right()))
        y = max(geo.top(), min(center.y(), geo.bottom()))
        return QPoint(x, y)

    @staticmethod
    def _window_class_name(hwnd: int) -> str:
        if not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _is_our_hwnd(self, hwnd: int, our_hwnd: int) -> bool:
        if not hwnd or not our_hwnd:
            return False
        if hwnd == our_hwnd:
            return True
        root = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or 0)
        if root == our_hwnd:
            return True
        # Облачко — отдельный top-level Tool; не считаем его «чужим» окном над мухой.
        try:
            bubble_hwnd = int(self._bubble.winId() or 0)
        except Exception:
            bubble_hwnd = 0
        if bubble_hwnd and (hwnd == bubble_hwnd or root == bubble_hwnd):
            return True
        return False

    def _is_desktop_on_top(self) -> bool:
        """True, если Show Desktop активен и стол над мухой (точка + z-order Progman)."""
        if sys.platform != "win32":
            logger.warning(
                "desktop_reassert недоступен на платформе %s — пропуск",
                sys.platform,
            )
            self._desktop_last_probe = f"платформа={sys.platform}"
            return False

        our_hwnd = int(self.winId())
        if our_hwnd == 0:
            self._desktop_last_probe = "нет hwnd"
            return False

        user32 = ctypes.windll.user32
        point = self._probe_point()
        at_point = int(user32.WindowFromPoint(wintypes.POINT(point.x(), point.y())) or 0)
        at_class = self._window_class_name(at_point)
        above = int(user32.GetWindow(our_hwnd, GW_HWNDPREV) or 0)
        above_class = self._window_class_name(above)

        point_desktop = False
        probe = f"в_точке={at_class or '?'}, над_ней={above_class or '?'}"
        if at_point and self._is_our_hwnd(at_point, our_hwnd):
            # Своя точка сама по себе не доказывает стол (особенно при topmost).
            pass
        elif at_point:
            if at_class in _DESKTOP_POINT_CLASSES:
                point_desktop = True
                probe = f"в_точке={at_class}"
            else:
                root = int(user32.GetAncestor(at_point, GA_ROOT) or 0)
                root_class = self._window_class_name(root)
                if root_class in _DESKTOP_ROOT_CLASSES:
                    point_desktop = True
                    probe = f"в_точке={at_class}, корень={root_class}"
        if not point_desktop and above_class in _DESKTOP_POINT_CLASSES:
            point_desktop = True
            probe = f"в_точке={at_class or '?'}, над_ней={above_class}"

        progman_z = self._progman_z_index()
        show_desktop = (
            progman_z is not None and progman_z < _PROGMAN_NEAR_TOP_MAX
        )

        # Topmost только в режиме Show Desktop: иначе SysListView32 на пустом
        # столе включал бы topmost «навсегда», а своя topmost-точка залипала бы.
        if self._desktop_topmost:
            self._desktop_last_probe = (
                f"{probe}, progman_z={progman_z if progman_z is not None else '?'}"
            )
            return show_desktop

        self._desktop_last_probe = probe
        return bool(point_desktop and show_desktop)

    def _progman_z_index(self) -> int | None:
        """Индекс Progman в z-order сверху; None если не найден за разумный предел."""
        if sys.platform != "win32":
            return None
        user32 = ctypes.windll.user32
        progman = int(user32.FindWindowW("Progman", None) or 0)
        if not progman:
            return None
        i = 0
        h = int(user32.GetTopWindow(None) or 0)
        while h:
            if h == progman:
                return i
            i += 1
            if i >= _PROGMAN_NEAR_TOP_MAX * 2:
                break
            h = int(user32.GetWindow(h, GW_HWNDNEXT) or 0)
        return None

    @staticmethod
    def _configure_set_window_pos() -> None:
        """Прототипы ctypes обязательны: без них SetWindowPos → 0 / err 1400."""
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

    def _set_hwnd_topmost(self, hwnd: int, topmost: bool) -> bool:
        """SetWindowPos(HWND_TOPMOST / HWND_NOTOPMOST) без перемещения и активации."""
        if sys.platform != "win32" or not hwnd:
            return False
        self._configure_set_window_pos()
        insert = HWND_TOPMOST if topmost else HWND_NOTOPMOST
        ctypes.set_last_error(0)
        ok = bool(
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(hwnd),
                insert,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        )
        if not ok:
            logger.warning(
                "SetWindowPos(%s) отказ err=%s hwnd=%s",
                "TOPMOST" if topmost else "NOTOPMOST",
                ctypes.get_last_error(),
                hwnd,
            )
        return ok

    def _sync_bubble_topmost(self) -> None:
        """Облачко — отдельный top-level; пока стол сверху — тоже временно topmost."""
        if sys.platform != "win32" or not self._desktop_topmost:
            return
        if not self._bubble.isVisible():
            return
        bh = int(self._bubble.winId() or 0)
        if not bh:
            return
        self._set_hwnd_topmost(bh, True)

    def _clear_desktop_topmost(self) -> None:
        """Снять временный topmost с мухи и облачка."""
        why = (
            f"стол больше не над мухой ({self._desktop_last_probe})"
            if self._desktop_last_probe
            else "стол больше не над мухой"
        )
        was = self._desktop_topmost
        if sys.platform == "win32":
            hwnd = int(self.winId() or 0)
            if hwnd:
                self._set_hwnd_topmost(hwnd, False)
            bh = int(self._bubble.winId() or 0)
            if bh:
                self._set_hwnd_topmost(bh, False)
        self._desktop_topmost = False
        if was:
            logger.info("снял topmost: %s", why)

    def _raise_without_activate(self) -> None:
        """Временно HWND_TOPMOST, пока стол над мухой (без SetParent)."""
        if sys.platform != "win32":
            logger.warning(
                "desktop_reassert/topmost недоступен на платформе %s — пропуск",
                sys.platform,
            )
            return
        hwnd = int(self.winId())
        if not hwnd:
            return
        if self._desktop_topmost:
            self._sync_bubble_topmost()
            return
        if not self._set_hwnd_topmost(hwnd, True):
            return
        self._desktop_topmost = True
        self._sync_bubble_topmost()
        probe = self._desktop_last_probe or "без пробы"
        logger.info("применил topmost: стол над мухой (%s)", probe)
