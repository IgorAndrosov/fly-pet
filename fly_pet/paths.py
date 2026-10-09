"""Пути ресурсов и данных: репозиторий vs сборка PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    """Папка ресурсов: в сборке — ``sys._MEIPASS``, иначе корень репозитория."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def app_dir() -> Path:
    """Папка приложения: в сборке — каталог exe, иначе корень репозитория."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    """Каталог данных по умолчанию: ``<app_dir>/_data`` (переносимая сборка)."""
    return app_dir() / "_data"
