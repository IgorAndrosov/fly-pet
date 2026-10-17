"""Шаг 21: нос в укус, зи-порядок, контур, ожидание пустой клетки."""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from fly_pet.animation import load_frames
from fly_pet.config import load_config
from fly_pet.eat import (
    HEAD_OFFSET_PX,
    DemoEatController,
    eaten_percents,
    facing_angle_deg,
    head_delta,
    run_cell_clear_wait,
    window_origin_for_head,
)
from fly_pet.eat_overlay import Bite, IconEatOverlay, remaining_ratio
from fly_pet.state import StateStore, default_state
from fly_pet.window import FlyWindow

REPO_ROOT = Path(__file__).resolve().parents[1]


_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    _APP = app
    return app


class _Loco:
    def __init__(self, angle: float) -> None:
        self.angle = angle

    def sprite_angle_deg(self) -> float:
        return self.angle

    def blocks_desktop_reassert(self) -> bool:
        return False


class _Fly:
    def __init__(self) -> None:
        self._x = 0
        self._y = 0
        self._w = 96
        self._h = 96
        self._angle = 0.0
        self._override: float | None = None
        self.outline = False
        self._locomotion = None
        self._desktop_topmost = False
        self._bubble = None
        self.hwnd = 1001
        self.keeper = None
        self.states: list[str] = []

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h

    def pos(self) -> QPoint:
        return QPoint(self._x, self._y)

    def move(self, x: int, y: int) -> None:
        self._x, self._y = int(x), int(y)

    def sprite_angle_deg(self) -> float:
        if self._override is not None:
            return self._override
        return self._angle

    def set_sprite_angle_override(self, angle: float | None) -> None:
        if angle is None:
            self._override = None
            if self._locomotion is not None:
                self._angle = float(self._locomotion.sprite_angle_deg())
            return
        self._override = float(angle)

    def set_readability_outline(self, enabled: bool) -> None:
        self.outline = bool(enabled)

    def set_state(self, name: str) -> None:
        self.states.append(name)

    def say(self, text: str, ttl_ms: int | None = None) -> None:
        del text, ttl_ms

    def winId(self) -> int:
        return self.hwnd

    def set_z_order_keeper(self, callback) -> None:
        self.keeper = callback


class _Overlay:
    def __init__(self, hwnd: int = 2002) -> None:
        self.hwnd = hwnd
        self.hidden = False
        self.closed = False
        self.mask = QImage()
        self.icon = QImage()

    def winId(self) -> int:
        return self.hwnd

    def hide(self) -> None:
        self.hidden = True

    def close(self) -> None:
        self.closed = True

    def deleteLater(self) -> None:
        return None

    def show(self) -> None:
        self.hidden = False

    def mask_image(self) -> QImage:
        return self.mask

    def icon_image(self) -> QImage:
        return self.icon

    def apply_bite_and_repaint(self, bite: Bite) -> float:
        del bite
        return 0.5


def _controller(tmp_path: Path, window: _Fly | None = None) -> tuple[DemoEatController, _Fly]:
    _qapp()
    fly = window or _Fly()
    ctrl = DemoEatController(
        window=fly,  # type: ignore[arg-type]
        state=default_state(),
        state_store=StateStore(tmp_path),
        data_dir=tmp_path,
    )
    return ctrl, fly


def _head_at(origin_x: int, origin_y: int, angle: float, width: int = 96, height: int = 96) -> tuple[float, float]:
    dx, dy = head_delta(angle)
    return origin_x + width / 2.0 + dx, origin_y + height / 2.0 + dy


