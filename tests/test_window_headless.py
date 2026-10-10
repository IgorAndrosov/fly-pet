"""Headless-тесты FlyWindow (offscreen, без показа на реальном экране)."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from fly_pet.animation import load_frames
from fly_pet.config import load_config
from fly_pet.state import StateStore, default_state
from fly_pet.window import FlyWindow, rotate_sprite_frame

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


def _expected_corner(qapp: QApplication, cfg) -> QPoint:
    geo = qapp.primaryScreen().availableGeometry()
    return QPoint(
        geo.x() + geo.width() - cfg.window.width - 24,
        geo.y() + geo.height() - cfg.window.height - 24,
    )


def _make_window(tmp_path: Path, pose: dict | None = None) -> FlyWindow:
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    store = StateStore(tmp_path)
    state = default_state()
    if pose is not None:
        state.pose = pose
        store.save(state)
        state = store.load()
    frames = load_frames(REPO_ROOT)
    win = FlyWindow(cfg, store, state, frames)
    win.show()
    QApplication.processEvents()
    return win


def test_default_pose_opens_bottom_right(qapp: QApplication, tmp_path: Path) -> None:
    """Без сохранённой позы — правый нижний угол, не (0,0); state.json не нужен."""
    assert not (tmp_path / "state.json").exists()
    win = _make_window(tmp_path)
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    expected = _expected_corner(qapp, cfg)
    assert win.pos() != QPoint(0, 0)
    assert win.pos() == expected
    assert not (tmp_path / "state.json").exists()


def test_saved_pose_respected(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    win = _make_window(tmp_path, {"x": 300, "y": 200, "facing": "left"})
    assert win.pos() == QPoint(300, 200)


def test_out_of_bounds_pose_falls_back_to_corner(qapp: QApplication, tmp_path: Path) -> None:
    win = _make_window(tmp_path, {"x": -5000, "y": -5000, "facing": "left"})
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    assert win.pos() == _expected_corner(qapp, cfg)


def _write_window_settings(tmp_path: Path, window: dict) -> None:
    (tmp_path / "settings.yaml").write_text(
        yaml.safe_dump({"window": window}, allow_unicode=True),
        encoding="utf-8",
    )


def test_never_minimize_restores(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    _write_window_settings(tmp_path, {"never_minimize": True})
    win = _make_window(tmp_path, {"x": 120, "y": 140, "facing": "left"})
    before = win.pos()
    win.setWindowState(Qt.WindowState.WindowMinimized)
    for _ in range(10):
        QApplication.processEvents()
    assert win.isMinimized() is False
    assert win.isVisible() is True
    assert win.pos() == before


def test_never_minimize_off_stays_minimized(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    # desktop_reassert тоже разворачивает — для этого теста его выключаем
    _write_window_settings(tmp_path, {"never_minimize": False, "desktop_reassert": False})
    win = _make_window(tmp_path, {"x": 120, "y": 140, "facing": "left"})
    win.setWindowState(Qt.WindowState.WindowMinimized)
    for _ in range(10):
        QApplication.processEvents()
    assert win.isMinimized() is True


def test_tool_window_flag(window: FlyWindow) -> None:
    assert window.windowFlags() & Qt.WindowType.Tool


def test_desktop_reassert_timer_on(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    _write_window_settings(tmp_path, {"desktop_reassert": True, "desktop_reassert_ms": 500})
    win = _make_window(tmp_path)
    assert win._desktop_reassert_timer is not None
    assert win._desktop_reassert_timer.isActive()
    assert win._desktop_reassert_timer.interval() == 500


def test_desktop_reassert_timer_off(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    _write_window_settings(tmp_path, {"desktop_reassert": False})
    win = _make_window(tmp_path)
    assert win._desktop_reassert_timer is None or not win._desktop_reassert_timer.isActive()


def test_desktop_reassert_raises_when_desktop_on_top(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qapp
    _write_window_settings(tmp_path, {"desktop_reassert": True})
    win = _make_window(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(win, "_is_desktop_on_top", lambda: True)
    monkeypatch.setattr(win, "_raise_without_activate", lambda: calls.append(1))
    win._on_desktop_reassert()
    assert calls == []  # hysteresis: нужно 2 подряд
    win._on_desktop_reassert()
    assert calls == [1]


def test_desktop_reassert_no_raise_when_not_desktop(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qapp
    _write_window_settings(tmp_path, {"desktop_reassert": True})
    win = _make_window(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(win, "_is_desktop_on_top", lambda: False)
    monkeypatch.setattr(win, "_raise_without_activate", lambda: calls.append(1))
    win._on_desktop_reassert()
    win._on_desktop_reassert()
    assert calls == []


def test_desktop_reassert_releases_when_adopted_and_show_desktop_ends(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qapp
    _write_window_settings(tmp_path, {"desktop_reassert": True})
    win = _make_window(tmp_path)
    win._desktop_adopted = True
    win._desktop_native_hwnd = 1
    releases: list[int] = []
    monkeypatch.setattr(win, "_is_adopted_native", lambda: True)
    monkeypatch.setattr(win, "_is_desktop_on_top", lambda: False)
    monkeypatch.setattr(
        win, "_release_desktop_parent", lambda **_kwargs: releases.append(1)
    )
    monkeypatch.setattr(win, "_raise_without_activate", lambda: releases.append(99))
    win._on_desktop_reassert()
    win._on_desktop_reassert()
    assert releases == []  # hysteresis: нужно 3 подряд
    win._on_desktop_reassert()
    assert releases == [1]


def test_desktop_reassert_keeps_adopt_when_loco_blocks_but_desktop_covers(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Полёт сам по себе не отпускает Progman — иначе Show Desktop снова прячет муху."""
    del qapp
    _write_window_settings(tmp_path, {"desktop_reassert": True})
    win = _make_window(tmp_path)
    win._desktop_adopted = True
    win._desktop_native_hwnd = 1

    class _Loco:
        def blocks_desktop_reassert(self) -> bool:
            return True

    win._locomotion = _Loco()  # type: ignore[assignment]
    releases: list[int] = []
    monkeypatch.setattr(win, "_is_desktop_on_top", lambda: True)
    monkeypatch.setattr(win, "_is_adopted_native", lambda: True)
    monkeypatch.setattr(
        win, "_release_desktop_parent", lambda **_kwargs: releases.append(1)
    )
    win._on_desktop_reassert()
    assert releases == []


