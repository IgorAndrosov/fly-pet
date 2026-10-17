"""Выбор монитора: область ходьбы и проверка «точка на этом экране».

Без Qt в импорте. Реальные экраны читаются только из ``read_qt_screens`` /
``pick_screen_rect`` без заранее переданного списка. Номер ``\\\\.\\DISPLAY<n>``
берётся из Win32, если Qt отдал EDID-имя.
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Sequence

from fly_pet.locomotion import Rect

logger = logging.getLogger("fly_pet")

_DISPLAY_RE = re.compile(r"DISPLAY(\d+)\s*$", re.IGNORECASE)
_FALLBACK_RECT = Rect(0, 0, 1920, 1080)
_RECT_TOLERANCE_PX = 2
_MONITORINFOF_PRIMARY = 0x1


class MonitorSpecError(ValueError):
    """Некорректное значение ``window.monitor``."""


@dataclass(frozen=True)
class ScreenDesc:
    """Один монитор Qt: полный прямоугольник и рабочая область (без панели задач)."""

    name: str
    geometry: Rect
    available: Rect
    primary: bool
    device_pixel_ratio: float = 1.0


@dataclass(frozen=True)
class MonitorDesc:
    """Монитор Win32: ``\\\\.\\DISPLAYn``, ``rcMonitor`` и ``rcWork`` в физических пикселях."""

    device: str
    monitor: Rect
    work: Rect
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
    match: str = "name"
    win32_device: str | None = None
    dpi_note: str | None = None


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
    """Принимает ``ScreenDesc`` или кортежи ``(name, (x,y,w,h), primary[, available[, dpr]])``."""
    result: list[ScreenDesc] = []
    for item in screens:
        if isinstance(item, ScreenDesc):
            result.append(item)
            continue
        if not isinstance(item, tuple) or len(item) not in {3, 4, 5}:
            raise MonitorSpecError(f"непонятный экран: {item!r}")
        name = str(item[0])
        geometry = _box_to_rect(item[1])
        primary = bool(item[2])
        available = geometry
        dpr = 1.0
        if len(item) >= 4:
            available = _box_to_rect(item[3])
        if len(item) == 5:
            dpr = float(item[4])
        result.append(
            ScreenDesc(
                name=name,
                geometry=geometry,
                available=available,
                primary=primary,
                device_pixel_ratio=dpr,
            )
        )
    return result


def normalize_monitors(monitors: Sequence[Any] | None) -> list[MonitorDesc]:
    """``MonitorDesc`` или ``(device, (x,y,w,h), primary[, work])``."""
    if not monitors:
        return []
    result: list[MonitorDesc] = []
    for item in monitors:
        if isinstance(item, MonitorDesc):
            result.append(item)
            continue
        if not isinstance(item, tuple) or len(item) not in {3, 4}:
            raise MonitorSpecError(f"непонятный монитор Win32: {item!r}")
        device = str(item[0])
        monitor = _box_to_rect(item[1])
        primary = bool(item[2])
        work = _box_to_rect(item[3]) if len(item) == 4 else monitor
        result.append(
            MonitorDesc(device=device, monitor=monitor, work=work, primary=primary)
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


def _physical_rect(screen: ScreenDesc) -> Rect:
    """Qt geometry в физических пикселях: каждая грань × ``devicePixelRatio``."""
    dpr = float(screen.device_pixel_ratio) if screen.device_pixel_ratio else 1.0
    if dpr <= 0:
        dpr = 1.0
    return Rect(
        int(round(screen.geometry.left * dpr)),
        int(round(screen.geometry.top * dpr)),
        int(round(screen.geometry.right * dpr)),
        int(round(screen.geometry.bottom * dpr)),
    )


def _rects_close(a: Rect, b: Rect, tolerance: int = _RECT_TOLERANCE_PX) -> bool:
    return (
        abs(a.left - b.left) <= tolerance
        and abs(a.top - b.top) <= tolerance
        and abs(a.right - b.right) <= tolerance
        and abs(a.bottom - b.bottom) <= tolerance
    )


def _edge_delta(a: Rect, b: Rect) -> int:
    return max(
        abs(a.left - b.left),
        abs(a.top - b.top),
        abs(a.right - b.right),
        abs(a.bottom - b.bottom),
    )


def _center(rect: Rect) -> tuple[float, float]:
    return (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2


def _match_qt_to_monitor(
    items: Sequence[ScreenDesc], mon: MonitorDesc
) -> ScreenDesc | None:
    """Сначала прямоугольник (допуск 2 px, с dpr), иначе экран, содержащий центр."""
    if not items:
        return None
    best: ScreenDesc | None = None
    best_delta = 10**9
    for screen in items:
        delta = _edge_delta(_physical_rect(screen), mon.monitor)
        if delta < best_delta:
            best = screen
            best_delta = delta
    if best is not None and best_delta <= _RECT_TOLERANCE_PX:
        return best

    cx, cy = _center(mon.monitor)
    containing: list[ScreenDesc] = []
    for screen in items:
        if point_inside(cx, cy, _physical_rect(screen)) or point_inside(
            cx, cy, screen.geometry
        ):
            containing.append(screen)
    if not containing:
        return None
    if len(containing) == 1:
        return containing[0]

    def _dist(screen: ScreenDesc) -> float:
        sx, sy = _center(_physical_rect(screen))
        return (sx - cx) ** 2 + (sy - cy) ** 2

    return min(containing, key=_dist)


def _dpi_note(screen: ScreenDesc, mon: MonitorDesc) -> str | None:
    """Сырой Qt geometry против rcMonitor: расхождение > 2 px — разный масштаб."""
    if _rects_close(screen.geometry, mon.monitor):
        return None
    geo = screen.geometry
    rc = mon.monitor
    return (
        f"Qt {screen.name} geometry=({geo.left},{geo.top})-({geo.right},{geo.bottom}) "
        f"{geo.width}x{geo.height} и Win32 {mon.device} "
        f"rcMonitor=({rc.left},{rc.top})-({rc.right},{rc.bottom}) "
        f"{rc.width}x{rc.height} расходятся больше чем на {_RECT_TOLERANCE_PX} px"
    )


def _win32_for_number(monitors: Sequence[MonitorDesc], number: int) -> MonitorDesc | None:
    for mon in monitors:
        if display_number(mon.device) == number:
            return mon
    return None


def select_screen(
    monitor: int | str,
    screens: Sequence[Any],
    win32: Sequence[Any] | None = None,
) -> ScreenChoice:
    """Область ходьбы для ``monitor``.

    Номер ищется по имени Qt (``\\\\.\\DISPLAY<n>``), иначе по прямоугольнику Win32.
    """
    items = normalize_screens(screens)
    monitors = normalize_monitors(win32)
    count = len(items)
    if count == 0:
        return ScreenChoice(
            name="(нет экрана)",
            geometry=_FALLBACK_RECT,
            walk=_FALLBACK_RECT,
            primary=False,
            fallback=True,
            count=0,
            match="fallback",
        )

    if monitor == "all":
        return ScreenChoice(
            name="all",
            geometry=union_rects([item.geometry for item in items]),
            walk=union_rects([item.available for item in items]),
            primary=False,
            fallback=False,
            count=count,
            match="all",
        )

    chosen: ScreenDesc | None = None
    fallback = False
    how = "name"
    device: str | None = None
    note: str | None = None
    if monitor == "primary":
        chosen = next((item for item in items if item.primary), None)
        if chosen is None:
            chosen = items[0]
            fallback = True
            how = "fallback"
        else:
            how = "primary"
    elif isinstance(monitor, int):
        for item in items:
            if display_number(item.name) == monitor:
                chosen = item
                how = "name"
                device = f"\\\\.\\DISPLAY{monitor}"
                break
        native = _win32_for_number(monitors, monitor)
        if chosen is None and native is not None:
            chosen = _match_qt_to_monitor(items, native)
            if chosen is not None:
                how = "win32"
                device = native.device
        if chosen is None:
            chosen = _primary_or_first(items)
            fallback = True
            how = "fallback"
            device = f"\\\\.\\DISPLAY{monitor}"
        elif native is not None and how in {"name", "win32"}:
            note = _dpi_note(chosen, native)
            if how == "win32":
                device = native.device
    else:
        raise MonitorSpecError(f"неизвестный монитор: {monitor!r}")

    return ScreenChoice(
        name=chosen.name,
        geometry=chosen.geometry,
        walk=chosen.available,
        primary=chosen.primary,
        fallback=fallback,
        count=count,
        match=how,
        win32_device=device,
        dpi_note=note,
    )


def _walk_text(choice: ScreenChoice) -> str:
    walk = choice.walk
    return (
        f"область ходьбы: ({walk.left},{walk.top})-({walk.right},{walk.bottom})"
    )


def format_screen_log(choice: ScreenChoice) -> str:
    """Строка INFO: как нашли экран и куда ходить."""
    area = _walk_text(choice)
    if choice.match == "all":
        return f"монитор all: объединение, {area}"
    if choice.match == "primary":
        return f"монитор primary: {choice.name} (основной), {area}"
    if choice.match == "win32" and choice.win32_device:
        number = display_number(choice.win32_device)
        label = number if number is not None else choice.win32_device
        return (
            f"монитор {label}: {choice.name} (по Win32 {choice.win32_device}), {area}"
        )
    if choice.match == "name":
        number = display_number(choice.name)
        if number is None and choice.win32_device:
            number = display_number(choice.win32_device)
        label = number if number is not None else choice.name
        return f"монитор {label}: {choice.name} (по имени Qt), {area}"
    return f"монитор {choice.name}: основной (fallback), {area}"


def format_monitors_log(monitors: Sequence[Any]) -> str:
    """Одна строка со всеми мониторами Win32."""
    items = normalize_monitors(monitors)
    if not items:
        return "мониторы: нет"
    parts: list[str] = []
    for mon in items:
        geo = mon.monitor
        work = mon.work
        flag = " primary" if mon.primary else ""
        parts.append(
            f"{mon.device} {geo.width}x{geo.height} at ({geo.left},{geo.top}) "
            f"work=({work.left},{work.top},{work.right},{work.bottom}){flag}"
        )
    return "мониторы: " + "; ".join(parts)


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
        self._dpi_note: str | None = None

    def resolve(
        self,
        screens: Sequence[Any],
        win32: Sequence[Any] | None = None,
    ) -> ScreenChoice:
        choice = select_screen(self.spec, screens, win32)
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
        if choice.dpi_note and choice.dpi_note != self._dpi_note:
            logger.warning("%s", choice.dpi_note)
            self._dpi_note = choice.dpi_note
        elif not choice.dpi_note:
            self._dpi_note = None
        self.choice = choice
        return choice


def read_qt_screens() -> list[ScreenDesc]:
    """Экраны ``QApplication``. При необходимости создаёт приложение."""
    from PyQt6.QtWidgets import QApplication

    from fly_pet.desktop_icons import ensure_dpi_aware

    app = QApplication.instance()
    if app is None:
        ensure_dpi_aware()
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
                device_pixel_ratio=float(screen.devicePixelRatio() or 1.0),
            )
        )
    return result


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def _win_rect(box: _RECT) -> Rect:
    return Rect(int(box.left), int(box.top), int(box.right), int(box.bottom))


def list_monitors() -> list[MonitorDesc]:
    """``EnumDisplayMonitors`` + ``GetMonitorInfoW``. Вне Windows — пустой список."""
    if sys.platform != "win32":
        return []
    try:
        return _enum_display_monitors()
    except Exception as exc:
        logger.warning("не удалось перечислить мониторы Win32: %s", exc)
        return []


def _enum_display_monitors() -> list[MonitorDesc]:
    user32 = ctypes.windll.user32
    found: list[MonitorDesc] = []

    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(_RECT),
        wintypes.LPARAM,
    )

    def _callback(hmon, _hdc, _lprc, _data) -> bool:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return True
        device = str(info.szDevice).rstrip("\x00")
        monitor = _win_rect(info.rcMonitor)
        work = _win_rect(info.rcWork)
        found.append(
            MonitorDesc(
                device=device,
                monitor=monitor,
                work=work,
                primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
            )
        )
        return True

    callback = enum_proc(_callback)
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        enum_proc,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    if not user32.EnumDisplayMonitors(None, None, callback, 0):
        raise OSError("EnumDisplayMonitors вернул 0")
    found.sort(key=lambda item: display_number(item.device) or 0)
    return found


def pick_screen_rect(
    monitor: int | str = 1,
    screens: Sequence[Any] | None = None,
    win32: Sequence[Any] | None = None,
) -> Rect:
    """Рабочая область выбранного монитора (``available``, для ``all`` — объединение)."""
    parsed = parse_monitor(monitor)
    source: Sequence[Any] = read_qt_screens() if screens is None else screens
    native: Sequence[Any] | None
    if win32 is not None:
        native = win32
    elif screens is None:
        native = list_monitors()
    else:
        native = None
    return select_screen(parsed, source, native).walk
