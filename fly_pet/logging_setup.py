"""Настройка логирования fly-pet с фильтрацией секретов."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_ENV_VARS = ("DEEPSEEK_API_KEY", "TELEGRAM_BOT_TOKEN")
_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")


def redact(text: str) -> str:
    """Вырезает похожие на секреты фрагменты из текста."""
    if not text:
        return text
    result = text
    for name in _SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value:
            result = result.replace(value, "***")
    result = _SK_RE.sub("***", result)
    result = _BEARER_RE.sub("Bearer ***", result)
    return result


class SecretFilter(logging.Filter):
    """Фильтр: подменяет секреты в сообщении и args на ***."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def setup_logging(data_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """Настраивает логгер fly_pet с ротацией в data_dir/logs/fly-pet.log."""
    logger = logging.getLogger("fly_pet")
    logger.setLevel(level)
    logger.propagate = False

    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (log_dir / "fly-pet.log").resolve()

    already = False
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(handler.baseFilename).resolve() == log_path:
                    already = True
                    break
            except Exception:
                continue

    if not already:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(funcName)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handler.addFilter(SecretFilter())
        logger.addHandler(handler)

    return logger
