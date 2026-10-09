"""Тесты загрузки спрайтов и AnimationPlayer (headless)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from fly_pet.animation import ANIMATION_STATES, AnimationError, AnimationPlayer, load_frames

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(scope="module")
def frames(qapp: QApplication) -> dict:
    del qapp
    return load_frames(REPO_ROOT)


def test_load_all_frames(frames: dict) -> None:
    total = sum(len(v) for v in frames.values())
    assert total == 22
    assert set(frames) == set(ANIMATION_STATES)
    assert len(frames["idle"]) == 2
    assert len(frames["walk"]) == 6
    assert len(frames["sleep"]) == 3
    assert len(frames["chew"]) == 2
    assert len(frames["fly"]) == 4
    assert len(frames["land"]) == 1
    assert len(frames["rub"]) == 4
    for state in ANIMATION_STATES:
        assert frames[state], f"пустая группа {state}"
        for pixmap in frames[state]:
            assert not pixmap.isNull()
            assert pixmap.width() == 64
            assert pixmap.height() == 64


def test_rub_state_cycles(frames: dict) -> None:
    player = AnimationPlayer(frames, fps=8)
    player.set_state("rub")
    assert player.state == "rub"
    assert len(frames["rub"]) == 4
    keys = [player.advance().cacheKey() for _ in range(4)]
    assert len(set(keys)) == 4
    assert player.advance().cacheKey() == keys[0]


def test_advance_cycles(frames: dict) -> None:
    player = AnimationPlayer(frames, fps=8)
    player.set_state("idle")
    first = player.advance()
    second = player.advance()
    third = player.advance()
    assert first.cacheKey() != second.cacheKey()
    assert third.cacheKey() == first.cacheKey()


def test_set_state_switches(frames: dict) -> None:
    player = AnimationPlayer(frames, fps=8)
    player.set_state("walk")
    assert player.current_frame().cacheKey() == frames["walk"][0].cacheKey()
    player.set_state("sleep")
    assert player.state == "sleep"
    # после смены начинаем с первого кадра
    assert player.current_frame().cacheKey() == frames["sleep"][0].cacheKey()
    assert player.current_frame().cacheKey() != frames["walk"][0].cacheKey()


def test_unknown_state_raises(frames: dict) -> None:
    player = AnimationPlayer(frames, fps=8)
    with pytest.raises(AnimationError, match="Неизвестное состояние"):
        player.set_state("dance")


def test_fly_and_land_states(frames: dict) -> None:
    player = AnimationPlayer(frames, fps=8)
    player.set_state("fly")
    assert player.state == "fly"
    assert len(frames["fly"]) == 4
    player.set_state("land")
    assert player.state == "land"
    assert len(frames["land"]) == 1


def test_missing_assets_dir(tmp_path: Path, qapp: QApplication) -> None:
    del qapp
    with pytest.raises(AnimationError, match="не найден"):
        load_frames(tmp_path)
