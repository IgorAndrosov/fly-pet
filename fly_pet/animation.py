"""Загрузка спрайтов и проигрывание кадровых анимаций."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtGui import QPixmap

ANIMATION_STATES = ("idle", "walk", "sleep", "chew", "fly", "land", "rub")
_FRAME_RE = re.compile(
    r"^(?P<state>idle|walk|sleep|chew|fly|land|rub)_(?P<num>\d+)\.png$",
    re.IGNORECASE,
)


class AnimationError(Exception):
    """Ошибка загрузки или проигрывания анимации."""


def load_frames(repo_root: Path) -> dict[str, list[QPixmap]]:
    """Читает PNG из assets/fly и группирует по состояниям в порядке номеров."""
    root = Path(repo_root).resolve()
    assets_dir = root / "assets" / "fly"
    if not assets_dir.is_dir():
        raise AnimationError(f"Каталог спрайтов не найден: {assets_dir}")

    grouped: dict[str, list[tuple[int, Path]]] = {name: [] for name in ANIMATION_STATES}
    for path in sorted(assets_dir.iterdir()):
        if not path.is_file():
            continue
        match = _FRAME_RE.match(path.name)
        if match is None:
            continue
        state = match.group("state").lower()
        num = int(match.group("num"))
        grouped[state].append((num, path))

    frames: dict[str, list[QPixmap]] = {}
    for state in ANIMATION_STATES:
        items = sorted(grouped[state], key=lambda item: item[0])
        if not items:
            raise AnimationError(f"Нет кадров анимации для состояния «{state}» в {assets_dir}")
        pixmaps: list[QPixmap] = []
        for _num, path in items:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                raise AnimationError(f"Не удалось загрузить спрайт: {path}")
            pixmaps.append(pixmap)
        frames[state] = pixmaps
    return frames


class AnimationPlayer:
    """Проигрывает кадры выбранного состояния с заданной частотой (тик снаружи)."""

    def __init__(self, frames: dict[str, list[QPixmap]], fps: int) -> None:
        if fps <= 0:
            raise AnimationError(f"Частота анимации должна быть > 0, получено: {fps}")
        missing = [name for name in ANIMATION_STATES if name not in frames or not frames[name]]
        if missing:
            raise AnimationError(
                "Нет кадров для состояний: " + ", ".join(missing)
            )
        self._frames = {name: list(frames[name]) for name in ANIMATION_STATES}
        self._fps = fps
        self._state = "idle"
        self._index = 0

    @property
    def fps(self) -> int:
        return self._fps

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, name: str) -> None:
        if name not in self._frames:
            known = ", ".join(ANIMATION_STATES)
            raise AnimationError(
                f"Неизвестное состояние анимации «{name}»; допустимы: {known}"
            )
        if name == self._state:
            return
        self._state = name
        self._index = 0

    def advance(self) -> QPixmap:
        """Переходит к следующему кадру (циклично) и возвращает его."""
        frames = self._frames[self._state]
        self._index = (self._index + 1) % len(frames)
        return frames[self._index]

    def current_frame(self) -> QPixmap:
        """Текущий кадр без сдвига индекса."""
        return self._frames[self._state][self._index]
