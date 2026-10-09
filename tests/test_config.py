"""Тесты загрузки и слияния конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fly_pet.config import ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_defaults(tmp_path: Path) -> None:
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    assert cfg.desktop.allowed_extensions == []
    assert cfg.window.always_on_top is False
    assert cfg.window.desktop_reassert is True
    assert cfg.window.desktop_reassert_ms == 800
    assert cfg.safety.dry_run is True
    assert cfg.needs.start["hunger"] == 40
    assert cfg.desktop.min_age_days == 7
    assert len(cfg.actions) == 4
    assert cfg.walk.enabled is True
    assert cfg.walk.tick_ms == 40
    assert cfg.walk.desktop_stay_sec == (8.0, 25.0)
    assert cfg.walk.burst_px == (28.0, 120.0)
    assert cfg.walk.burst_speed_px_s == 165.0
    assert cfg.walk.dash_animation_fps == 16
    assert cfg.walk.pause_sec == (0.25, 1.4)
    assert cfg.walk.groom_chance == 0.45
    assert cfg.walk.groom_sec == (1.0, 2.6)
    assert cfg.walk.long_burst_chance == 0.12
    assert cfg.walk.long_burst_px == (180.0, 380.0)
    assert cfg.walk.turn_on_pause_chance == 0.35
    assert cfg.walk.seed is None


def test_merge_settings_yaml(tmp_path: Path) -> None:
    settings = {
        "desktop": {
            "allowed_extensions": [".png", ".jpg"],
            "min_age_days": 14,
        }
    }
    (tmp_path / "settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True),
        encoding="utf-8",
    )
    cfg = load_config(repo_root=REPO_ROOT, data_dir=tmp_path)
    assert cfg.desktop.allowed_extensions == [".png", ".jpg"]
    assert cfg.desktop.min_age_days == 14
    # Остальные дефолты сохранились
    assert cfg.safety.dry_run is True
    assert cfg.window.always_on_top is False
    assert cfg.desktop.blocked_extensions == [".lnk", ".url", ".ini"]
    assert cfg.tick.needs_interval_sec == 30


def test_broken_yaml_raises(tmp_path: Path) -> None:
    (tmp_path / "settings.yaml").write_text("desktop: [\n  - broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="Битый YAML|YAML"):
        load_config(repo_root=REPO_ROOT, data_dir=tmp_path)


def test_fly_pet_data_dir_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLY_PET_DATA_DIR", str(tmp_path))
    cfg = load_config(repo_root=REPO_ROOT)
    assert cfg.data_dir == tmp_path.resolve()
    assert cfg.data_path("state.json") == tmp_path.resolve() / "state.json"