def test_desktop_reassert_no_adopt_when_loco_blocks_and_not_desktop(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qapp
    _write_window_settings(tmp_path, {"desktop_reassert": True})
    win = _make_window(tmp_path)

    class _Loco:
        def blocks_desktop_reassert(self) -> bool:
            return True

    win._locomotion = _Loco()  # type: ignore[assignment]
    calls: list[int] = []
    monkeypatch.setattr(win, "_is_desktop_on_top", lambda: False)
    monkeypatch.setattr(win, "_raise_without_activate", lambda: calls.append(1))
    win._on_desktop_reassert()
    win._on_desktop_reassert()
    assert calls == []


def test_desktop_reassert_restores_minimized(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    _write_window_settings(
        tmp_path,
        {"desktop_reassert": True, "never_minimize": False},
    )
    win = _make_window(tmp_path, {"x": 120, "y": 140, "facing": "left"})
    win.setWindowState(Qt.WindowState.WindowMinimized)
    QApplication.processEvents()
    assert win.isMinimized() is True
    win._on_desktop_reassert()
    QApplication.processEvents()
    assert win.isMinimized() is False
    assert win.isVisible() is True


def test_rotate_angle_zero_pixel_perfect(window: FlyWindow) -> None:
    frame = window._current_frame
    assert frame is not None and not frame.isNull()
    got = rotate_sprite_frame(frame, 0.0, frame_name="idle")
    assert got.cacheKey() == frame.cacheKey()
    assert got.width() == frame.width()
    assert got.height() == frame.height()
    assert got.toImage() == frame.toImage()
