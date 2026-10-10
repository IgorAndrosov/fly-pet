"""Локомоция мухи: ходьба по столу, полёт и посадка на чужие окна."""

from __future__ import annotations

import ctypes
import logging
import math
import random
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol, Sequence

from ctypes import wintypes

logger = logging.getLogger("fly_pet")


def get_cursor_pos() -> tuple[float, float] | None:
    """Позиция курсора (экранные координаты); вне Windows — None."""
    if sys.platform != "win32":
        return None
    pt = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return float(pt.x), float(pt.y)

GWL_STYLE = -16
GWL_EXSTYLE = -20
GW_OWNER = 4
GW_HWNDNEXT = 2
GW_HWNDPREV = 3
GA_ROOT = 2
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
SM_CYCAPTION = 4
SM_CYFRAME = 32
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

_BLOCKED_CLASSES = frozenset(
    {
        "IME",
        "MSCTFIME UI",
        "Chrome_RenderWidgetHostHWND",
        "_q_titlebar",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Progman",
        "WorkerW",
        "SHELLDLL_DefView",
        "SysListView32",
        "DummyDwmListenerWindow",
        "AsHotkeyExec",
        "ApplicationFrameWindow",
    }
)
_BLOCKED_CLASS_SUBSTRINGS = frozenset({"AsHotkeyExec"})
_ATTACH_VERIFY_TICKS = 2

_TAKEOFF_SEC = 0.35
_TAKEOFF_RISE_PX = 56.0
_LANDING_SEC = 0.4
_STRIP_MIN_H = 24
_STRIP_MAX_H = 44


