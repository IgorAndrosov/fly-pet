"""Тесты петли потребностей."""

from __future__ import annotations

from pathlib import Path

from fly_pet import config as config_mod
from fly_pet.config import load_config
from fly_pet.needs import Needs

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path: Path, **needs_overrides):
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    fields = {
        "start": cfg.needs.start,
        "decay_per_minute": dict(cfg.needs.decay_per_minute),
        "sleep_energy_below": cfg.needs.sleep_energy_below,
        "complain_hunger_above": cfg.needs.complain_hunger_above,
        "bored_attention_above": cfg.needs.bored_attention_above,
        "wake_energy_above": cfg.needs.wake_energy_above,
        "event_cooldown_sec": cfg.needs.event_cooldown_sec,
        "restore_per_minute": dict(cfg.needs.restore_per_minute),
    }
    fields.update(needs_overrides)
    return config_mod.NeedsConfig(**fields)


def test_decay_equals_rate_per_minute(tmp_path: Path) -> None:
    needs_cfg = _cfg(tmp_path)
    n = Needs(
        {"hunger": 40.0, "energy": 70.0, "mood": 60.0, "attention": 50.0},
        needs_cfg,
    )
    n.tick(60.0)
    snap = n.snapshot()
    assert abs(snap["hunger"] - (40.0 + needs_cfg.decay_per_minute["hunger"])) < 1e-6
    assert abs(snap["energy"] - (70.0 - needs_cfg.decay_per_minute["energy"])) < 1e-6
    assert abs(snap["mood"] - (60.0 - needs_cfg.decay_per_minute["mood"])) < 1e-6
    assert abs(snap["attention"] - (50.0 + needs_cfg.decay_per_minute["attention"])) < 1e-6


def test_clamp_at_zero(tmp_path: Path) -> None:
    needs_cfg = _cfg(
        tmp_path,
        decay_per_minute={"hunger": 0.0, "energy": 0.0, "mood": 50.0, "attention": 0.0},
    )
    n = Needs(
        {"hunger": 10.0, "energy": 100.0, "mood": 1.0, "attention": 10.0},
        needs_cfg,
        mode="idle",
    )
    n.tick(60.0)
    assert n.snapshot()["mood"] == 0.0


def test_hungry_crossing_and_cooldown(tmp_path: Path) -> None:
    needs_cfg = _cfg(
        tmp_path,
        event_cooldown_sec=300.0,
        complain_hunger_above=70.0,
        decay_per_minute={"hunger": 2.0, "energy": 0.0, "mood": 0.0, "attention": 0.0},
    )
    n = Needs(
        {"hunger": 69.0, "energy": 80.0, "mood": 50.0, "attention": 50.0},
        needs_cfg,
    )
    events = n.tick(60.0)  # 69 → 71
    assert [e.name for e in events].count("hungry") == 1

    events2 = n.tick(60.0)
    assert "hungry" not in [e.name for e in events2]

    n._values["hunger"] = 69.0
    n._time_sec += 300.0
    events3 = n.tick(60.0)
    assert "hungry" in [e.name for e in events3]


def test_sleepy_sleep_and_wake(tmp_path: Path) -> None:
    needs_cfg = _cfg(
        tmp_path,
        sleep_energy_below=20.0,
        wake_energy_above=35.0,
        event_cooldown_sec=0.0,
        decay_per_minute={"hunger": 0.8, "energy": 5.0, "mood": 0.0, "attention": 0.0},
        restore_per_minute={"energy": 20.0},
    )
    n = Needs(
        {"hunger": 40.0, "energy": 22.0, "mood": 50.0, "attention": 50.0},
        needs_cfg,
        mode="idle",
    )
    events = n.tick(60.0)  # energy 22 → 17
    assert "sleepy" in [e.name for e in events]
    assert n.mode == "sleep"

    n2 = Needs(
        {"hunger": 40.0, "energy": 34.0, "mood": 50.0, "attention": 50.0},
        needs_cfg,
        mode="sleep",
    )
    ev = n2.tick(60.0)  # 34 + 20 → 54
    assert "woke" in [e.name for e in ev]
    assert n2.mode == "idle"


def test_sleep_restores_energy_hunger_keeps_growing(tmp_path: Path) -> None:
    """Пока спит — energy растёт, hunger продолжает расти (нужда)."""
    needs_cfg = _cfg(
        tmp_path,
        decay_per_minute={"hunger": 1.5, "energy": 0.5, "mood": 0.0, "attention": 0.0},
        restore_per_minute={"energy": 3.0},
    )
    n = Needs(
        {"hunger": 40.0, "energy": 10.0, "mood": 50.0, "attention": 50.0},
        needs_cfg,
        mode="sleep",
    )
    before = n.snapshot()
    n.tick(60.0)
    after = n.snapshot()
    assert after["energy"] == before["energy"] + 3.0
    assert after["hunger"] == before["hunger"] + 1.5
