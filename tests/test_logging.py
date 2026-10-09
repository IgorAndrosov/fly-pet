"""Тесты логирования и redaction секретов."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from fly_pet.logging_setup import redact, setup_logging


@pytest.fixture(autouse=True)
def _reset_fly_pet_logger() -> None:
    logger = logging.getLogger("fly_pet")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    yield
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_redact_sk_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "super-secret-key-value-xyz"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    text = f"key={secret} token=sk-abcdefghijklmnop Bearer abc.def-ghi"
    scrubbed = redact(text)
    assert secret not in scrubbed
    assert "sk-abcdefghijklmnop" not in scrubbed
    assert "Bearer abc.def-ghi" not in scrubbed
    assert "***" in scrubbed


def test_setup_logging_filters_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "env-secret-12345"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    logger = setup_logging(tmp_path, level=logging.INFO)
    logger.info("leak sk-0123456789abcdef and %s", secret)

    for handler in logger.handlers:
        handler.flush()

    content = (tmp_path / "logs" / "fly-pet.log").read_text(encoding="utf-8")
    assert secret not in content
    assert "sk-0123456789abcdef" not in content
    assert "***" in content


def test_setup_logging_idempotent(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path)
    first_count = sum(1 for h in logger.handlers if isinstance(h, RotatingFileHandler))
    setup_logging(tmp_path)
    second_count = sum(1 for h in logger.handlers if isinstance(h, RotatingFileHandler))
    assert first_count == 1
    assert second_count == 1
