"""Юнит-тесты маски укусов и маршрута поедания."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from fly_pet.eat_overlay import (
    Bite,
    IconEatOverlay,
    _crop_from_full,
    apply_bite,
    bite_interval_sec,
    compose_frame,
    eaten_end_layers,
    make_full_mask,
    opaque_bbox,
    plan_bites,
    remaining_ratio,
)


def _solid_icon(w: int = 48, h: int = 48) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    for y in range(8, h - 8):
        for x in range(8, w - 8):
            img.setPixel(x, y, QColor(40, 120, 200, 255).rgba())
    return img


def test_opaque_bbox_and_plan_bites_count() -> None:
    icon = _solid_icon(56, 64)
    box = opaque_bbox(icon)
    assert box is not None
    assert box.width() >= 30
    bites = plan_bites(icon, radius=10)
    assert 15 <= len(bites) <= 30


def test_bite_reduces_remaining() -> None:
    icon = _solid_icon(40, 40)
    mask = make_full_mask(icon)
    before = remaining_ratio(mask, icon)
    assert before > 0.9
    apply_bite(mask, Bite(x=20, y=20, r=12, angle_deg=45.0))
    after = remaining_ratio(mask, icon)
    assert after < before


def test_bite_interval_clamped() -> None:
    for n in (15, 20, 30):
        iv = bite_interval_sec(n)
        assert 0.25 <= iv <= 0.4


def test_final_frame_after_last_bite_is_opaque_background() -> None:
    """После последнего укуса icon_img пуст, bg_out — альфа 255 на всей клетке."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    del app

    bg = QImage(10, 10, QImage.Format.Format_ARGB32)
    rgb: list[tuple[int, int, int]] = []
    for y in range(10):
        for x in range(10):
            color = QColor(10 + x, 40 + y, 80, 0)
            bg.setPixelColor(x, y, color)
            rgb.append((color.red(), color.green(), color.blue()))
    icon = QImage(10, 10, QImage.Format.Format_ARGB32)
    icon.fill(QColor(255, 255, 255, 255))
    overlay = IconEatOverlay(QRect(0, 0, 10, 10), bg, icon)
    overlay.apply_bite_and_repaint(Bite(x=5, y=5, r=8, angle_deg=0.0))
    overlay.clear_mask()

    bg_out, icon_img = eaten_end_layers(bg)
    frame = overlay.frame_image()
    assert bg_out.size() == frame.size() == icon.size()
    i = 0
    for y in range(10):
        for x in range(10):
            icon_px = icon_img.pixelColor(x, y)
            assert icon_px.alpha() == 0
            bg_px = bg_out.pixelColor(x, y)
            assert bg_px.alpha() == 255
            assert (bg_px.red(), bg_px.green(), bg_px.blue()) == rgb[i]
            frame_px = frame.pixelColor(x, y)
            assert frame_px.alpha() == 255
            assert (frame_px.red(), frame_px.green(), frame_px.blue()) == rgb[i]
            i += 1
    overlay.close()


def test_eat_overlay_exports_stay_importable() -> None:
    assert callable(_crop_from_full)
    assert callable(compose_frame)
    assert callable(eaten_end_layers)
