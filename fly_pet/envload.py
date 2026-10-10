"""Загрузка переменных из .env без внешних зависимостей."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path | str) -> None:
    """Подставляет KEY=VALUE из файла в os.environ, не перезаписывая уже заданные."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value