def test_facing_angles_and_head_on_bite(tmp_path: Path) -> None:
    # Голова в (0, 0): вправо / вниз / влево / вверх.
    cases = (
        (80.0, 0.0, 0.0),
        (0.0, 80.0, 90.0),
        (-80.0, 0.0, 180.0),
        (0.0, -80.0, -90.0),
    )
    for bite_x, bite_y, expected in cases:
        angle = facing_angle_deg(0.0, 0.0, bite_x, bite_y)
        assert angle == pytest.approx(expected, abs=1e-6)
        ox, oy = window_origin_for_head(bite_x, bite_y, angle, 96, 96)
        hx, hy = _head_at(ox, oy, angle)
        assert hx == pytest.approx(bite_x, abs=0.51)
        assert hy == pytest.approx(bite_y, abs=0.51)
        assert HEAD_OFFSET_PX == 32.0

    ctrl, fly = _controller(tmp_path)
    ctrl._cell = QRect(0, 0, 400, 400)
    ctrl._busy = True
    # Старт: окно (0,0), взгляд 0° → голова (80, 48).
    targets = (
        (180, 48, 0.0),
        (80, 148, 90.0),
        (-20, 48, 180.0),
        (80, -52, -90.0),
    )
    for bite_x, bite_y, expected in targets:
        fly.move(0, 0)
        fly._override = None
        fly._angle = 0.0
        ctrl._place_fly_on_bite(Bite(x=bite_x, y=bite_y, r=8, angle_deg=0.0))
        assert fly.sprite_angle_deg() == pytest.approx(expected, abs=1e-6)
        hx, hy = _head_at(fly.x(), fly.y(), fly.sprite_angle_deg())
        assert hx == pytest.approx(bite_x, abs=0.51)
        assert hy == pytest.approx(bite_y, abs=0.51)


def test_sprite_angle_override_none_restores_locomotion(tmp_path: Path) -> None:
    _qapp()
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    store = StateStore(tmp_path)
    state = default_state()
    state.pose = {"x": 40, "y": 50, "facing": "left"}
    store.save(state)
    frames = load_frames(REPO_ROOT)
    win = FlyWindow(cfg, store, state, frames)
    win.show()
    QApplication.processEvents()
    loco = _Loco(35.0)
    win._locomotion = loco  # type: ignore[assignment]
    win._sprite_angle_deg = 35.0
    win.set_sprite_angle_override(90.0)
    assert win.sprite_angle_deg() == pytest.approx(90.0)
    win.set_sprite_angle_override(None)
    assert win.sprite_angle_deg() == pytest.approx(35.0)
    assert win._sprite_angle_override is None

    win.set_readability_outline(True)
    frame = win._rotated_frame(win._current_frame, 0.0, frame_name="idle")
    halo = win._outline_halo(frame)
    assert halo.isEmpty() is False
    win.repaint()
    QApplication.processEvents()
    win.set_readability_outline(False)
    seen: list[int] = []
    win.set_z_order_keeper(lambda: seen.append(1))
    win._on_desktop_reassert()
    assert seen == [1]
    win.set_z_order_keeper(None)
    win.close()


def test_do_bite_reasserts_z_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []

    class _SetWindowPos:
        argtypes = None
        restype = None

        def __call__(self, hwnd, insert_after, x, y, w, h, flags):  # noqa: ANN001
            del x, y, w, h, flags

            def _n(value: object) -> int:
                return int(getattr(value, "value", value))

            calls.append((_n(hwnd), _n(insert_after)))
            return 1

    monkeypatch.setattr(ctypes.windll.user32, "SetWindowPos", _SetWindowPos())
    ctrl, fly = _controller(tmp_path)
    overlay = _Overlay()
    ctrl._overlay = overlay  # type: ignore[assignment]
    ctrl._busy = True
    ctrl._cell = QRect(0, 0, 80, 80)
    ctrl._bites = [
        Bite(x=30, y=20, r=8, angle_deg=0.0),
        Bite(x=40, y=20, r=8, angle_deg=0.0),
    ]
    ctrl._bite_i = 0
    ctrl._interval_sec = 0.3
    ctrl._do_bite()
    assert calls[-2] == (overlay.hwnd, fly.hwnd)
    assert calls[-1] == (fly.hwnd, 0)

    calls.clear()
    fly._desktop_topmost = True
    ctrl._do_bite()
    assert calls[-2] == (overlay.hwnd, fly.hwnd)
    assert calls[-1] == (fly.hwnd, -1)
    if ctrl._bite_timer is not None:
        ctrl._bite_timer.stop()


