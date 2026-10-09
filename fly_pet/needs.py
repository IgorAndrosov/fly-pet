"""Петля потребностей: decay, пороги, сон/пробуждение."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fly_pet.state import NEED_KEYS, _clamp_need

if TYPE_CHECKING:
    from fly_pet.config import NeedsConfig

# hunger/attention растут (нужда усиливается), energy/mood падают.
_GROWING = frozenset({"hunger", "attention"})


@dataclass(frozen=True)
class NeedsEvent:
    """Событие пересечения порога потребностей."""

    name: str


class Needs:
    """Словарь потребностей 0–100 с тиком decay и событиями порогов."""

    def __init__(
        self,
        values: dict[str, float],
        config: NeedsConfig,
        mode: str = "idle",
    ) -> None:
        self._config = config
        self._values: dict[str, float] = {
            key: _clamp_need(values.get(key, 0.0)) for key in NEED_KEYS
        }
        for key, value in values.items():
            if key not in self._values:
                self._values[key] = _clamp_need(value)
        self._mode = (
            mode
            if mode in {"idle", "walk", "eat", "sleep", "desktop", "window"}
            else "idle"
        )
        self._last_event_at: dict[str, float] = {}
        self._time_sec = 0.0

    @property
    def mode(self) -> str:
        return self._mode

    def force_sleep(self) -> None:
        """Принудительный сон (меню трея)."""
        self._mode = "sleep"

    def force_wake(self) -> None:
        """Принудительное пробуждение (меню трея)."""
        if self._mode == "sleep":
            self._mode = "idle"

    def snapshot(self) -> dict[str, float]:
        return {key: float(self._values[key]) for key in NEED_KEYS}

    def tick(self, dt_sec: float) -> list[NeedsEvent]:
        """Применяет decay/восстановление за dt_sec, возвращает события порогов."""
        if dt_sec < 0:
            dt_sec = 0.0
        self._time_sec += dt_sec
        prev = dict(self._values)
        factor = dt_sec / 60.0
        decay = self._config.decay_per_minute

        sleeping = self._mode == "sleep"
        for key in NEED_KEYS:
            rate = float(decay.get(key, 0.0))
            if key == "energy" and sleeping:
                rate = float(self._config.restore_per_minute.get("energy", 0.0))
                self._values[key] = _clamp_need(self._values[key] + rate * factor)
            elif key in _GROWING:
                self._values[key] = _clamp_need(self._values[key] + rate * factor)
            else:
                self._values[key] = _clamp_need(self._values[key] - rate * factor)

        events: list[NeedsEvent] = []
        events.extend(self._check_threshold_events(prev))
        events.extend(self._update_sleep_mode(prev))
        return events

    def _cooldown_ok(self, name: str) -> bool:
        last = self._last_event_at.get(name)
        if last is None:
            return True
        return (self._time_sec - last) >= float(self._config.event_cooldown_sec)

    def _emit(self, name: str, events: list[NeedsEvent]) -> None:
        if not self._cooldown_ok(name):
            return
        self._last_event_at[name] = self._time_sec
        events.append(NeedsEvent(name=name))

    def _check_threshold_events(self, prev: dict[str, float]) -> list[NeedsEvent]:
        events: list[NeedsEvent] = []
        hunger_th = self._config.complain_hunger_above
        if prev["hunger"] <= hunger_th < self._values["hunger"]:
            self._emit("hungry", events)

        energy_th = self._config.sleep_energy_below
        if prev["energy"] >= energy_th > self._values["energy"]:
            self._emit("sleepy", events)

        attention_th = self._config.bored_attention_above
        if prev["attention"] <= attention_th < self._values["attention"]:
            self._emit("bored", events)
        return events

    def _update_sleep_mode(self, prev: dict[str, float]) -> list[NeedsEvent]:
        events: list[NeedsEvent] = []
        energy = self._values["energy"]
        sleep_below = self._config.sleep_energy_below
        wake_above = self._config.wake_energy_above

        if self._mode != "sleep" and energy < sleep_below:
            self._mode = "sleep"
        elif self._mode == "sleep" and energy > wake_above:
            self._mode = "idle"
            # Пробуждение — отдельное событие без cooldown-ограничения порогов
            events.append(NeedsEvent(name="woke"))
            self._last_event_at["woke"] = self._time_sec

        # Если sleepy ещё не пойман как пересечение (старт уже ниже порога) —
        # режим всё равно sleep; событие только на пересечении.
        del prev
        return events
