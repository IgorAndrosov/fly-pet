"""Тесты карантина и имён иконок демо-поедания."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from fly_pet.desktop_icons import icon_display_names
from fly_pet.eat import (
    DEMO_FILENAME,
    DemoEatController,
    create_demo_file,
    move_to_quarantine,
    quarantine_path,
)
from fly_pet.state import StateStore, default_state


def test_icon_display_names_include_stem() -> None:
    names = icon_display_names(DEMO_FILENAME)
    assert "муха-демо.txt".casefold() in names
    assert "муха-демо".casefold() in names


def test_create_and_quarantine(tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    path = create_demo_file(desktop)
    assert path.is_file()
    assert path.name == DEMO_FILENAME
    dest = move_to_quarantine(path, tmp_path)
    assert not path.exists()
    assert dest.is_file()
    assert dest.parent == tmp_path / "trash"
    assert dest.name == DEMO_FILENAME


class _SayWindow:
    def __init__(self) -> None:
        self.said: list[str] = []
        self._locomotion = None

    def say(self, text: str, ttl_ms: int | None = None) -> None:
        del ttl_ms
        self.said.append(text)


def test_offscreen_icon_aborts_without_overlay(tmp_path: Path) -> None:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    del app
    desktop = tmp_path / "Desktop"
    path = create_demo_file(desktop)
    window = _SayWindow()
    store = StateStore(tmp_path)
    ctrl = DemoEatController(
        window=window,  # type: ignore[arg-type]
        state=default_state(),
        state_store=store,
        data_dir=tmp_path,
        icon_on_screen=lambda _icon: False,
    )
    ctrl._busy = True
    ctrl._demo_path = path
    ctrl._on_icon_for_cell(
        {
            "name": DEMO_FILENAME,
            "screen_x": 2100,
            "screen_y": 40,
            "cell": [76, 100],
        }
    )
    assert ctrl._busy is False
    assert ctrl._overlay is None
    assert ctrl._phase == "idle"
    assert not path.exists()
    assert (tmp_path / "trash" / DEMO_FILENAME).is_file()
    assert window.said == ["файл не на моём экране"]


def test_crop_from_full_does_not_depend_on_missing_imports() -> None:
    """Регрессия: NameError QPoint в _crop_from_full молча валил слот демо."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    del app
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QPixmap

    from fly_pet.eat_overlay import _crop_from_full

    pm = QPixmap(200, 150)
    pm.fill()
    img = _crop_from_full(pm, QRect(10, 20, 60, 40))
    assert (img.width(), img.height()) == (60, 40)


def test_quarantine_collision_gets_timestamp(tmp_path: Path) -> None:
    first = quarantine_path(tmp_path, DEMO_FILENAME)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("old", encoding="utf-8")
    second = quarantine_path(tmp_path, DEMO_FILENAME)
    assert second != first
    assert DEMO_FILENAME.split(".")[0] in second.name
    assert second.suffix == ".txt"
