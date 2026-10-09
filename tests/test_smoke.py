"""Дымовые тесты скелета (без Qt)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import fly_pet

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_version_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", fly_pet.__version__)


def test_module_run_ok() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fly_pet"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "fly-pet ok" in result.stdout


def test_module_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fly_pet", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == fly_pet.__version__


def test_module_help_russian() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fly_pet", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    help_text = result.stdout.lower()
    assert help_text.strip()
    # Устойчиво к локали: русское описание всегда в description
    assert "питомец-муха" in help_text or "использование" in help_text
