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
HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
GA_PARENT = 1
GA_ROOT = 2
GW_HWNDNEXT = 2
DWMWA_CLOAKED = 14
_MARGIN_PX = 24
_DESKTOP_POINT_CLASSES = frozenset(
    {"Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32"}
)
_DESKTOP_FOREGROUND_CLASSES = frozenset({"Progman", "WorkerW"})
# Оболочка над Progman при Show Desktop — не считаем «обычным окном».
_ZORDER_SKIP_CLASSES = frozenset(
    {
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Shell_Flyout",
        "DV2ControlHost",
        "NativeHWNDHost",
        "ForegroundStaging",
        "ApplicationManager_ImmersiveShellWindow",
        "EdgeUiInputTopWndClass",
        "SuspendableDesktopWindow",
    }
)

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
        self._desktop_adopted = False  # SetParent(Progman) после Show Desktop
        self._desktop_reassert_logged = False
        self._desktop_band_denied_logged = False

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
            self._sync_bubble_desktop_parent()
            # raise_() в SpeechBubble.say отрабатывает через очередь — повторить после.
            if self._desktop_adopted:
                QTimer.singleShot(0, self._sync_bubble_desktop_parent)

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
        # Усыновление Progman'ом ломает z-порядок посадки/полёта — отпустить до move.
        if self._desktop_adopted and self._locomotion.blocks_desktop_reassert():
            self._release_desktop_parent()
        if not self._locomotion.is_hold_still():
            self.move(int(round(pose.x)), int(round(pose.y)))
        anim = _MODE_TO_ANIM.get(pose.anim, pose.anim)
        self._player.set_state(anim)
        self._sync_anim_fps(anim)
        self._current_frame = self._player.current_frame()
        self._bubble.follow_anchor()
        self._sync_bubble_desktop_parent()
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
            self._sync_bubble_desktop_parent()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def move(self, *args) -> None:  # type: ignore[override]
        """Экранные координаты для Qt; при усыновлении Progman'ом — нативный client-pos."""
        if len(args) == 1 and isinstance(args[0], QPoint):
            x, y = int(args[0].x()), int(args[0].y())
        elif len(args) >= 2:
            x, y = int(args[0]), int(args[1])
        else:
            super().move(*args)
            return
        super().move(x, y)
        if self._desktop_adopted:
            self._sync_adopted_native_pos(x, y)

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
        """Тик сторожа: видимость + усыновление Progman'ом при Show Desktop."""
        if self._user_hidden:
            return
        if self.isMinimized():
            self.setWindowState(Qt.WindowState.WindowNoState)
        if not self.isVisible():
            self.show()

        # На чужом окне / в полёте — только отпустить, не усыновлять.
        if self._locomotion is not None and self._locomotion.blocks_desktop_reassert():
            if self._desktop_adopted:
                self._release_desktop_parent()
            self._desktop_reassert_logged = False
            return

        if self._desktop_adopted:
            if not self._is_adopted_native():
                self._desktop_adopted = False
                self._desktop_reassert_logged = False
                return
            # Рабочий стол снова «внизу» — вернуть top-level над целью/столом.
            if not self._is_show_desktop_active():
                self._release_desktop_parent()
                self._desktop_reassert_logged = False
                return
            # Qt raise_/move облачка может сбросить SetParent — вернуть.
            self._sync_bubble_desktop_parent()
            return

        if not self._is_desktop_on_top():
            self._desktop_reassert_logged = False
            return

        self._raise_without_activate()
        if not self._desktop_reassert_logged:
            logger.debug(
                "окно усыновлено Progman'ом поверх рабочего стола (desktop_reassert)"
            )
            self._desktop_reassert_logged = True

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
        return root == our_hwnd

    def _is_desktop_on_top(self) -> bool:
        """True, если поверх питомца лежит рабочий стол (не обычное окно)."""
        if sys.platform != "win32":
            logger.warning(
                "desktop_reassert недоступен на платформе %s — пропуск",
                sys.platform,
            )
            return False

        our_hwnd = int(self.winId())
        if our_hwnd == 0:
            return False

        user32 = ctypes.windll.user32
        point = self._probe_point()
        at_point = int(user32.WindowFromPoint(wintypes.POINT(point.x(), point.y())) or 0)
        at_class = self._window_class_name(at_point)
        if (
            at_point
            and not self._is_our_hwnd(at_point, our_hwnd)
            and at_class in _DESKTOP_POINT_CLASSES
        ):
            return True

        fg = int(user32.GetForegroundWindow() or 0)
        fg_class = self._window_class_name(fg)
        return fg_class in _DESKTOP_FOREGROUND_CLASSES

    @staticmethod
    def _progman_hwnd() -> int:
        if sys.platform != "win32":
            return 0
        return int(ctypes.windll.user32.FindWindowW("Progman", None) or 0)

    def _is_adopted_native(self) -> bool:
        """True, если нативный parent — Progman (после SetParent)."""
        if sys.platform != "win32":
            return False
        hwnd = int(self.winId())
        progman = self._progman_hwnd()
        if not hwnd or not progman:
            return False
        parent = int(ctypes.windll.user32.GetAncestor(hwnd, GA_PARENT) or 0)
        return parent == progman

    @staticmethod
    def _is_window_cloaked(hwnd: int) -> bool:
        if not hwnd or sys.platform != "win32":
            return False
        try:
            cloaked = ctypes.c_int(0)
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(DWMWA_CLOAKED),
                ctypes.byref(cloaked),
                ctypes.sizeof(cloaked),
            )
            return hr == 0 and cloaked.value != 0
        except (AttributeError, OSError):
            return False

    def _is_show_desktop_active(self) -> bool:
        """True, если Progman/WorkerW выше обычных окон (режим Show Desktop)."""
        if sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32
        our_hwnd = int(self.winId())
        hwnd = int(user32.GetTopWindow(0) or 0)
        while hwnd:
            if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                cls = self._window_class_name(hwnd)
                if cls in _DESKTOP_FOREGROUND_CLASSES:
                    return True
                if cls in _ZORDER_SKIP_CLASSES or self._is_our_hwnd(hwnd, our_hwnd):
                    hwnd = int(user32.GetWindow(hwnd, GW_HWNDNEXT) or 0)
                    continue
                if self._is_window_cloaked(hwnd):
                    hwnd = int(user32.GetWindow(hwnd, GW_HWNDNEXT) or 0)
                    continue
                return False
            hwnd = int(user32.GetWindow(hwnd, GW_HWNDNEXT) or 0)
        return False

    def _sync_adopted_native_pos(self, screen_x: int, screen_y: int) -> None:
        """Пересчитать экранные координаты в клиентские Progman и MoveWindow."""
        if sys.platform != "win32":
            return
        if not self._is_adopted_native():
            self._desktop_adopted = False
            return
        hwnd = int(self.winId())
        progman = self._progman_hwnd()
        if not hwnd or not progman:
            return
        user32 = ctypes.windll.user32
        pt = wintypes.POINT(int(screen_x), int(screen_y))
        user32.ScreenToClient(progman, ctypes.byref(pt))
        user32.MoveWindow(
            hwnd, pt.x, pt.y, int(self.width()), int(self.height()), True
        )

    def _set_progman_parent(self, hwnd: int, *, adopt: bool) -> None:
        """Усыновить/отпустить произвольный HWND относительно Progman, сохранив экранный rect."""
        if sys.platform != "win32" or not hwnd:
            return
        progman = self._progman_hwnd()
        if not progman:
            return
        user32 = ctypes.windll.user32
        parent = int(user32.GetAncestor(hwnd, GA_PARENT) or 0)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        screen_x, screen_y = int(rect.left), int(rect.top)
        w = max(1, int(rect.right - rect.left))
        h = max(1, int(rect.bottom - rect.top))
        if adopt:
            if parent == progman:
                pt = wintypes.POINT(screen_x, screen_y)
                user32.ScreenToClient(progman, ctypes.byref(pt))
                user32.MoveWindow(hwnd, pt.x, pt.y, w, h, True)
                return
            user32.SetParent(hwnd, progman)
            pt = wintypes.POINT(screen_x, screen_y)
            user32.ScreenToClient(progman, ctypes.byref(pt))
            user32.MoveWindow(hwnd, pt.x, pt.y, w, h, True)
            return
        if parent != progman:
            return
        user32.SetParent(hwnd, None)
        user32.MoveWindow(hwnd, screen_x, screen_y, w, h, True)

    def _sync_bubble_desktop_parent(self) -> None:
        """Облачко — отдельный top-level; при Show Desktop тоже усыновляем Progman'ом."""
        if sys.platform != "win32" or not self._desktop_adopted:
            return
        if not self._bubble.isVisible():
            return
        bh = int(self._bubble.winId() or 0)
        if not bh:
            return
        self._set_progman_parent(bh, adopt=True)

    def _adopt_desktop_parent(self) -> None:
        """SetParent(Progman): муха в полосе рабочего стола после Win+D."""
        if sys.platform != "win32":
            logger.warning(
                "desktop_reassert/_adopt недоступен на платформе %s — пропуск",
                sys.platform,
            )
            return
        hwnd = int(self.winId())
        progman = self._progman_hwnd()
        if not hwnd or not progman:
            return
        if self._is_adopted_native():
            self._desktop_adopted = True
            self._sync_bubble_desktop_parent()
            return

        # Замер: SetWindowBand(ZBID_DESKTOP) → ERROR_ACCESS_DENIED (5) без uiAccess.
        if not self._desktop_band_denied_logged:
            self._try_log_set_window_band_denied(hwnd)

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        screen_x, screen_y = int(rect.left), int(rect.top)
        ctypes.set_last_error(0)
        self._set_progman_parent(hwnd, adopt=True)
        err = ctypes.get_last_error()
        if not self._is_adopted_native():
            logger.warning(
                "SetParent(Progman) не усыновил окно (err=%s) — муха может пропасть под столом",
                err,
            )
            return
        self._desktop_adopted = True
        # Qt продолжает думать экранными координатами; натив — клиентские.
        super().move(screen_x, screen_y)
        self._sync_adopted_native_pos(screen_x, screen_y)
        self._sync_bubble_desktop_parent()

    def _release_desktop_parent(self) -> None:
        """SetParent(NULL) и вернуть экранную геометрию."""
        if sys.platform != "win32":
            self._desktop_adopted = False
            return
        hwnd = int(self.winId())
        if not hwnd:
            self._desktop_adopted = False
            return
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        screen_x, screen_y = int(rect.left), int(rect.top)
        bh = int(self._bubble.winId() or 0)
        if bh:
            self._set_progman_parent(bh, adopt=False)
        if self._is_adopted_native():
            self._set_progman_parent(hwnd, adopt=False)
        self._desktop_adopted = False
        super().move(screen_x, screen_y)

    def _try_log_set_window_band_denied(self, hwnd: int) -> None:
        """Один раз зафиксировать отказ SetWindowBand (замер: err=5)."""
        self._desktop_band_denied_logged = True
        user32 = ctypes.windll.user32
        if not hasattr(user32, "SetWindowBand"):
            logger.info("SetWindowBand недоступен в user32 — используем SetParent(Progman)")
            return
        fn = user32.SetWindowBand
        fn.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.DWORD]
        fn.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        ok = bool(fn(hwnd, None, 1))  # ZBID_DESKTOP
        err = ctypes.get_last_error()
        if ok:
            # На этой машине не ожидается; откатим, чтобы не оставлять полосу.
            fn(hwnd, None, 0)
            logger.info("SetWindowBand(ZBID_DESKTOP) неожиданно успешен — откатили, выбран SetParent")
            return
        logger.info(
            "SetWindowBand(ZBID_DESKTOP) отказ ok=%s err=%s — выбран SetParent(Progman)",
            ok,
            err,
        )

    def _raise_without_activate(self) -> None:
        """Поднять над рабочим столом: SetParent(Progman), не HWND_TOP / не TOPMOST."""
        self._adopt_desktop_parent()