@dataclass(frozen=True)
class Rect:
    """Прямоугольник в экранных координатах (left/top/right/bottom)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def shifted(self, dx: int, dy: int) -> Rect:
        return Rect(
            self.left + dx,
            self.top + dy,
            self.right + dx,
            self.bottom + dy,
        )


@dataclass(frozen=True)
class WindowInfo:
    """Описание чужого окна-кандидата для посадки."""

    hwnd: int
    title: str
    class_name: str
    rect: Rect
    is_foreground: bool
    has_caption: bool = True
    client_top_screen: int | None = None
    visible: bool = True
    iconic: bool = False
    has_owner: bool = False
    is_tool_window: bool = False
    has_sysmenu: bool = False
    intersects_screen: bool = True


class LocomotionState(Enum):
    """Фазы локомоции."""

    ON_DESKTOP = "on_desktop"
    TAKEOFF = "takeoff"
    IN_FLIGHT = "in_flight"
    LANDING = "landing"
    ON_WINDOW = "on_window"


class LocomotionMode(Enum):
    """Режим поверхности: стол (2D) или полоса окна (1D). Меняется только на посадке."""

    DESKTOP = "desktop"
    WINDOW = "window"


def _norm_angle_deg(deg: float) -> float:
    """Нормализовать угол в (−180, 180]."""
    return (float(deg) + 180.0) % 360.0 - 180.0


def _heading_to_facing(heading_deg: float) -> int:
    return 1 if abs(_norm_angle_deg(heading_deg)) <= 90.0 else -1


@dataclass(frozen=True)
class LocomotionPose:
    """Поза после шага: позиция окна спрайта и анимация."""

    x: float
    y: float
    facing: int  # 1 вправо, -1 влево
    anim: str
    state: LocomotionState
    attached_hwnd: int | None


class WindowApi(Protocol):
    """Тонкий слой Win32: в тестах подменяется."""

    def list_windows(self) -> list[WindowInfo]:
        ...

    def refresh_window(self, hwnd: int) -> WindowInfo | None:
        ...

    def title_bar_strip(self, info: WindowInfo) -> Rect | None:
        ...

    def attach_above(self, our_hwnd: int, target_hwnd: int) -> None:
        ...

    def detach_to_desktop(self, our_hwnd: int) -> None:
        ...

    def find_desktop_hwnd(self) -> int | None:
        ...

    def window_from_point(self, x: int, y: int) -> int:
        ...

    def is_window(self, hwnd: int) -> bool:
        ...

    def is_our_window(self, hwnd: int, our_hwnd: int) -> bool:
        ...

    def is_immediately_above(self, our_hwnd: int, target_hwnd: int) -> bool:
        ...


def title_bar_strip_from_metrics(
    window_rect: Rect,
    *,
    has_caption: bool,
    caption_plus_frame: int,
    client_top_screen: int | None,
) -> Rect | None:
    """Полоса заголовка по метрикам (без Win32). Вырожденная → None."""
    if window_rect.width <= 0 or window_rect.height <= 0:
        return None
    if has_caption:
        h = max(1, int(caption_plus_frame))
        bottom = window_rect.top + h
    else:
        if client_top_screen is None:
            return None
        raw_h = int(client_top_screen) - window_rect.top
        if raw_h <= 0:
            # нет видимой неклиентской зоны сверху — берём середину диапазона
            h = (_STRIP_MIN_H + _STRIP_MAX_H) // 2
        else:
            h = max(_STRIP_MIN_H, min(_STRIP_MAX_H, raw_h))
        bottom = window_rect.top + h
    if bottom <= window_rect.top or bottom > window_rect.bottom:
        return None
    strip = Rect(window_rect.left, window_rect.top, window_rect.right, bottom)
    if strip.width <= 0 or strip.height <= 0:
        return None
    return strip


def _class_is_blocked(class_name: str) -> bool:
    if class_name in _BLOCKED_CLASSES:
        return True
    return any(token in class_name for token in _BLOCKED_CLASS_SUBSTRINGS)


def _rects_intersect(a: Rect, b: Rect) -> bool:
    return not (
        a.right <= b.left
        or a.left >= b.right
        or a.bottom <= b.top
        or a.top >= b.bottom
    )


def filter_window_candidates(
    windows: Sequence[WindowInfo],
    *,
    min_width: int,
    min_height: int,
    ignore_titles: Sequence[str],
    our_hwnd: int | None = None,
    exclude_hwnds: Sequence[int] | None = None,
    screen: Rect | None = None,
) -> list[WindowInfo]:
    """Жёсткий отбор окон-кандидатов для посадки."""
    ignored = {t.casefold() for t in ignore_titles}
    excluded = set(exclude_hwnds or ())
    result: list[WindowInfo] = []
    for info in windows:
        if our_hwnd is not None and info.hwnd == our_hwnd:
            continue
        if info.hwnd in excluded:
            continue
        if not info.visible or info.iconic:
            continue
        if info.rect.width <= 0 or info.rect.height <= 0:
            continue
        if info.rect.width < min_width or info.rect.height < min_height:
            continue
        if not info.intersects_screen:
            continue
        if screen is not None and not _rects_intersect(info.rect, screen):
            continue
        if info.has_owner:
            continue
        if info.is_tool_window:
            continue
        if not (info.has_caption or info.has_sysmenu):
            continue
        if _class_is_blocked(info.class_name):
            continue
        if not info.title or not info.title.strip():
            continue
        if info.title.casefold() in ignored:
            continue
        result.append(info)
    return result


class Win32WindowApi:
    """Реальная работа с окнами через ctypes (только win32)."""

    def __init__(self, our_pid: int | None = None) -> None:
        self._our_pid = our_pid if our_pid is not None else (os_getpid())

    def list_windows(self) -> list[WindowInfo]:
        if sys.platform != "win32":
            return []
        user32 = ctypes.windll.user32
        fg = int(user32.GetForegroundWindow() or 0)
        found: list[WindowInfo] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd: int, _lparam: int) -> bool:
            info = self._read_info(int(hwnd), fg)
            if info is not None:
                found.append(info)
            return True

        user32.EnumWindows(_enum, 0)
        return found

    def refresh_window(self, hwnd: int) -> WindowInfo | None:
        if sys.platform != "win32" or not hwnd:
            return None
        fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        return self._read_info(int(hwnd), fg)

    def title_bar_strip(self, info: WindowInfo) -> Rect | None:
        caption = 0
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            caption = int(user32.GetSystemMetrics(SM_CYCAPTION)) + int(
                user32.GetSystemMetrics(SM_CYFRAME)
            )
        return title_bar_strip_from_metrics(
            info.rect,
            has_caption=info.has_caption,
            caption_plus_frame=caption or 31,
            client_top_screen=info.client_top_screen,
        )

    def attach_above(self, our_hwnd: int, target_hwnd: int) -> None:
        """Вставить наше окно в z-порядке непосредственно над целью.

        ``SetWindowPos(our, target)`` ставит нас *под* target (hWndInsertAfter).
        Нужно встать после текущего соседа сверху у цели.
        """
        if sys.platform != "win32" or not our_hwnd or not target_hwnd:
            return
        user32 = ctypes.windll.user32
        if not user32.IsWindow(target_hwnd):
            return
        prev = int(user32.GetWindow(target_hwnd, GW_HWNDPREV) or 0)
        if prev == our_hwnd:
            return
        insert_after = prev if prev else HWND_TOP
        user32.SetWindowPos(
            our_hwnd,
            insert_after,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def detach_to_desktop(self, our_hwnd: int) -> None:
        if sys.platform != "win32" or not our_hwnd:
            return
        user32 = ctypes.windll.user32
        desktop = self.find_desktop_hwnd()
        insert_after = desktop if desktop else HWND_TOP
        user32.SetWindowPos(
            our_hwnd,
            insert_after,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def window_from_point(self, x: int, y: int) -> int:
        if sys.platform != "win32":
            return 0
        return int(
            ctypes.windll.user32.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0
        )

    def is_window(self, hwnd: int) -> bool:
        if sys.platform != "win32" or not hwnd:
            return False
        return bool(ctypes.windll.user32.IsWindow(hwnd))

    def is_our_window(self, hwnd: int, our_hwnd: int) -> bool:
        if not hwnd or not our_hwnd:
            return False
        if hwnd == our_hwnd:
            return True
        if sys.platform != "win32":
            return False
        root = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or 0)
        return root == our_hwnd

    def is_immediately_above(self, our_hwnd: int, target_hwnd: int) -> bool:
        if sys.platform != "win32" or not our_hwnd or not target_hwnd:
            return False
        below = int(ctypes.windll.user32.GetWindow(our_hwnd, GW_HWNDNEXT) or 0)
        return below == target_hwnd

    def find_desktop_hwnd(self) -> int | None:
        if sys.platform != "win32":
            return None
        user32 = ctypes.windll.user32
        progman = int(user32.FindWindowW("Progman", None) or 0)
        if progman:
            shell = self._find_child(progman, "SHELLDLL_DefView")
            if shell:
                listview = self._find_child(shell, "SysListView32")
                if listview:
                    return listview
                return shell
            return progman
        # Fallback: WorkerW с SHELLDLL_DefView
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd: int, _lparam: int) -> bool:
            cls = self._class_name(int(hwnd))
            if cls == "WorkerW":
                shell = self._find_child(int(hwnd), "SHELLDLL_DefView")
                if shell:
                    found.append(shell)
                    return False
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            listview = self._find_child(found[0], "SysListView32")
            return listview or found[0]
        return None

    def _read_info(self, hwnd: int, fg: int) -> WindowInfo | None:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return None
        if self._is_our_process(hwnd):
            return None
        visible = bool(user32.IsWindowVisible(hwnd))
        iconic = bool(user32.IsIconic(hwnd))
        owner = int(user32.GetWindow(hwnd, GW_OWNER) or 0)
        ex = self._get_long(hwnd, GWL_EXSTYLE)
        style = self._get_long(hwnd, GWL_STYLE)
        title = self._window_text(hwnd)
        class_name = self._class_name(hwnd)
        rect = self._window_rect(hwnd)
        if rect is None:
            return None
        screen = self._virtual_screen_rect()
        intersects = screen is None or _rects_intersect(rect, screen)
        return WindowInfo(
            hwnd=hwnd,
            title=title,
            class_name=class_name,
            rect=rect,
            is_foreground=(hwnd == fg),
            has_caption=bool(style & WS_CAPTION),
            client_top_screen=self._client_top_screen(hwnd),
            visible=visible,
            iconic=iconic,
            has_owner=owner != 0,
            is_tool_window=bool(ex & WS_EX_TOOLWINDOW),
            has_sysmenu=bool(style & WS_SYSMENU),
            intersects_screen=intersects,
        )

    @staticmethod
    def _virtual_screen_rect() -> Rect | None:
        if sys.platform != "win32":
            return None
        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            return None
        return Rect(left, top, left + width, top + height)

    def _is_our_process(self, hwnd: int) -> bool:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == int(self._our_pid)

    def _get_long(self, hwnd: int, index: int) -> int:
        user32 = ctypes.windll.user32
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            user32.GetWindowLongPtrW.restype = ctypes.c_longlong
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            return int(user32.GetWindowLongPtrW(hwnd, index) or 0)
        return int(user32.GetWindowLongW(hwnd, index) or 0)

    @staticmethod
    def _class_name(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    @staticmethod
    def _window_text(hwnd: int) -> str:
        user32 = ctypes.windll.user32
        length = int(user32.GetWindowTextLengthW(hwnd) or 0)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    @staticmethod
    def _window_rect(hwnd: int) -> Rect | None:
        rc = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc)):
            return None
        if rc.right <= rc.left or rc.bottom <= rc.top:
            return None
        return Rect(int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))

    @staticmethod
    def _client_top_screen(hwnd: int) -> int | None:
        user32 = ctypes.windll.user32
        rc = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        return int(pt.y)

    @staticmethod
    def _find_child(parent: int, class_name: str) -> int | None:
        hwnd = int(
            ctypes.windll.user32.FindWindowExW(parent, None, class_name, None) or 0
        )
        return hwnd or None


def os_getpid() -> int:
    import os

    return int(os.getpid())


class LocomotionDriver:
    """Задел этапа 2: connectome-драйвер скорости (пока пустой)."""

    def set_velocity(self, vx: float, turn: float) -> None:
        del vx, turn
        return None


class Locomotion(LocomotionDriver):
    """Автомат ходьбы/полёта; Win32 только через WindowApi."""

    def __init__(
        self,
        walk_cfg: object,
        *,
        pet_width: int,
        pet_height: int,
        desktop: Rect,
        api: WindowApi,
        our_hwnd_getter: Callable[[], int] | None = None,
        rng: random.Random | None = None,
        cursor_getter: Callable[[], tuple[float, float] | None] | None = None,
    ) -> None:
        self._cfg = walk_cfg
        self._pet_w = int(pet_width)
        self._pet_h = int(pet_height)
        self._desktop = desktop
        self._api = api
        self._our_hwnd_getter = our_hwnd_getter or (lambda: 0)
        self._cursor_getter = cursor_getter or get_cursor_pos
        seed = getattr(walk_cfg, "seed", None)
        self._rng = rng if rng is not None else random.Random(seed)
        self._mode = LocomotionMode.DESKTOP
        self._state = LocomotionState.ON_DESKTOP
        self._x = float(desktop.left + max(0, (desktop.width - pet_width) // 2))
        self._y = self._desktop_y()
        self._heading_deg = 0.0
        self._facing = 1
        self._vx = 0.0
        self._paused = False
        self._hold_still = False
        self._scare_cursor = bool(getattr(walk_cfg, "scare_cursor", True))
        self._scare_cooldown_until = 0.0
        self._burst_speed_override: float | None = None
        self._stay_left = self._roll_stay(
            getattr(walk_cfg, "desktop_stay_sec", (8.0, 25.0))
        )
        self._phase_t = 0.0
        self._takeoff_from_y = self._y
        self._flight_x0 = self._x
        self._flight_y0 = self._y
        self._flight_x1 = self._x
        self._flight_y1 = self._y
        self._flight_dur = 0.5
        self._flight_target_hwnd: int | None = None
        self._flight_to_desktop = False
        self._attached_hwnd: int | None = None
        self._prev_window_hwnd: int | None = None
        self._window_offset_x = 0.0
        self._anim = "walk"
        self._attach_attempts = 0
        self._failed_attach_hwnds: set[int] = set()
        self._attach_verify_left: int | None = None
        # Прерывистая ходьба: рывок → пауза (idle/rub)
        self._walk_phase = "burst"  # "burst" | "pause"
        self._burst_left = 0.0
        self._pause_left = 0.0
        self._last_burst_px = 0.0
        self._last_burst_was_long = False
        self._last_pause_groomed = False
        self._last_pause_turned = False
        self._last_pause_turn_delta = 0.0
        self._begin_burst()

    @property
    def state(self) -> LocomotionState:
        return self._state

    @property
    def mode(self) -> LocomotionMode:
        return self._mode

    def sprite_angle_deg(self) -> float:
        """Угол спрайта: 0 = вправо; в полёте — к цели."""
        if self._state in {
            LocomotionState.TAKEOFF,
            LocomotionState.IN_FLIGHT,
            LocomotionState.LANDING,
        }:
            dx = self._flight_x1 - self._x
            dy = self._flight_y1 - self._y
            if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                return _norm_angle_deg(math.degrees(math.atan2(dy, dx)))
        return _norm_angle_deg(self._heading_deg)

    @property
    def pose(self) -> LocomotionPose:
        return LocomotionPose(
            x=self._x,
            y=self._y,
            facing=self._facing,
            anim=self._anim,
            state=self._state,
            attached_hwnd=self._attached_hwnd,
        )

    def blocks_desktop_reassert(self) -> bool:
        """True, если нельзя поднимать окно через HWND_TOP."""
        return self._state != LocomotionState.ON_DESKTOP

    def pause(self) -> None:
        self._paused = True

    def set_hold_still(self, value: bool) -> None:
        """Стоять на месте: не ходит и не летает."""
        self._hold_still = bool(value)
        if self._hold_still:
            self._anim = "idle"
            self._vx = 0.0
            self._burst_speed_override = None

    def is_hold_still(self) -> bool:
        return self._hold_still

    def set_scare_cursor(self, value: bool) -> None:
        """Вкл/выкл отпугивание курсором (на лету)."""
        self._scare_cursor = bool(value)

    def scare_cursor_enabled(self) -> bool:
        return self._scare_cursor

    def resume_from_desktop(self, x: float, y: float) -> None:
        """После перетаскивания человеком — снова с рабочего стола."""
        self._paused = False
        self._mode = LocomotionMode.DESKTOP
        self._x, self._y = self._clamp_desktop_xy(float(x), float(y))
        self._attached_hwnd = None
        self._prev_window_hwnd = None
        self._flight_target_hwnd = None
        self._flight_to_desktop = False
        self._attach_verify_left = None
        self._attach_attempts = 0
        self._failed_attach_hwnds.clear()
        self._state = LocomotionState.ON_DESKTOP
        self._stay_left = self._roll_stay(
            getattr(self._cfg, "desktop_stay_sec", (8.0, 25.0))
        )
        if not self._hold_still:
            self._begin_burst()
        else:
            self._anim = "idle"
            self._vx = 0.0
        our = self._our_hwnd_getter()
        if our:
            self._api.detach_to_desktop(our)

    def set_desktop(self, desktop: Rect) -> None:
        self._desktop = desktop

    def step(self, dt_sec: float) -> LocomotionPose:
        if self._paused or self._hold_still or dt_sec <= 0:
            if self._hold_still:
                self._anim = "idle"
            return self.pose
        self._maybe_scare()
        dt = float(dt_sec)
        if self._state == LocomotionState.ON_DESKTOP:
            self._step_desktop(dt)
        elif self._state == LocomotionState.TAKEOFF:
            self._step_takeoff(dt)
        elif self._state == LocomotionState.IN_FLIGHT:
            self._step_flight(dt)
        elif self._state == LocomotionState.LANDING:
            self._step_landing(dt)
        elif self._state == LocomotionState.ON_WINDOW:
            self._step_on_window(dt)
        return self.pose

    def _desktop_y(self) -> float:
        _, _, _, y_hi = self._desktop_walk_bounds()
        return float(y_hi)

    def _desktop_walk_bounds(self) -> tuple[float, float, float, float]:
        """Границы top-left спрайта при ходьбе по столу (с desktop_margin_px)."""
        margin = float(getattr(self._cfg, "desktop_margin_px", 12))
        x_lo = float(self._desktop.left) + margin
        x_hi = float(self._desktop.right) - margin - float(self._pet_w)
        y_lo = float(self._desktop.top) + margin
        y_hi = float(self._desktop.bottom) - margin - float(self._pet_h)
        if x_hi < x_lo:
            x_lo = float(self._desktop.left)
            x_hi = float(self._desktop.right - self._pet_w)
        if y_hi < y_lo:
            y_lo = float(self._desktop.top)
            y_hi = float(self._desktop.bottom - self._pet_h)
        return x_lo, x_hi, y_lo, y_hi

    def _clamp_desktop_xy(self, x: float, y: float) -> tuple[float, float]:
        x_lo, x_hi, y_lo, y_hi = self._desktop_walk_bounds()
        return max(x_lo, min(x_hi, x)), max(y_lo, min(y_hi, y))

    def _roll_stay(self, pair: Sequence[float]) -> float:
        lo, hi = float(pair[0]), float(pair[1])
        if hi < lo:
            lo, hi = hi, lo
        return float(self._rng.uniform(lo, hi))

    def _roll_pair(self, pair: Sequence[float]) -> float:
        return self._roll_stay(pair)

    def _clamp_desktop_x(self, x: float) -> float:
        x_lo, x_hi, _, _ = self._desktop_walk_bounds()
        if x_hi < x_lo:
            return x_lo
        return max(x_lo, min(x_hi, x))

    def _roll_burst_distance(self) -> tuple[float, bool]:
        """Длина следующего рывка и флаг «длинный»."""
        long_chance = float(getattr(self._cfg, "long_burst_chance", 0.12))
        is_long = self._rng.random() < long_chance
        if is_long:
            pair = getattr(self._cfg, "long_burst_px", (180.0, 380.0))
        else:
            pair = getattr(self._cfg, "burst_px", (28.0, 120.0))
        return self._roll_pair(pair), is_long

    def _sync_facing_from_heading(self) -> None:
        self._facing = _heading_to_facing(self._heading_deg)

    def _burst_speed(self) -> float:
        if self._burst_speed_override is not None:
            return abs(float(self._burst_speed_override))
        return abs(float(getattr(self._cfg, "burst_speed_px_s", 165)))

    def _maybe_scare(self) -> None:
        """Рывок от курсора или панический взлёт (если включено и кулдаун прошёл)."""
        if not self._scare_cursor:
            return
        if self._state not in {
            LocomotionState.ON_DESKTOP,
            LocomotionState.ON_WINDOW,
        }:
            return
        now = time.monotonic()
        if now < self._scare_cooldown_until:
            return
        cursor = self._cursor_getter()
        if cursor is None:
            return
        cx, cy = cursor
        fx = self._x + self._pet_w / 2.0
        fy = self._y + self._pet_h / 2.0
        away_dx = fx - cx
        away_dy = fy - cy
        dist = math.hypot(away_dx, away_dy)
        scare_r = float(getattr(self._cfg, "scare_radius_px", 90))
        panic_r = float(getattr(self._cfg, "panic_radius_px", 40))
        if dist >= scare_r:
            return
        cooldown = float(getattr(self._cfg, "scare_cooldown_sec", 1.2))
        self._scare_cooldown_until = now + cooldown
        if dist < panic_r:
            logger.debug(
                "испуг: dist=%.1f паника (away=(%.1f, %.1f))",
                dist,
                away_dx,
                away_dy,
            )
            self._begin_takeoff()
            return
        logger.debug(
            "испуг: dist=%.1f рывок (away=(%.1f, %.1f))",
            dist,
            away_dx,
            away_dy,
        )
        self._begin_scare_burst(away_dx, away_dy)

    def _begin_scare_burst(self, away_dx: float, away_dy: float) -> None:
        """Немедленный рывок в сторону от курсора."""
        burst = self._roll_pair(
            getattr(self._cfg, "scare_burst_px", (110.0, 240.0))
        )
        speed = abs(float(getattr(self._cfg, "scare_speed_px_s", 340)))
        self._burst_speed_override = speed
        self._last_burst_px = burst
        self._last_burst_was_long = False
        self._burst_left = burst
        self._pause_left = 0.0
        self._walk_phase = "burst"
        self._anim = "walk"
        if self._mode == LocomotionMode.WINDOW:
            if abs(away_dx) < 1e-6:
                away_dx = 1.0 if self._facing >= 0 else -1.0
            self._heading_deg = 0.0 if away_dx >= 0 else 180.0
            self._sync_facing_from_heading()
            self._vx = speed * (1 if self._facing >= 0 else -1)
        else:
            length = math.hypot(away_dx, away_dy)
            if length < 1e-6:
                away_dx, away_dy = float(self._facing), 0.0
                length = 1.0
            self._heading_deg = _norm_angle_deg(
                math.degrees(math.atan2(away_dy / length, away_dx / length))
            )
            self._sync_facing_from_heading()
            self._vx = speed

    def _begin_burst(self) -> None:
        dist, is_long = self._roll_burst_distance()
        self._burst_speed_override = None
        speed = self._burst_speed()
        self._last_burst_px = dist
        self._last_burst_was_long = is_long
        self._burst_left = dist
        self._pause_left = 0.0
        self._walk_phase = "burst"
        self._anim = "walk"
        if self._mode == LocomotionMode.WINDOW:
            if abs(_norm_angle_deg(self._heading_deg)) > 90.0:
                self._heading_deg = 180.0
            else:
                self._heading_deg = 0.0
            self._sync_facing_from_heading()
            self._vx = speed * (1 if self._facing >= 0 else -1)
        else:
            self._sync_facing_from_heading()
            self._vx = speed

    def _begin_pause(self) -> None:
        self._burst_speed_override = None
        turn_chance = float(getattr(self._cfg, "turn_on_pause_chance", 0.35))
        turned = self._rng.random() < turn_chance
        self._last_pause_turn_delta = 0.0
        if turned:
            if self._mode == LocomotionMode.WINDOW:
                self._heading_deg = 180.0 if self._heading_deg == 0.0 else 0.0
                self._last_pause_turn_delta = 180.0
            else:
                pair = getattr(self._cfg, "desktop_turn_deg", (20.0, 90.0))
                delta = self._roll_pair(pair)
                if self._rng.random() < 0.5:
                    delta = -delta
                self._heading_deg = _norm_angle_deg(self._heading_deg + delta)
                self._last_pause_turn_delta = delta
            self._sync_facing_from_heading()
        self._last_pause_turned = turned

        groom_chance = float(getattr(self._cfg, "groom_chance", 0.45))
        groomed = self._rng.random() < groom_chance
        self._last_pause_groomed = groomed
        if groomed:
            self._anim = "rub"
            self._pause_left = self._roll_pair(
                getattr(self._cfg, "groom_sec", (1.0, 2.6))
            )
        else:
            self._anim = "idle"
            self._pause_left = self._roll_pair(
                getattr(self._cfg, "pause_sec", (0.25, 1.4))
            )
        self._walk_phase = "pause"
        self._burst_left = 0.0
        self._vx = 0.0

    def _bounce_step(
        self, x: float, dx: float, lo: float, hi: float
    ) -> tuple[float, int]:
        """Сдвиг на dx с отражением от краёв; возвращает (x, facing)."""
        if hi < lo:
            return lo, self._facing
        if abs(dx) <= 0:
            return max(lo, min(hi, x)), self._facing
        new_x = x + dx
        facing = 1 if dx >= 0 else -1
        for _ in range(8):
            if new_x < lo:
                new_x = lo + (lo - new_x)
                facing = 1
            elif new_x > hi:
                new_x = hi - (new_x - hi)
                facing = -1
            else:
                break
        return max(lo, min(hi, new_x)), facing

    def _bounce_step_2d(
        self, x: float, y: float, dx: float, dy: float
    ) -> tuple[float, float]:
        """Сдвиг в 2D с зеркальным отражением угла от краёв."""
        x_lo, x_hi, y_lo, y_hi = self._desktop_walk_bounds()
        if x_hi < x_lo or y_hi < y_lo:
            return x_lo, y_lo
        nx, ny = x + dx, y + dy
        heading = self._heading_deg
        for _ in range(8):
            bounced = False
            if nx < x_lo:
                nx = x_lo + (x_lo - nx)
                heading = _norm_angle_deg(180.0 - heading)
                bounced = True
            elif nx > x_hi:
                nx = x_hi - (nx - x_hi)
                heading = _norm_angle_deg(180.0 - heading)
                bounced = True
            if ny < y_lo:
                ny = y_lo + (y_lo - ny)
                heading = _norm_angle_deg(-heading)
                bounced = True
            elif ny > y_hi:
                ny = y_hi - (ny - y_hi)
                heading = _norm_angle_deg(-heading)
                bounced = True
            if not bounced:
                break
        self._heading_deg = heading
        self._sync_facing_from_heading()
        return max(x_lo, min(x_hi, nx)), max(y_lo, min(y_hi, ny))

    def _advance_burst_x(self, cur_x: float, dt: float, lo: float, hi: float) -> float:
        """Сдвинуть X на один тик рывка (режим окна); вернуть новую координату."""
        self._anim = "walk"
        speed = self._burst_speed()
        if abs(self._vx) < 1e-9:
            self._vx = speed * (1 if self._facing >= 0 else -1)
        step = min(abs(self._vx) * dt, max(0.0, self._burst_left))
        if step <= 0:
            self._begin_pause()
            return cur_x
        signed = step if self._vx >= 0 else -step
        new_x, facing = self._bounce_step(cur_x, signed, lo, hi)
        self._facing = facing
        self._heading_deg = 0.0 if facing >= 0 else 180.0
        self._vx = speed * (1 if facing >= 0 else -1)
        if hi > lo:
            self._burst_left = max(0.0, self._burst_left - step)
        else:
            self._burst_left = 0.0
        if self._burst_left <= 1e-6:
            self._begin_pause()
        return new_x

    def _advance_burst_desktop(self, dt: float) -> None:
        """Рывок по столу в направлении heading с отражением от краёв."""
        self._anim = "walk"
        speed = self._burst_speed()
        step = min(speed * dt, max(0.0, self._burst_left))
        if step <= 0:
            self._begin_pause()
            return
        rad = math.radians(self._heading_deg)
        dx = step * math.cos(rad)
        dy = step * math.sin(rad)
        self._x, self._y = self._bounce_step_2d(self._x, self._y, dx, dy)
        self._burst_left = max(0.0, self._burst_left - step)
        if self._burst_left <= 1e-6:
            self._begin_pause()

    def _tick_walk_or_pause(self, dt: float, cur_x: float, lo: float, hi: float) -> float:
        """Тик прерывистой ходьбы по линии (окно); возвращает новый x."""
        if self._walk_phase == "pause":
            self._pause_left -= dt
            if self._pause_left <= 0:
                self._begin_burst()
            return cur_x
        return self._advance_burst_x(cur_x, dt, lo, hi)

    def _tick_walk_desktop(self, dt: float) -> None:
        if self._walk_phase == "pause":
            self._pause_left -= dt
            if self._pause_left <= 0:
                self._begin_burst()
            return
        self._advance_burst_desktop(dt)

    def _step_desktop(self, dt: float) -> None:
        self._tick_walk_desktop(dt)
        self._x, self._y = self._clamp_desktop_xy(self._x, self._y)

        self._stay_left -= dt
        if self._stay_left <= 0:
            self._begin_takeoff()

    def _max_attach_attempts(self) -> int:
        return int(getattr(self._cfg, "max_attach_attempts", 3))

    def _choose_flight_kind(self) -> str:
        """Решение взлёта: «to_window» или «to_desktop»."""
        if self._mode == LocomotionMode.DESKTOP:
            chance = float(getattr(self._cfg, "fly_to_window_chance", 0.5))
            return "to_window" if self._rng.random() < chance else "to_desktop"
        chance = float(getattr(self._cfg, "leave_to_desktop_chance", 0.5))
        return "to_desktop" if self._rng.random() < chance else "to_window"

    def _pick_desktop_fly_point(self) -> tuple[float, float]:
        margin = float(getattr(self._cfg, "desktop_fly_margin_px", 80))
        x_lo = float(self._desktop.left) + margin
        x_hi = float(self._desktop.right) - margin - float(self._pet_w)
        y_lo = float(self._desktop.top) + margin
        y_hi = float(self._desktop.bottom) - margin - float(self._pet_h)
        if x_hi < x_lo or y_hi < y_lo:
            return self._clamp_desktop_xy(self._x, self._y)
        return (
            float(self._rng.uniform(x_lo, x_hi)),
            float(self._rng.uniform(y_lo, y_hi)),
        )

    def _begin_takeoff(self, *, to_window: bool | None = None) -> None:
        """Старт перелёта. to_window=None — решить по режиму и шансам из конфига."""
        self._prev_window_hwnd = self._attached_hwnd
        kind = (
            ("to_window" if to_window else "to_desktop")
            if to_window is not None
            else self._choose_flight_kind()
        )
        self._state = LocomotionState.TAKEOFF
        self._anim = "fly"
        self._phase_t = 0.0
        self._takeoff_from_y = self._y
        self._attached_hwnd = None
        self._attach_verify_left = None
        target_hwnd: int | None = None
        tx, ty = self._x, self._y

        if kind == "to_window":
            if self._attach_attempts >= self._max_attach_attempts():
                self._land_stay_on_desktop()
                return
            exclude = set(self._failed_attach_hwnds)
            if self._prev_window_hwnd is not None:
                exclude.add(self._prev_window_hwnd)
            pick = self._pick_window_target(exclude_hwnds=exclude)
            if pick is None and self._prev_window_hwnd is not None:
                # Единственное окно — допускаем то же, если других нет
                pick = self._pick_window_target(
                    exclude_hwnds=set(self._failed_attach_hwnds)
                )
            if pick is not None:
                target_hwnd, tx, ty = pick
                self._attach_attempts += 1
            else:
                if self._mode == LocomotionMode.DESKTOP:
                    # Некуда лететь — остаёмся на столе
                    self._land_stay_on_desktop()
                    return
                tx, ty = self._pick_desktop_fly_point()
                kind = "to_desktop"
            self._flight_to_desktop = kind == "to_desktop"
        else:
            tx, ty = self._pick_desktop_fly_point()
            self._flight_to_desktop = True
            self._attach_attempts = 0
            self._failed_attach_hwnds.clear()

        if self._flight_to_desktop:
            target_hwnd = None

        self._flight_target_hwnd = target_hwnd
        self._flight_x0 = self._x
        self._flight_y0 = self._y
        self._flight_x1 = tx
        self._flight_y1 = ty
        dist = ((tx - self._x) ** 2 + (ty - self._y) ** 2) ** 0.5
        speed = max(1.0, float(getattr(self._cfg, "fly_speed_px_s", 520)))
        self._flight_dur = max(0.05, dist / speed)
        self._heading_deg = _norm_angle_deg(
            math.degrees(math.atan2(ty - self._y, tx - self._x))
        )
        self._sync_facing_from_heading()
        our = self._our_hwnd_getter()
        if our and self._flight_to_desktop:
            self._api.detach_to_desktop(our)

    def _land_stay_on_desktop(self) -> None:
        self._mode = LocomotionMode.DESKTOP
        self._state = LocomotionState.ON_DESKTOP
        self._attach_attempts = 0
        self._failed_attach_hwnds.clear()
        self._x, self._y = self._clamp_desktop_xy(self._x, self._y)
        self._stay_left = self._roll_stay(
            getattr(self._cfg, "desktop_stay_sec", (8.0, 25.0))
        )
        self._begin_burst()
        our = self._our_hwnd_getter()
        if our:
            self._api.detach_to_desktop(our)

    def _pick_window_target(
        self, *, exclude_hwnds: set[int] | None = None
    ) -> tuple[int, float, float] | None:
        our = self._our_hwnd_getter()
        raw = self._api.list_windows()
        excluded = set(self._failed_attach_hwnds)
        if exclude_hwnds:
            excluded |= set(exclude_hwnds)
        cands = filter_window_candidates(
            raw,
            min_width=int(getattr(self._cfg, "min_window_width", 320)),
            min_height=int(getattr(self._cfg, "min_window_height", 160)),
            ignore_titles=list(getattr(self._cfg, "ignore_titles", [])),
            our_hwnd=our or None,
            exclude_hwnds=list(excluded),
        )
        usable: list[tuple[WindowInfo, Rect]] = []
        for info in cands:
            strip = self._api.title_bar_strip(info)
            if strip is None or strip.width < self._pet_w:
                continue
            usable.append((info, strip))
        if not usable:
            return None
        info, strip = self._rng.choice(usable)
        max_off = max(0, strip.width - self._pet_w)
        offset = float(self._rng.uniform(0, max_off)) if max_off else 0.0
        x = float(strip.left) + offset
        y = float(strip.top + strip.height / 2 - self._pet_h / 2)
        return info.hwnd, x, y

    def _step_takeoff(self, dt: float) -> None:
        self._anim = "fly"
        self._phase_t += dt
        t = min(1.0, self._phase_t / _TAKEOFF_SEC)
        self._y = self._takeoff_from_y - _TAKEOFF_RISE_PX * t
        if t >= 1.0:
            self._state = LocomotionState.IN_FLIGHT
            self._phase_t = 0.0
            self._flight_x0 = self._x
            self._flight_y0 = self._y

    def _step_flight(self, dt: float) -> None:
        self._anim = "fly"
        self._phase_t += dt
        dur = max(1e-6, self._flight_dur)
        t = min(1.0, self._phase_t / dur)
        self._x = self._flight_x0 + (self._flight_x1 - self._flight_x0) * t
        self._y = self._flight_y0 + (self._flight_y1 - self._flight_y0) * t
        dx = self._flight_x1 - self._flight_x0
        dy = self._flight_y1 - self._flight_y0
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            self._heading_deg = _norm_angle_deg(math.degrees(math.atan2(dy, dx)))
            self._sync_facing_from_heading()
        if t >= 1.0:
            self._x = self._flight_x1
            self._y = self._flight_y1
            self._begin_landing()

    def _begin_landing(self) -> None:
        self._state = LocomotionState.LANDING
        self._anim = "land"
        self._phase_t = 0.0
        if self._flight_to_desktop or self._flight_target_hwnd is None:
            self._attached_hwnd = None
            our = self._our_hwnd_getter()
            if our:
                self._api.detach_to_desktop(our)
        else:
            target = self._flight_target_hwnd
            if not self._api.is_window(target):
                self._attached_hwnd = None
                our = self._our_hwnd_getter()
                if our:
                    self._api.detach_to_desktop(our)
                return
            self._attached_hwnd = target
            our = self._our_hwnd_getter()
            if our and self._attached_hwnd:
                self._api.attach_above(our, self._attached_hwnd)
            info = self._api.refresh_window(self._attached_hwnd)
            if info is not None:
                strip = self._api.title_bar_strip(info)
                if strip is not None:
                    self._window_offset_x = self._x - float(strip.left)

    def _step_landing(self, dt: float) -> None:
        self._anim = "land"
        self._phase_t += dt
        if self._attached_hwnd is not None:
            if not self._api.is_window(self._attached_hwnd) or not self._sync_to_window():
                self._begin_takeoff(to_window=False)
                return
            our = self._our_hwnd_getter()
            if our:
                self._api.attach_above(our, self._attached_hwnd)
        if self._phase_t >= _LANDING_SEC:
            if self._attached_hwnd is None:
                # Смена режима — только здесь, на посадке
                self._mode = LocomotionMode.DESKTOP
                self._state = LocomotionState.ON_DESKTOP
                self._x, self._y = self._clamp_desktop_xy(self._x, self._y)
                self._attach_attempts = 0
                self._failed_attach_hwnds.clear()
                self._stay_left = self._roll_stay(
                    getattr(self._cfg, "desktop_stay_sec", (8.0, 25.0))
                )
                self._begin_burst()
            else:
                self._mode = LocomotionMode.WINDOW
                self._state = LocomotionState.ON_WINDOW
                self._heading_deg = 0.0 if self._facing >= 0 else 180.0
                self._attach_verify_left = _ATTACH_VERIFY_TICKS
                self._stay_left = self._roll_stay(
                    getattr(self._cfg, "window_stay_sec", (6.0, 18.0))
                )
                self._begin_burst()

    def _step_on_window(self, dt: float) -> None:
        if self._attached_hwnd is None or not self._api.is_window(self._attached_hwnd):
            self._begin_takeoff(to_window=False)
            return
        if not self._sync_to_window():
            self._begin_takeoff(to_window=False)
            return
        our = self._our_hwnd_getter()
        if our:
            self._api.attach_above(our, self._attached_hwnd)

        if self._attach_verify_left is not None:
            self._attach_verify_left -= 1
            if self._attach_verify_left <= 0:
                self._attach_verify_left = None
                if not self._verify_attach_visible():
                    return

        info = self._api.refresh_window(self._attached_hwnd)
        if info is None:
            self._begin_takeoff(to_window=False)
            return
        strip = self._api.title_bar_strip(info)
        if strip is None or strip.width < self._pet_w:
            self._begin_takeoff(to_window=False)
            return

        max_off = float(max(0, strip.width - self._pet_w))
        self._window_offset_x = self._tick_walk_or_pause(
            dt, self._window_offset_x, 0.0, max_off
        )
        self._x = float(strip.left) + self._window_offset_x
        self._y = float(strip.top + strip.height / 2 - self._pet_h / 2)

        self._stay_left -= dt
        if self._stay_left <= 0:
            self._attach_attempts = 0
            self._failed_attach_hwnds.clear()
            self._begin_takeoff()

    def _verify_attach_visible(self) -> bool:
        """True, если мы над целью в z-порядке; иначе откат на стол/другое окно.

        ``WindowFromPoint`` по центру спрайта дополнительно логируется, но сам по себе
        ненадёжен: прозрачные пиксели спрайта пробиваются до дочернего рендера цели.
        Критерий успеха — цель непосредственно под нами (``GetWindow`` / GW_HWNDNEXT).
        """
        our = self._our_hwnd_getter()
        failed = self._attached_hwnd
        if not our or failed is None:
            return True
        cx = int(self._x + self._pet_w / 2)
        cy = int(self._y + self._pet_h / 2)
        at = self._api.window_from_point(cx, cy)
        z_ok = self._api.is_immediately_above(our, failed)
        point_ok = self._api.is_our_window(at, our)
        if z_ok:
            if not point_ok:
                logger.debug(
                    "посадка ок по z-порядку, но в точке (%s,%s) hwnd=%s (прозрачность?)",
                    cx,
                    cy,
                    at,
                )
            self._attach_attempts = 0
            self._failed_attach_hwnds.clear()
            return True
        logger.debug(
            "посадка неудачна: z-порядок сбит, в точке (%s,%s) hwnd=%s, цель=%s — откат",
            cx,
            cy,
            at,
            failed,
        )
        self._failed_attach_hwnds.add(failed)
        self._attached_hwnd = None
        self._attach_verify_left = None
        self._api.detach_to_desktop(our)
        # Режим ещё WINDOW до посадки; сразу пробуем другое окно
        self._x, self._y = self._clamp_desktop_xy(self._x, self._y)
        self._begin_takeoff(to_window=True)
        return False

    def _sync_to_window(self) -> bool:
        """Пересчитать позицию по текущему rect цели. False — окно пропало."""
        assert self._attached_hwnd is not None
        if not self._api.is_window(self._attached_hwnd):
            self._attached_hwnd = None
            return False
        info = self._api.refresh_window(self._attached_hwnd)
        if info is None:
            self._attached_hwnd = None
            return False
        if not info.visible or info.iconic:
            self._attached_hwnd = None
            return False
        if not info.intersects_screen:
            self._attached_hwnd = None
            return False
        if info.rect.width <= 0 or info.rect.height <= 0:
            self._attached_hwnd = None
            return False
        strip = self._api.title_bar_strip(info)
        if strip is None:
            self._attached_hwnd = None
            return False
        max_off = float(max(0, strip.width - self._pet_w))
        self._window_offset_x = max(0.0, min(max_off, self._window_offset_x))
        self._x = float(strip.left) + self._window_offset_x
        self._y = float(strip.top + strip.height / 2 - self._pet_h / 2)
        return True
