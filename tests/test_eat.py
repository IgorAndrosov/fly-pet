"""Тесты карантина и имён иконок демо-поедания."""

from __future__ import annotations

from pathlib import Path

from fly_pet.desktop_icons import icon_display_names
from fly_pet.eat import DEMO_FILENAME, create_demo_file, move_to_quarantine, quarantine_path


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


def test_quarantine_collision_gets_timestamp(tmp_path: Path) -> None:
    first = quarantine_path(tmp_path, DEMO_FILENAME)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("old", encoding="utf-8")
    second = quarantine_path(tmp_path, DEMO_FILENAME)
    assert second != first
    assert DEMO_FILENAME.split(".")[0] in second.name
    assert second.suffix == ".txt"
