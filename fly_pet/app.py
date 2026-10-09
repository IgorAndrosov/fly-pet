"""Сборка и запуск GUI питомца."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from fly_pet.animation import load_frames
from fly_pet.config import Config, load_config
from fly_pet.locomotion import Locomotion, Rect, Win32WindowApi
from fly_pet.logging_setup import setup_logging
from fly_pet.needs import Needs
from fly_pet.phrases import pick
from fly_pet.state import StateStore
from fly_pet.window import FlyWindow

_SAVE_INTERVAL_SEC = 60.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _mode_to_anim(mode: str) -> str:
    if mode == "eat":
        return "chew"
    return mode if mode in {"idle", "walk", "sleep"} else "idle"


def _desktop_rect() -> Rect:
    screen = QApplication.primaryScreen()
    if screen is None:
        return Rect(0, 0, 1920, 1080)
    geo = screen.availableGeometry()
    return Rect(int(geo.x()), int(geo.y()), int(geo.x() + geo.width()), int(geo.y() + geo.height()))


def build_app(config: Config, state_store: StateStore) -> QApplication:
    """Собирает QApplication, таймеры анимации/потребностей и показывает FlyWindow."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    frames = load_frames(_repo_root())
    state = state_store.load()
    # Стартовые needs из конфига, если state ещё дефолтный и файла не было — уже в state;
    # не перетираем сохранённые значения.
    window = FlyWindow(config, state_store, state, frames)
    needs = Needs(state.needs, config.needs, mode=state.mode)
    if needs.mode != state.mode:
        state.mode = needs.mode
        window.set_state(_mode_to_anim(needs.mode))

    if config.walk.enabled:
        api = Win32WindowApi()
        loco = Locomotion(
            config.walk,
            pet_width=config.window.width,
            pet_height=config.window.height,
            desktop=_desktop_rect(),
            api=api,
            our_hwnd_getter=lambda: int(window.winId()) if window.winId() else 0,
        )
        pos = window.pos()
        loco.resume_from_desktop(float(pos.x()), float(pos.y()))
        window.set_locomotion(loco)

    last_tick = time.monotonic()
    last_save = 0.0  # первый тик сохранит сразу

    def on_needs_tick() -> None:
        nonlocal last_tick, last_save
        now = time.monotonic()
        dt = now - last_tick
        last_tick = now
        prev_needs_mode = needs.mode
        events = needs.tick(dt)
        state.needs = needs.snapshot()
        if needs.mode != prev_needs_mode:
            if needs.mode == "sleep":
                state.mode = "sleep"
                window.set_state("sleep")
            elif prev_needs_mode == "sleep":
                loco = window._locomotion
                state.mode = loco.mode.value if loco is not None else "idle"
                window.set_state("idle")
        for event in events:
            phrase = pick(event.name)
            if phrase:
                window.say(phrase)
        if now - last_save >= _SAVE_INTERVAL_SEC:
            state_store.save(state)
            last_save = now

    needs_interval_ms = max(1, int(round(config.tick.needs_interval_sec * 1000)))
    needs_timer = QTimer(app)
    needs_timer.setInterval(needs_interval_ms)
    needs_timer.timeout.connect(on_needs_tick)
    needs_timer.start()

    app._fly_window = window  # type: ignore[attr-defined]
    app._needs = needs  # type: ignore[attr-defined]
    app._needs_timer = needs_timer  # type: ignore[attr-defined]
    app._animation_timer = window._timer  # type: ignore[attr-defined]
    app._on_needs_tick = on_needs_tick  # type: ignore[attr-defined]
    app.aboutToQuit.connect(window.persist_state)

    window.show()
    greeting = pick("greeting")
    if greeting:
        window.say(greeting)
    return app


def run(config: Config | None = None) -> int:
    """Загружает конфиг/состояние, показывает окно и крутит цикл событий."""
    cfg = config if config is not None else load_config()
    setup_logging(cfg.data_dir)
    store = StateStore(cfg.data_dir)
    print(f"питомец запущен, данные: {cfg.data_dir}")
    app = build_app(cfg, store)
    return int(app.exec())
