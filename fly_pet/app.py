"""Сборка и запуск GUI питомца."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from fly_pet.animation import load_frames
from fly_pet.config import Config, load_config
from fly_pet.logging_setup import setup_logging
from fly_pet.state import StateStore
from fly_pet.window import FlyWindow


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_app(config: Config, state_store: StateStore) -> QApplication:
    """Собирает QApplication, загружает кадры и показывает FlyWindow."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    frames = load_frames(_repo_root())
    state = state_store.load()
    window = FlyWindow(config, state_store, state, frames)
    app._fly_window = window  # type: ignore[attr-defined]
    app.aboutToQuit.connect(window.persist_state)
    window.show()
    return app


def run(config: Config | None = None) -> int:
    """Загружает конфиг/состояние, показывает окно и крутит цикл событий."""
    cfg = config if config is not None else load_config()
    setup_logging(cfg.data_dir)
    store = StateStore(cfg.data_dir)
    print(f"питомец запущен, данные: {cfg.data_dir}")
    app = build_app(cfg, store)
    return int(app.exec())
