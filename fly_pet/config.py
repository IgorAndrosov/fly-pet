"""Загрузка и валидация конфигурации fly-pet."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Ошибка чтения или валидации конфигурации."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно сливает словари; списки и скаляры из override побеждают."""
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"Ожидался словарь в «{path}», получено: {type(value).__name__}")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"Ожидался список в «{path}», получено: {type(value).__name__}")
    return value


def _as_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"Ожидался bool в «{path}», получено: {type(value).__name__}")
    return value


def _as_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"Ожидалось целое число в «{path}», получено: {type(value).__name__}")
    return value


def _as_float(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"Ожидалось число в «{path}», получено: {type(value).__name__}")
    return float(value)


def _as_str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"Ожидалась строка в «{path}», получено: {type(value).__name__}")
    return value


def _as_optional_int(value: Any, path: str) -> int | None:
    if value is None:
        return None
    return _as_int(value, path)


def _as_str_list(value: Any, path: str) -> list[str]:
    items = _require_list(value, path)
    result: list[str] = []
    for i, item in enumerate(items):
        result.append(_as_str(item, f"{path}[{i}]"))
    return result


@dataclass(frozen=True)
class AppConfig:
    locale: str
    data_dir: str | None


@dataclass(frozen=True)
class FeedingPointConfig:
    x: int | None
    y: int | None


@dataclass(frozen=True)
class WindowConfig:
    width: int
    height: int
    always_on_top: bool
    click_through: bool
    never_minimize: bool
    tool_window: bool
    feeding_point: FeedingPointConfig
    title: str


@dataclass(frozen=True)
class TickConfig:
    needs_interval_sec: int
    brain_interval_sec: int
    animation_fps: int


@dataclass(frozen=True)
class NeedsConfig:
    start: dict[str, float]
    decay_per_minute: dict[str, float]
    sleep_energy_below: float
    complain_hunger_above: float


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    timeout_sec: int
    max_tokens: int
    temperature: float


@dataclass(frozen=True)
class SafetyConfig:
    dry_run: bool
    allow_real_actions: bool


@dataclass(frozen=True)
class DesktopConfig:
    use_registry_path: bool
    allowed_extensions: list[str]
    blocked_extensions: list[str]
    blocked_names: list[str]
    exclude_globs: list[str]
    min_age_days: int
    skip_hidden: bool
    skip_system: bool
    files_only: bool
    never_dirs: list[str]


@dataclass(frozen=True)
class ActionConfig:
    name: str
    enabled: bool
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PathsConfig:
    hermes_env: str


@dataclass(frozen=True)
class Config:
    app: AppConfig
    window: WindowConfig
    tick: TickConfig
    needs: NeedsConfig
    llm: LlmConfig
    safety: SafetyConfig
    desktop: DesktopConfig
    actions: list[ActionConfig]
    paths: PathsConfig
    data_dir: Path

    def data_path(self, name: str) -> Path:
        return self.data_dir / name


def _parse_needs_map(raw: Any, path: str, keys: tuple[str, ...]) -> dict[str, float]:
    data = _require_dict(raw, path)
    result: dict[str, float] = {}
    for key in keys:
        if key not in data:
            raise ConfigError(f"В «{path}» отсутствует ключ «{key}»")
        result[key] = _as_float(data[key], f"{path}.{key}")
    return result


