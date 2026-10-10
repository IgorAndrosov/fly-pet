"""Сборка и запуск GUI питомца."""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from fly_pet.animation import load_frames
from fly_pet.brain import BrainClient, BrainResult
from fly_pet.config import Config, load_config
from fly_pet.envload import load_env_file
from fly_pet.locomotion import Locomotion, Rect, Win32WindowApi
from fly_pet.logging_setup import setup_logging
from fly_pet.needs import Needs
from fly_pet.paths import resource_dir
from fly_pet.phrases import pick
from fly_pet.state import StateStore
from fly_pet.tray import TrayController
from fly_pet.window import FlyWindow

logger = logging.getLogger("fly_pet")


class _BrainBridge(QObject):
    """Мост: результат мозга из рабочего потока → GUI-поток."""

    result_ready = pyqtSignal(object)

_SAVE_INTERVAL_SEC = 60.0


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

    # Не завершать приложение при закрытии последнего окна (есть трей).
    app.setQuitOnLastWindowClosed(False)

    frames = load_frames(resource_dir())
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

    allowed_actions = frozenset(a.name for a in config.actions if a.enabled)
    brain_client = BrainClient(config.llm, allowed_actions)
    brain_bridge = _BrainBridge(app)
    brain_busy = False
    brain_lock = threading.Lock()

    def on_brain_result(result: object) -> None:
        nonlocal brain_busy
        with brain_lock:
            brain_busy = False
        if not isinstance(result, BrainResult):
            return
        state.last_brain_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if result.ok:
            state.stats["llm_calls"] = int(state.stats.get("llm_calls", 0)) + 1
            state.stats["llm_last_provider"] = result.provider
            if result.failed_providers:
                state.stats["llm_failures"] = int(state.stats.get("llm_failures", 0)) + 1
        else:
            state.stats["llm_failures"] = int(state.stats.get("llm_failures", 0)) + 1
        ttl_ms = int(round(config.llm.phrase_ttl_sec * 1000))
        if result.say:
            window.say(result.say, ttl_ms=ttl_ms)
        if result.action:
            logger.info("мозг предложил действие «%s» (пока только лог)", result.action)

    brain_bridge.result_ready.connect(on_brain_result)

    def on_brain_tick() -> None:
        nonlocal brain_busy
        if not config.llm.enabled:
            return
        with brain_lock:
            if brain_busy:
                return
            brain_busy = True
        needs_snap = dict(state.needs)
        mode_snap = state.mode

        def work() -> None:
            try:
                result = brain_client.ask(needs=needs_snap, mode=mode_snap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("мозг упал неожиданно: %s: %s", type(exc).__name__, exc)
                result = BrainResult(
                    ok=False,
                    say=pick("brain_fail") or "Жужжу без облака.",
                    action=None,
                    provider=None,
                    elapsed_ms=0,
                    failed_providers=(),
                    from_template=True,
                )
            brain_bridge.result_ready.emit(result)

        threading.Thread(target=work, name="fly-brain", daemon=True).start()

    brain_interval_ms = max(1, int(round(config.tick.brain_interval_sec * 1000)))
    brain_timer = QTimer(app)
    brain_timer.setInterval(brain_interval_ms)
    brain_timer.timeout.connect(on_brain_tick)
    if config.llm.enabled:
        brain_timer.start()

    def quit_app() -> None:
        window.persist_state()
        needs_timer.stop()
        brain_timer.stop()
        anim_timer = window._timer
        if anim_timer is not None:
            anim_timer.stop()
        loco_timer = window._loco_timer
        if loco_timer is not None:
            loco_timer.stop()
        reassert = window._desktop_reassert_timer
        if reassert is not None:
            reassert.stop()
        if tray is not None:
            tray.hide()
        window.hide()
        app.quit()

    tray = TrayController(
        window=window,
        needs=needs,
        state=state,
        on_quit=quit_app,
        parent=window,
    )

    app._fly_window = window  # type: ignore[attr-defined]
    app._needs = needs  # type: ignore[attr-defined]
    app._needs_timer = needs_timer  # type: ignore[attr-defined]
    app._brain_timer = brain_timer  # type: ignore[attr-defined]
    app._on_needs_tick = on_needs_tick  # type: ignore[attr-defined]
    app._on_brain_tick = on_brain_tick  # type: ignore[attr-defined]
    app._on_brain_result = on_brain_result  # type: ignore[attr-defined]
    app._brain_client = brain_client  # type: ignore[attr-defined]
    app._animation_timer = window._timer  # type: ignore[attr-defined]
    app._tray = tray  # type: ignore[attr-defined]
    app._quit_app = quit_app  # type: ignore[attr-defined]
    app.aboutToQuit.connect(window.persist_state)

    window.show()
    greeting = pick("greeting")
    if greeting:
        window.say(greeting)
    return app


def run(config: Config | None = None) -> int:
    """Загружает конфиг/состояние, показывает окно и крутит цикл событий."""
    cfg = config if config is not None else load_config()
    load_env_file(cfg.paths.hermes_env)
    setup_logging(cfg.data_dir)
    store = StateStore(cfg.data_dir)
    print(f"питомец запущен, данные: {cfg.data_dir}")
    app = build_app(cfg, store)
    return int(app.exec())
