"""Выбор монитора: область ходьбы и проверка «точка на этом экране».

Без Qt в импорте. Реальные экраны читаются только из ``read_qt_screens`` /
``pick_screen_rect`` без заранее переданного списка.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from typing import Any, Sequence

from fly_pet.locomotion import Rect

logger = logging.getLogger("fly_pet")

_DISPLAY_RE = re.compile(r"DISPLAY(\d+)\s*$", re.IGNORECASE)
_FALLBACK_RECT = Rect(0, 0, 1920, 1080)


class MonitorSpecError(ValueError):
    """Некорректное значение ``window.monitor``."""


@dataclass(frozen=True)
class ScreenDesc:
    """Один монитор: полный прямоугольник и рабочая область (без панели задач)."""

    name: str
    geometry: Rect
    available: Rect
    primary: bool


@dataclass(frozen=True)
class ScreenChoice:
    """Результат выбора: куда ходить и что писать в лог."""

    name: str
    geometry: Rect
    walk: Rect
    primary: bool
    fallback: bool
    count: int


def parse_monitor(value: Any) -> int | str:
    """``1``, ``2``, … или ``primary`` / ``all``. Строка из цифр — номер."""
    if isinstance(value, bool):
        raise MonitorSpecError(
            f"ожидался номер, primary или all, получено: {type(value).__name__}"
        )
    if isinstance(value, int):
        if value < 1:
            raise MonitorSpecError(f"номер монитора должен быть ≥ 1, получено: {value}")
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"primary", "all"}:
            return text
        if text.isdigit():
            number = int(text)
            if number < 1:
                raise MonitorSpecError(
                    f"номер монитора должен быть ≥ 1, получено: {value}"
                )
            return number
    raise MonitorSpecError(
        f"ожидался номер, primary или all, получено: {value!r}"
    )


def display_number(name: str) -> int | None:
    """``\\\\.\\DISPLAY2`` → 2. Иначе None."""
    match = _DISPLAY_RE.search(str(name).replace("/", "\\"))
    if match is None:
        return None
    return int(match.group(1))


def _box_to_rect(box: Sequence[int]) -> Rect:
    if len(box) != 4:
        raise MonitorSpecError(f"прямоугольник экрана — 4 числа (x, y, w, h), получено: {box!r}")
    x, y, w, h = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
    return Rect(x, y, x + w, y + h)


def normalize_screens(screens: Sequence[Any]) -> list[ScreenDesc]:
    """Принимает ``ScreenDesc`` или кортежи ``(name, (x,y,w,h), primary[, available])``."""
    result: list[ScreenDesc] = []
    for item in screens:
        if isinstance(item, ScreenDesc):
            result.append(item)
            continue
        if not isinstance(item, tuple) or len(item) not in {3, 4}:
            raise MonitorSpecError(f"непонятный экран: {item!r}")
        name = str(item[0])
        geometry = _box_to_rect(item[1])
        primary = bool(item[2])
        available = _box_to_rect(item[3]) if len(item) == 4 else geometry
        result.append(
            ScreenDesc(
                name=name,
                geometry=geometry,
                available=available,
                primary=primary,
            )
        )
    return result


def union_rects(rects: Sequence[Rect]) -> Rect:
    left = min(r.left for r in rects)
    top = min(r.top for r in rects)
    right = max(r.right for r in rects)
    bottom = max(r.bottom for r in rects)
    return Rect(left, top, right, bottom)


def _primary_or_first(items: Sequence[ScreenDesc]) -> ScreenDesc:
    for item in items:
        if item.primary:
            return item
    return items[0]


def select_screen(monitor: int | str, screens: Sequence[Any]) -> ScreenChoice:
    """Область ходьбы для ``monitor`` по уже известному списку экранов."""
    items = normalize_screens(screens)
    count = len(items)
    if count == 0:
        return ScreenChoice(
            name="(нет экрана)",
            geometry=_FALLBACK_RECT,
            walk=_FALLBACK_RECT,
            primary=False,
            fallback=True,
            count=0,
        )

    if monitor == "all":
        return ScreenChoice(
            name="all",
            geometry=union_rects([item.geometry for item in items]),
            walk=union_rects([item.available for item in items]),
            primary=False,
            fallback=False,
            count=count,
        )

    chosen: ScreenDesc | None = None
    fallback = False
    if monitor == "primary":
        chosen = next((item for item in items if item.primary), None)
        if chosen is None:
            chosen = items[0]
            fallback = True
    elif isinstance(monitor, int):
        for item in items:
            if display_number(item.name) == monitor:
                chosen = item
                break
        if chosen is None:
            chosen = _primary_or_first(items)
            fallback = True
    else:
        raise MonitorSpecError(f"неизвестный монитор: {monitor!r}")

    return ScreenChoice(
        name=chosen.name,
        geometry=chosen.geometry,
        walk=chosen.available,
        primary=chosen.primary,
        fallback=fallback,
        count=count,
    )


def format_screen_log(choice: ScreenChoice) -> str:
    """Строка INFO при старте."""
    geo = choice.geometry
    walk = choice.walk
    return (
        f"мониторов: {choice.count}; экран: {choice.name} "
        f"{geo.width}x{geo.height} at ({geo.left},{geo.top}) primary={choice.primary}; "
        f"область ходьбы: ({walk.left},{walk.top})-({walk.right},{walk.bottom})"
    )


def point_inside(x: float, y: float, rect: Rect) -> bool:
    """Полуинтервал: правая и нижняя границы принадлежат соседнему экрану."""
    return rect.left <= x < rect.right and rect.top <= y < rect.bottom


def icon_on_screen(icon: dict, rect: Rect) -> bool:
    """Центр клетки иконки лежит на ``rect``."""
    from fly_pet.desktop_icons import icon_center_screen

    cx, cy = icon_center_screen(icon)
    return point_inside(cx, cy, rect)


class ScreenSession:
    """Повторный выбор экрана. WARNING о fallback — один раз, пока он длится."""

    def __init__(self, monitor: int | str) -> None:
        self.spec = monitor
        self.choice: ScreenChoice | None = None
        self._warned = False

    def resolve(self, screens: Sequence[Any]) -> ScreenChoice:
        choice = select_screen(self.spec, screens)
        if choice.fallback:
            if not self._warned:
                if isinstance(self.spec, int):
                    wanted = f"\\\\.\\DISPLAY{self.spec}"
                else:
                    wanted = str(self.spec)
                logger.warning(
                    "монитор %s не найден — беру основной %s",
                    wanted,
                    choice.name,
                )
                self._warned = True
        else:
            self._warned = False
        self.choice = choice
        return choice


def read_qt_screens() -> list[ScreenDesc]:
    """Экраны ``QApplication``. При необходимости создаёт приложение."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    primary = app.primaryScreen()
    result: list[ScreenDesc] = []
    for screen in app.screens():
        geo = screen.geometry()
        avail = screen.availableGeometry()
        result.append(
            ScreenDesc(
                name=str(screen.name()),
                geometry=Rect(
                    int(geo.x()),
                    int(geo.y()),
                    int(geo.x() + geo.width()),
                    int(geo.y() + geo.height()),
                ),
                available=Rect(
                    int(avail.x()),
                    int(avail.y()),
                    int(avail.x() + avail.width()),
                    int(avail.y() + avail.height()),
                ),
                primary=screen is primary,
            )
        )
    return result


def pick_screen_rect(
    monitor: int | str = 1,
    screens: Sequence[Any] | None = None,
) -> Rect:
    """Рабочая область выбранного монитора (``available``, для ``all`` — объединение)."""
    parsed = parse_monitor(monitor)
    source: Sequence[Any] = read_qt_screens() if screens is None else screens
    return select_screen(parsed, source).walk