def _parse_config(raw: dict[str, Any], data_dir: Path) -> Config:
    app_raw = _require_dict(raw.get("app"), "app")
    window_raw = _require_dict(raw.get("window"), "window")
    tick_raw = _require_dict(raw.get("tick"), "tick")
    needs_raw = _require_dict(raw.get("needs"), "needs")
    llm_raw = _require_dict(raw.get("llm"), "llm")
    safety_raw = _require_dict(raw.get("safety"), "safety")
    desktop_raw = _require_dict(raw.get("desktop"), "desktop")
    actions_raw = _require_list(raw.get("actions"), "actions")
    paths_raw = _require_dict(raw.get("paths"), "paths")

    feeding_raw = _require_dict(window_raw.get("feeding_point"), "window.feeding_point")
    thresholds = _require_dict(needs_raw.get("thresholds"), "needs.thresholds")
    need_keys = ("hunger", "energy", "mood", "attention")

    app = AppConfig(
        locale=_as_str(app_raw.get("locale"), "app.locale"),
        data_dir=app_raw.get("data_dir"),
    )
    if app.data_dir is not None and not isinstance(app.data_dir, str):
        raise ConfigError(
            f"Ожидалась строка или null в «app.data_dir», получено: {type(app.data_dir).__name__}"
        )

    window = WindowConfig(
        width=_as_int(window_raw.get("width"), "window.width"),
        height=_as_int(window_raw.get("height"), "window.height"),
        always_on_top=_as_bool(window_raw.get("always_on_top"), "window.always_on_top"),
        click_through=_as_bool(window_raw.get("click_through"), "window.click_through"),
        never_minimize=_as_bool(window_raw.get("never_minimize"), "window.never_minimize"),
        tool_window=_as_bool(window_raw.get("tool_window"), "window.tool_window"),
        feeding_point=FeedingPointConfig(
            x=_as_optional_int(feeding_raw.get("x"), "window.feeding_point.x"),
            y=_as_optional_int(feeding_raw.get("y"), "window.feeding_point.y"),
        ),
        title=_as_str(window_raw.get("title"), "window.title"),
    )

    tick = TickConfig(
        needs_interval_sec=_as_int(tick_raw.get("needs_interval_sec"), "tick.needs_interval_sec"),
        brain_interval_sec=_as_int(tick_raw.get("brain_interval_sec"), "tick.brain_interval_sec"),
        animation_fps=_as_int(tick_raw.get("animation_fps"), "tick.animation_fps"),
    )

    needs = NeedsConfig(
        start=_parse_needs_map(needs_raw.get("start"), "needs.start", need_keys),
        decay_per_minute=_parse_needs_map(
            needs_raw.get("decay_per_minute"), "needs.decay_per_minute", need_keys
        ),
        sleep_energy_below=_as_float(
            thresholds.get("sleep_energy_below"), "needs.thresholds.sleep_energy_below"
        ),
        complain_hunger_above=_as_float(
            thresholds.get("complain_hunger_above"),
            "needs.thresholds.complain_hunger_above",
        ),
    )

    llm = LlmConfig(
        base_url=_as_str(llm_raw.get("base_url"), "llm.base_url"),
        model=_as_str(llm_raw.get("model"), "llm.model"),
        timeout_sec=_as_int(llm_raw.get("timeout_sec"), "llm.timeout_sec"),
        max_tokens=_as_int(llm_raw.get("max_tokens"), "llm.max_tokens"),
        temperature=_as_float(llm_raw.get("temperature"), "llm.temperature"),
    )

    safety = SafetyConfig(
        dry_run=_as_bool(safety_raw.get("dry_run"), "safety.dry_run"),
        allow_real_actions=_as_bool(
            safety_raw.get("allow_real_actions"), "safety.allow_real_actions"
        ),
    )

    desktop = DesktopConfig(
        use_registry_path=_as_bool(
            desktop_raw.get("use_registry_path"), "desktop.use_registry_path"
        ),
        allowed_extensions=_as_str_list(
            desktop_raw.get("allowed_extensions"), "desktop.allowed_extensions"
        ),
        blocked_extensions=_as_str_list(
            desktop_raw.get("blocked_extensions"), "desktop.blocked_extensions"
        ),
        blocked_names=_as_str_list(desktop_raw.get("blocked_names"), "desktop.blocked_names"),
        exclude_globs=_as_str_list(desktop_raw.get("exclude_globs"), "desktop.exclude_globs"),
        min_age_days=_as_int(desktop_raw.get("min_age_days"), "desktop.min_age_days"),
        skip_hidden=_as_bool(desktop_raw.get("skip_hidden"), "desktop.skip_hidden"),
        skip_system=_as_bool(desktop_raw.get("skip_system"), "desktop.skip_system"),
        files_only=_as_bool(desktop_raw.get("files_only"), "desktop.files_only"),
        never_dirs=_as_str_list(desktop_raw.get("never_dirs"), "desktop.never_dirs"),
    )

    actions: list[ActionConfig] = []
    for i, item in enumerate(actions_raw):
        item_path = f"actions[{i}]"
        item_dict = _require_dict(item, item_path)
        params_raw = item_dict.get("params", {})
        if params_raw is None:
            params: dict[str, Any] = {}
        else:
            params = _require_dict(params_raw, f"{item_path}.params")
        actions.append(
            ActionConfig(
                name=_as_str(item_dict.get("name"), f"{item_path}.name"),
                enabled=_as_bool(item_dict.get("enabled"), f"{item_path}.enabled"),
                params=dict(params),
            )
        )

    paths = PathsConfig(
        hermes_env=_as_str(paths_raw.get("hermes_env"), "paths.hermes_env"),
    )

    return Config(
        app=app,
        window=window,
        tick=tick,
        needs=needs,
        llm=llm,
        safety=safety,
        desktop=desktop,
        actions=actions,
        paths=paths,
        data_dir=data_dir,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Не удалось прочитать конфиг «{path}»: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Битый YAML в «{path}»: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Корень YAML в «{path}» должен быть словарём, получено: {type(data).__name__}"
        )
    return data


def resolve_data_dir(repo_root: Path, data_dir: Path | None = None) -> Path:
    """Приоритет: аргумент → FLY_PET_DATA_DIR → <repo>/_data."""
    if data_dir is not None:
        return data_dir.expanduser().resolve()
    env = os.environ.get("FLY_PET_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (repo_root / "_data").resolve()


def load_config(
    repo_root: Path | None = None,
    data_dir: Path | None = None,
) -> Config:
    root = (repo_root or _repo_root()).resolve()
    default_path = root / "config" / "default.yaml"
    if not default_path.is_file():
        raise ConfigError(f"Не найден дефолтный конфиг: {default_path}")

    resolved_data_dir = resolve_data_dir(root, data_dir=data_dir)
    merged = _load_yaml(default_path)

    settings_path = resolved_data_dir / "settings.yaml"
    if settings_path.is_file():
        user = _load_yaml(settings_path)
        merged = _deep_merge(merged, user)

    return _parse_config(merged, resolved_data_dir)
