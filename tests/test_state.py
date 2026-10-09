"""Тесты StateStore и PetState."""

from __future__ import annotations

import json
from pathlib import Path

from fly_pet.state import PetState, StateStore, default_state


def test_default_when_missing(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    assert state.version == 1
    assert state.needs["hunger"] == 40
    assert state.mode == "idle"
    assert state.eaten == 0
    assert not (tmp_path / "state.json").exists()


def test_save_load_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = default_state()
    state.needs["hunger"] = 12
    state.mode = "walk"
    state.eaten = 3
    state.extras["custom_flag"] = True
    store.save(state)

    loaded = store.load()
    assert loaded.needs["hunger"] == 12
    assert loaded.mode == "walk"
    assert loaded.eaten == 3
    assert loaded.extras["custom_flag"] is True

    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["custom_flag"] is True


def test_needs_clamped(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    state = default_state()
    state.needs["hunger"] = -10
    state.needs["energy"] = 150
    store.save(state)
    loaded = store.load()
    assert loaded.needs["hunger"] == 0
    assert loaded.needs["energy"] == 100


def test_broken_json_renamed(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{not-json", encoding="utf-8")
    store = StateStore(tmp_path)
    state = store.load()
    assert state.needs == default_state().needs
    assert not state_path.exists()
    broken = list(tmp_path.glob("state.json.broken-*"))
    assert len(broken) == 1
    assert broken[0].read_text(encoding="utf-8") == "{not-json"


def test_atomic_save_no_temp_left(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.save(default_state())
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftovers == []
    assert (tmp_path / "state.json").is_file()


def test_unknown_fields_preserved(tmp_path: Path) -> None:
    raw = default_state().to_dict()
    raw["future_field"] = {"nested": 1}
    (tmp_path / "state.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    store = StateStore(tmp_path)
    state = store.load()
    store.save(state)
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["future_field"] == {"nested": 1}