def test_cell_clear_wait_immediate_delayed_and_timeout() -> None:
    slept: list[int] = []

    def _sleep(ms: int) -> None:
        slept.append(ms)

    assert run_cell_clear_wait(lambda: True, sleep_ms=_sleep) == "empty"
    assert slept == []

    clock = {"ms": 0}

    def _empty_later() -> bool:
        return clock["ms"] >= 400

    def _sleep_clock(ms: int) -> None:
        clock["ms"] += ms
        slept.append(ms)

    slept.clear()
    assert run_cell_clear_wait(_empty_later, sleep_ms=_sleep_clock) == "empty"
    assert clock["ms"] == 400
    assert sum(slept) == 400

    slept.clear()
    assert run_cell_clear_wait(lambda: False, sleep_ms=_sleep) == "timeout"
    assert sum(slept) == 1500


def test_cell_clear_timeout_warns_and_finishes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctrl, _fly = _controller(tmp_path)
    overlay = _Overlay()
    ctrl._overlay = overlay  # type: ignore[assignment]
    ctrl._busy = True
    ctrl._eat_t0 = 0.0
    ctrl._bites = []
    ctrl._demo_path = None
    ctrl._cell_empty_fn = lambda: False
    before = int(ctrl._state.eaten)
    with caplog.at_level(logging.WARNING, logger="fly_pet"):
        ctrl._finish_eat()
        assert ctrl._busy is True
        assert ctrl._clear_timer is not None
        ctrl._clear_timer.stop()
        while ctrl._busy:
            ctrl._on_cell_clear_tick()
    assert ctrl._clear_elapsed == 1500
    assert any("клетка не опустела" in rec.message for rec in caplog.records)
    assert ctrl._busy is False
    assert ctrl._overlay is None
    assert overlay.closed is True
    assert int(ctrl._state.eaten) == before + 1


def test_finish_log_matches_mask_remainder(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert eaten_percents(0.04) == (96, 4)
    assert eaten_percents(0.0) == (100, 0)

    icon = QImage(10, 10, QImage.Format.Format_ARGB32)
    icon.fill(QColor(255, 255, 255, 255))
    mask = QImage(10, 10, QImage.Format.Format_ARGB32)
    mask.fill(Qt.GlobalColor.transparent)
    for i in range(4):
        mask.setPixelColor(i, 0, QColor(255, 255, 255, 255))
    assert remaining_ratio(mask, icon) == pytest.approx(0.04)

    ctrl, _fly = _controller(tmp_path)
    overlay = _Overlay()
    overlay.mask = mask
    overlay.icon = icon
    ctrl._overlay = overlay  # type: ignore[assignment]
    ctrl._busy = True
    ctrl._eat_t0 = 0.0
    ctrl._bites = [Bite(x=1, y=1, r=4, angle_deg=0.0)]
    ctrl._cell_empty_fn = lambda: True
    with caplog.at_level(logging.INFO, logger="fly_pet"):
        ctrl._finish_eat()
    text = "\n".join(rec.message for rec in caplog.records)
    assert "съедено 96%" in text
    assert "остаток маски 4%" in text
    assert ctrl._busy is False


def test_clear_mask_zeroes_remainder() -> None:
    _qapp()
    icon = QImage(8, 8, QImage.Format.Format_ARGB32)
    icon.fill(QColor(20, 20, 20, 255))
    bg = QImage(8, 8, QImage.Format.Format_ARGB32)
    bg.fill(QColor(240, 240, 240, 255))
    overlay = IconEatOverlay(QRect(0, 0, 8, 8), bg, icon)
    assert remaining_ratio(overlay.mask_image(), overlay.icon_image()) == pytest.approx(1.0)
    overlay.clear_mask()
    assert remaining_ratio(overlay.mask_image(), overlay.icon_image()) == pytest.approx(0.0)
    overlay.close()


def test_readability_outline_on_eat_off_after(tmp_path: Path) -> None:
    ctrl, fly = _controller(tmp_path)
    ctrl._busy = True
    ctrl._cell = QRect(0, 0, 80, 80)
    ctrl._bites = [Bite(x=20, y=20, r=8, angle_deg=0.0)]
    ctrl._overlay = None
    ctrl._begin_eat_loop()
    assert fly.outline is True
    if ctrl._bite_timer is not None:
        ctrl._bite_timer.stop()

    ctrl._cell_empty_fn = lambda: True
    ctrl._finish_eat()
    assert fly.outline is False
    assert fly._override is None

    fly.outline = True
    fly.set_sprite_angle_override(12.0)
    ctrl._teardown_overlay()
    assert fly.outline is False
    assert fly._override is None
