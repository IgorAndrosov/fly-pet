"""Headless-тесты SpeechBubble."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from fly_pet.bubble import SpeechBubble
from fly_pet.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def bubble(qapp: QApplication, tmp_path: Path) -> SpeechBubble:
    del qapp
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    b = SpeechBubble(cfg.bubble)
    return b


def test_say_shows_and_sizes(bubble: SpeechBubble, tmp_path: Path) -> None:
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    bubble.say("Привет")
    assert bubble.isVisible()
    short_w = bubble.width()
    bubble.say("Очень длинная реплика про голод и скуку на рабочем столе " * 3)
    assert bubble.isVisible()
    assert bubble.width() >= short_w
    assert bubble.width() <= cfg.bubble.max_width


def test_empty_text_hides(bubble: SpeechBubble) -> None:
    bubble.say("Есть текст")
    assert bubble.isVisible()
    bubble.say("")
    assert not bubble.isVisible()
    bubble.say("   ")
    assert not bubble.isVisible()


def test_hide_by_timer_handler(bubble: SpeechBubble) -> None:
    bubble.say("Тик", ttl_ms=60_000)
    assert bubble.isVisible()
    bubble._hide_timer.timeout.emit()
    assert not bubble.isVisible()


def test_place_near_stays_in_available_geometry(qapp: QApplication, bubble: SpeechBubble) -> None:
    screen = qapp.primaryScreen()
    assert screen is not None
    geo = screen.availableGeometry()

    anchor = QWidget()
    anchor.resize(96, 96)
    bubble.attach_to(anchor)
    bubble.say("Край")

    edges = [
        QPoint(geo.left(), geo.top()),
        QPoint(geo.right() - 96, geo.top()),
        QPoint(geo.left(), geo.bottom() - 96),
        QPoint(geo.right() - 96, geo.bottom() - 96),
    ]
    for pos in edges:
        anchor.move(pos)
        bubble.place_near(anchor)
        rect = QRect(bubble.pos(), bubble.size())
        assert geo.contains(rect), f"облачко вне экрана при якоре {pos}: {rect} vs {geo}"
