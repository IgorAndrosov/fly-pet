"""Тесты сборки приложения: таймеры needs/animation и тик потребностей."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from fly_pet.app import build_app
from fly_pet.config import load_config
from fly_pet.state import StateStore, default_state

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_timers_from_config(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    store = StateStore(tmp_path)
    store.save(default_state())
    app = build_app(cfg, store)

    anim_ms = max(1, int(round(1000 / cfg.tick.animation_fps)))
    needs_ms = max(1, int(round(cfg.tick.needs_interval_sec * 1000)))
    assert app._animation_timer.interval() == anim_ms  # type: ignore[attr-defined]
    assert app._needs_timer.interval() == needs_ms  # type: ignore[attr-defined]
    assert app._animation_timer.isActive()  # type: ignore[attr-defined]
    assert app._needs_timer.isActive()  # type: ignore[attr-defined]

    app._fly_window.close()  # type: ignore[attr-defined]


def test_needs_tick_updates_and_saves(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qapp
    clock = {"t": 1_000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    store = StateStore(tmp_path)
    state = default_state()
    state.needs = {"hunger": 40.0, "energy": 70.0, "mood": 60.0, "attention": 50.0}
    store.save(state)

    app = build_app(cfg, store)
    before = dict(app._needs.snapshot())  # type: ignore[attr-defined]

    clock["t"] = 1_060.0
    app._on_needs_tick()  # type: ignore[attr-defined]

    after = app._needs.snapshot()  # type: ignore[attr-defined]
    assert after != before
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert raw["needs"]["hunger"] == after["hunger"]
    assert raw["needs"]["energy"] == after["energy"]

    app._fly_window.close()  # type: ignore[attr-defined]
