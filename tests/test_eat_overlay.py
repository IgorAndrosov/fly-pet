"""Юнит-тесты маски укусов и маршрута поедания."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage

from fly_pet.eat_overlay import (
    Bite,
    apply_bite,
    bite_interval_sec,
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
