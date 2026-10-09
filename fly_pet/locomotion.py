"""Локомоция мухи: ходьба по столу, полёт и посадка на чужие окна."""

from __future__ import annotations

import ctypes
import logging
import random
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol, Sequence

from ctypes import wintypes

logger = logging.getLogger("fly_pet")

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
        desktop = self.find_desktop_hwnd()
        insert_after = desktop if desktop else HWND_TOP
        ctypes.windll.user32.SetWindowPos(
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
    ) -> None:
        self._cfg = walk_cfg
        self._pet_w = int(pet_width)
        self._pet_h = int(pet_height)
        self._desktop = desktop
        self._api = api
        self._our_hwnd_getter = our_hwnd_getter or (lambda: 0)
        seed = getattr(walk_cfg, "seed", None)
        self._rng = rng if rng is not None else random.Random(seed)
        self._state = LocomotionState.ON_DESKTOP
        self._x = float(desktop.left + max(0, (desktop.width - pet_width) // 2))
        self._y = self._desktop_y()
        self._facing = 1
        self._vx = float(getattr(walk_cfg, "desktop_speed_px_s", 26))
        self._paused = False
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
        self._window_offset_x = 0.0
        self._anim = "walk"
        self._attach_attempts = 0
        self._failed_attach_hwnds: set[int] = set()
        self._attach_verify_left: int | None = None

    @property
    def state(self) -> LocomotionState:
        return self._state

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

    def resume_from_desktop(self, x: float, y: float) -> None:
        """После перетаскивания человеком — снова с рабочего стола."""
        self._paused = False
        self._x = float(x)
        self._y = self._desktop_y()
        self._attached_hwnd = None
        self._flight_target_hwnd = None
        self._flight_to_desktop = False
        self._attach_verify_left = None
        self._attach_attempts = 0
        self._failed_attach_hwnds.clear()
        self._state = LocomotionState.ON_DESKTOP
        self._anim = "walk"
        self._stay_left = self._roll_stay(
            getattr(self._cfg, "desktop_stay_sec", (8.0, 25.0))
        )
        self._vx = abs(float(getattr(self._cfg, "desktop_speed_px_s", 26))) * (
            1 if self._facing >= 0 else -1
        )
        our = self._our_hwnd_getter()
        if our:
            self._api.detach_to_desktop(our)

    def set_desktop(self, desktop: Rect) -> None:
        self._desktop = desktop

    def step(self, dt_sec: float) -> LocomotionPose:
        if self._paused or dt_sec <= 0:
            return self.pose
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
        margin = float(getattr(self._cfg, "desktop_margin_px", 12))
        return float(self._desktop.bottom - margin - self._pet_h)

    def _roll_stay(self, pair: Sequence[float]) -> float:
        lo, hi = float(pair[0]), float(pair[1])
        if hi < lo:
            lo, hi = hi, lo
        return float(self._rng.uniform(lo, hi))

    def _clamp_desktop_x(self, x: float) -> float:
        lo = float(self._desktop.left)
        hi = float(self._desktop.right - self._pet_w)
        if hi < lo:
            return lo
        return max(lo, min(hi, x))

    def _step_desktop(self, dt: float) -> None:
        self._y = self._desktop_y()
        pause_chance = float(getattr(self._cfg, "pause_chance", 0.15))
        speed = float(getattr(self._cfg, "desktop_speed_px_s", 26))
        if self._rng.random() < pause_chance:
            self._anim = "idle"
        else:
            self._anim = "walk"
            self._x = self._clamp_desktop_x(self._x + self._vx * dt)
            lo = float(self._desktop.left)
            hi = float(self._desktop.right - self._pet_w)
            if self._x <= lo and self._vx < 0:
                self._vx = abs(speed)
                self._facing = 1
                self._x = lo
            elif self._x >= hi and self._vx > 0:
                self._vx = -abs(speed)
                self._facing = -1
                self._x = hi
            else:
                self._facing = 1 if self._vx >= 0 else -1

        self._stay_left -= dt
        if self._stay_left <= 0:
            self._begin_takeoff(to_window=True)

    def _max_attach_attempts(self) -> int:
        return int(getattr(self._cfg, "max_attach_attempts", 3))

    def _begin_takeoff(self, *, to_window: bool) -> None:
        self._state = LocomotionState.TAKEOFF
        self._anim = "fly"
        self._phase_t = 0.0
        self._takeoff_from_y = self._y
        self._attached_hwnd = None
        self._attach_verify_left = None
        target_hwnd: int | None = None
        tx, ty = self._x, self._desktop_y()
        if to_window:
            if self._attach_attempts >= self._max_attach_attempts():
                # Лимит неудачных посадок — только ходьба по столу
                self._state = LocomotionState.ON_DESKTOP
                self._anim = "walk"
                self._attach_attempts = 0
                self._failed_attach_hwnds.clear()
                self._stay_left = self._roll_stay(
                    getattr(self._cfg, "desktop_stay_sec", (8.0, 25.0))
                )
                our = self._our_hwnd_getter()
                if our:
                    self._api.detach_to_desktop(our)
                return
            pick = self._pick_window_target()
            if pick is not None:
                target_hwnd, tx, ty = pick
                self._attach_attempts += 1
            else:
                # Некуда лететь — остаёмся на столе
                self._state = LocomotionState.ON_DESKTOP
                self._anim = "walk"
                self._attach_attempts = 0
                self._failed_attach_hwnds.clear()
                self._stay_left = self._roll_stay(
                    getattr(self._cfg, "desktop_stay_sec", (8.0, 25.0))
                )
                return
            self._flight_to_desktop = False
        else:
            tx = self._clamp_desktop_x(self._x)
            ty = self._desktop_y()
            self._flight_to_desktop = True
            self._attach_attempts = 0
            self._failed_attach_hwnds.clear()
        self._flight_target_hwnd = target_hwnd
        self._flight_x0 = self._x
        self._flight_y0 = self._y
        self._flight_x1 = tx
        self._flight_y1 = ty
        dist = ((tx - self._x) ** 2 + (ty - self._y) ** 2) ** 0.5
        speed = max(1.0, float(getattr(self._cfg, "fly_speed_px_s", 520)))
        self._flight_dur = max(0.05, dist / speed)
        if tx != self._x:
            self._facing = 1 if tx > self._x else -1
        our = self._our_hwnd_getter()
        if our and to_window is False:
            self._api.detach_to_desktop(our)

    def _pick_window_target(self) -> tuple[int, float, float] | None:
        our = self._our_hwnd_getter()
        raw = self._api.list_windows()
        cands = filter_window_candidates(
            raw,
            min_width=int(getattr(self._cfg, "min_window_width", 320)),
            min_height=int(getattr(self._cfg, "min_window_height", 160)),
            ignore_titles=list(getattr(self._cfg, "ignore_titles", [])),
            our_hwnd=our or None,
            exclude_hwnds=list(self._failed_attach_hwnds),
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
        if self._flight_x1 != self._flight_x0:
            self._facing = 1 if self._flight_x1 > self._flight_x0 else -1
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
                self._state = LocomotionState.ON_DESKTOP
                self._anim = "walk"
                self._y = self._desktop_y()
                self._attach_attempts = 0
                self._failed_attach_hwnds.clear()
                self._stay_left = self._roll_stay(
                    getattr(self._cfg, "desktop_stay_sec", (8.0, 25.0))
                )
                self._vx = abs(float(getattr(self._cfg, "desktop_speed_px_s", 26))) * (
                    1 if self._facing >= 0 else -1
                )
            else:
                self._state = LocomotionState.ON_WINDOW
                self._anim = "walk"
                self._attach_verify_left = _ATTACH_VERIFY_TICKS
                speed = float(getattr(self._cfg, "window_speed_px_s", 22))
                self._vx = abs(speed) * (1 if self._facing >= 0 else -1)
                self._stay_left = self._roll_stay(
                    getattr(self._cfg, "window_stay_sec", (6.0, 18.0))
                )

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

        pause_chance = float(getattr(self._cfg, "pause_chance", 0.15))
        speed = float(getattr(self._cfg, "window_speed_px_s", 22))
        if self._rng.random() < pause_chance:
            self._anim = "idle"
        else:
            self._anim = "walk"
            self._window_offset_x += self._vx * dt
            max_off = float(max(0, strip.width - self._pet_w))
            if self._window_offset_x <= 0 and self._vx < 0:
                self._window_offset_x = 0.0
                self._vx = abs(speed)
                self._facing = 1
            elif self._window_offset_x >= max_off and self._vx > 0:
                self._window_offset_x = max_off
                self._vx = -abs(speed)
                self._facing = -1
            else:
                self._facing = 1 if self._vx >= 0 else -1
            self._x = float(strip.left) + self._window_offset_x
            self._y = float(strip.top + strip.height / 2 - self._pet_h / 2)

        self._stay_left -= dt
        if self._stay_left <= 0:
            # Улететь на стол или на другое окно
            self._attach_attempts = 0
            self._failed_attach_hwnds.clear()
            go_desktop = self._rng.random() < 0.45
            self._begin_takeoff(to_window=not go_desktop)

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
        # На стол, затем снова выбрать другое окно (с учётом лимита попыток)
        self._y = self._desktop_y()
        self._x = self._clamp_desktop_x(self._x)
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
