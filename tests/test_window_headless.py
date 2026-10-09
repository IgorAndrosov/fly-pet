"""Headless-тесты FlyWindow (offscreen, без показа на реальном экране)."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from fly_pet.animation import load_frames
from fly_pet.config import load_config
from fly_pet.state import StateStore, default_state
from fly_pet.window import FlyWindow

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def window(qapp: QApplication, tmp_path: Path) -> FlyWindow:
    del qapp
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    store = StateStore(tmp_path)
    state = default_state()
    state.pose = {"x": 40, "y": 50, "facing": "left"}
    store.save(state)
    state = store.load()
    frames = load_frames(REPO_ROOT)
    win = FlyWindow(cfg, store, state, frames)
    win.show()
    QApplication.processEvents()
    return win


def test_window_size_matches_config(window: FlyWindow, tmp_path: Path) -> None:
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    assert window.width() == cfg.window.width
    assert window.height() == cfg.window.height


def test_animation_tick_advances(window: FlyWindow) -> None:
    window.set_state("walk")
    before = window._current_frame.cacheKey()
    window._on_tick()
    after = window._current_frame.cacheKey()
    assert before != after


def test_drag_updates_pos_and_saves(window: FlyWindow, tmp_path: Path) -> None:
    start = window.pos()
    press_global = QPointF(start + QPoint(10, 10))
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        press_global,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.mousePressEvent(press)

    move_global = QPointF(start + QPoint(80, 60))
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(10, 10),
        move_global,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.mouseMoveEvent(move)
    QApplication.processEvents()

    assert window.pos() != start

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(10, 10),
        QPointF(window.pos() + QPoint(10, 10)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.mouseReleaseEvent(release)
    QApplication.processEvents()

    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert raw["pose"]["x"] == window.pos().x()
    assert raw["pose"]["y"] == window.pos().y()


def test_click_through_toggle(window: FlyWindow) -> None:
    window.set_click_through(True)
    window.set_click_through(False)


def test_set_state_public(window: FlyWindow) -> None:
    window.set_state("sleep")
    assert window._player.state == "sleep"
