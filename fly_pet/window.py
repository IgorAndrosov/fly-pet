"""Прозрачное frameless-окно питомца со спрайтом."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QMouseEvent, QPaintEvent, QPainter, QPixmap
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
GA_ROOT = 2
_MARGIN_PX = 24
_DESKTOP_POINT_CLASSES = frozenset(
    {"Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32"}
)
_DESKTOP_FOREGROUND_CLASSES = frozenset({"Progman", "WorkerW"})

_MODE_TO_ANIM = {
    "idle": "idle",
    "walk": "walk",
    "sleep": "sleep",
    "eat": "chew",
    "fly": "fly",
    "land": "land",
    "rub": "rub",
}


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
        self._facing = 1
        self._locomotion: Locomotion | None = None
        self._loco_timer: QTimer | None = None
        self._loco_last_mono: float | None = None

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
        self._desktop_reassert_logged = False
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
        self.move(int(round(pose.x)), int(round(pose.y)))
        anim = _MODE_TO_ANIM.get(pose.anim, pose.anim)
        self._player.set_state(anim)
        self._sync_anim_fps(anim)
        self._current_frame = self._player.current_frame()
        self._bubble.follow_anchor()
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        x = (self.width() - self._current_frame.width()) // 2
        y = (self.height() - self._current_frame.height()) // 2
        if self._facing < 0:
            painter.translate(self.width(), 0)
            painter.scale(-1, 1)
            x = (self.width() - self._current_frame.width()) // 2
        painter.drawPixmap(x, y, self._current_frame)

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
        """Тик сторожа: восстановить видимость и поднять окно над рабочим столом."""
        if self.isMinimized():
            self.setWindowState(Qt.WindowState.WindowNoState)
        if not self.isVisible():
            self.show()

        # На чужом окне / в полёте не поднимаем через HWND_TOP.
        if self._locomotion is not None and self._locomotion.blocks_desktop_reassert():
            self._desktop_reassert_logged = False
            return

        if not self._is_desktop_on_top():
            self._desktop_reassert_logged = False
            return

        self._raise_without_activate()
        if not self._desktop_reassert_logged:
            logger.debug("окно возвращено поверх рабочего стола (desktop_reassert)")
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

    def _raise_without_activate(self) -> None:
        """Поднять окно наверх без активации (Win32 SetWindowPos)."""
        if sys.platform != "win32":
            logger.warning(
                "desktop_reassert/_raise недоступен на платформе %s — пропуск",
                sys.platform,
            )
            return
        hwnd = int(self.winId())
        if hwnd == 0:
            return
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            HWND_TOP,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
