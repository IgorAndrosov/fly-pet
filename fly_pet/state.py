"""Состояние питомца: загрузка и атомарное сохранение state.json."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("fly_pet")

STATE_VERSION = 1
NEED_KEYS = ("hunger", "energy", "mood", "attention")
MODES = frozenset({"idle", "walk", "eat", "sleep"})


def _clamp_need(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(100.0, number))


def default_needs() -> dict[str, float]:
    return {
        "hunger": 40.0,
        "energy": 70.0,
        "mood": 60.0,
        "attention": 50.0,
    }


def default_pose() -> dict[str, Any]:
    return {"x": 0, "y": 0, "facing": "left"}


def default_last_action() -> dict[str, Any]:
    return {"name": None, "at": None, "dry_run": True, "ok": None}


def default_stats() -> dict[str, int]:
    return {"actions_run": 0, "llm_calls": 0, "llm_failures": 0}


@dataclass
class PetState:
    version: int = STATE_VERSION
    needs: dict[str, float] = field(default_factory=default_needs)
    pose: dict[str, Any] = field(default_factory=default_pose)
    mode: str = "idle"
    last_brain_at: str | None = None
    last_action: dict[str, Any] = field(default_factory=default_last_action)
    last_user_activity_at: str | None = None
    pending_reminder: Any = None
    stats: dict[str, int] = field(default_factory=default_stats)
    eaten: int = 0
    extras: dict[str, Any] = field(default_factory=dict)

    def clamp_needs(self) -> None:
        clamped: dict[str, float] = {}
        for key in NEED_KEYS:
            clamped[key] = _clamp_need(self.needs.get(key, default_needs()[key]))
        # Сохраняем неизвестные ключи needs, тоже клампя известные
        for key, value in self.needs.items():
            if key not in clamped:
                clamped[key] = _clamp_need(value)
        self.needs = clamped

    def to_dict(self) -> dict[str, Any]:
        self.clamp_needs()
        data: dict[str, Any] = {
            "version": self.version,
            "needs": dict(self.needs),
            "pose": dict(self.pose),
            "mode": self.mode,
            "last_brain_at": self.last_brain_at,
            "last_action": dict(self.last_action),
            "last_user_activity_at": self.last_user_activity_at,
            "pending_reminder": self.pending_reminder,
            "stats": dict(self.stats),
            "eaten": self.eaten,
        }
        for key, value in self.extras.items():
            if key not in data:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PetState:
        known = {
            "version",
            "needs",
            "pose",
            "last_brain_at",
            "last_action",
            "last_user_activity_at",
            "pending_reminder",
            "stats",
            "eaten",
            "mode",
        }
        extras = {k: v for k, v in raw.items() if k not in known}

        needs_raw = raw.get("needs")
        if not isinstance(needs_raw, dict):
            needs = default_needs()
        else:
            needs = default_needs()
            needs.update(needs_raw)

        pose_raw = raw.get("pose")
        pose = default_pose() if not isinstance(pose_raw, dict) else {**default_pose(), **pose_raw}

        last_action_raw = raw.get("last_action")
        last_action = (
            default_last_action()
            if not isinstance(last_action_raw, dict)
            else {**default_last_action(), **last_action_raw}
        )

        stats_raw = raw.get("stats")
        stats = default_stats() if not isinstance(stats_raw, dict) else {**default_stats(), **stats_raw}

        mode = raw.get("mode", "idle")
        if mode not in MODES:
            mode = "idle"

        version = raw.get("version", STATE_VERSION)
        try:
            version_int = int(version)
        except (TypeError, ValueError):
            version_int = STATE_VERSION

        eaten_raw = raw.get("eaten", 0)
        try:
            eaten = int(eaten_raw)
        except (TypeError, ValueError):
            eaten = 0

        state = cls(
            version=version_int,
            needs=needs,
            pose=pose,
            mode=str(mode),
            last_brain_at=raw.get("last_brain_at"),
            last_action=last_action,
            last_user_activity_at=raw.get("last_user_activity_at"),
            pending_reminder=raw.get("pending_reminder"),
            stats={k: int(v) for k, v in stats.items()},
            eaten=eaten,
            extras=extras,
        )
        state.clamp_needs()
        return state


def default_state() -> PetState:
    return PetState()


class StateStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "state.json"

    def load(self) -> PetState:
        if not self.state_path.is_file():
            return default_state()

        try:
            text = self.state_path.read_text(encoding="utf-8")
            raw = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            broken = self.state_path.with_name(f"state.json.broken-{stamp}")
            try:
                self.state_path.rename(broken)
                logger.warning(
                    "Битый state.json переименован в %s (%s); загружено состояние по умолчанию",
                    broken.name,
                    exc,
                )
            except OSError as rename_exc:
                logger.warning(
                    "Не удалось переименовать битый state.json (%s); исходная ошибка: %s",
                    rename_exc,
                    exc,
                )
            return default_state()

        if not isinstance(raw, dict):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            broken = self.state_path.with_name(f"state.json.broken-{stamp}")
            try:
                self.state_path.rename(broken)
                logger.warning(
                    "state.json имеет неверный тип корня; переименован в %s",
                    broken.name,
                )
            except OSError as rename_exc:
                logger.warning("Не удалось переименовать некорректный state.json: %s", rename_exc)
            return default_state()

        return PetState.from_dict(raw)

    def save(self, state: PetState) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            prefix="state.",
            suffix=".tmp",
            dir=str(self.data_dir),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.state_path)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise
