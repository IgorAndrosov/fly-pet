"""Прозрачное frameless-окно питомца со спрайтом."""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QMouseEvent, QPaintEvent, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from fly_pet.animation import AnimationPlayer

if TYPE_CHECKING:
    from fly_pet.config import Config
    from fly_pet.state import PetState, StateStore

logger = logging.getLogger("fly_pet")

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
_MARGIN_PX = 24


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
        if mode == "eat":
            self._player.set_state("chew")
        elif mode in frames:
            self._player.set_state(mode)
        else:
            self._player.set_state("idle")
        self._current_frame = self._player.current_frame()
        self._dragging = False
        self._drag_offset = QPoint()
        self._click_through = False

        flags = Qt.WindowType.FramelessWindowHint
        if config.window.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(config.window.title)
        self.resize(config.window.width, config.window.height)
        self.move(self._resolve_start_pos())

        interval_ms = max(1, int(round(1000 / config.tick.animation_fps)))
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        self._pending_click_through = bool(config.window.click_through)

    def set_state(self, name: str) -> None:
        """Переключает набор кадров анимации."""
        self._player.set_state(name)
        self._current_frame = self._player.current_frame()
        self.update()

    def _on_tick(self) -> None:
        self._current_frame = self._player.advance()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        if self._current_frame is None or self._current_frame.isNull():
            return
        painter = QPainter(self)
        x = (self.width() - self._current_frame.width()) // 2
        y = (self.height() - self._current_frame.height()) // 2
        painter.drawPixmap(x, y, self._current_frame)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._persist_pose()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # HWND появляется после создания нативного окна
        self.set_click_through(self._pending_click_through)

    def closeEvent(self, event) -> None:  # noqa: N802
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
