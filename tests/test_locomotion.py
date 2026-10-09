"""Тесты локомоции (headless, Win32 подменён)."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

import pytest

from fly_pet.locomotion import (
    Locomotion,
    LocomotionState,
    Rect,
    WindowInfo,
    filter_window_candidates,
    title_bar_strip_from_metrics,
)


@dataclass
class FakeWalk:
    enabled: bool = True
    tick_ms: int = 40
    desktop_speed_px_s: float = 100.0
    window_speed_px_s: float = 50.0
    fly_speed_px_s: float = 500.0
    desktop_margin_px: int = 10
    desktop_stay_sec: tuple[float, float] = (1000.0, 1000.0)
    window_stay_sec: tuple[float, float] = (1000.0, 1000.0)
    pause_chance: float = 0.0
    min_window_width: int = 320
    min_window_height: int = 160
    ignore_titles: list[str] | None = None
    max_attach_attempts: int = 3
    seed: int | None = 1

    def __post_init__(self) -> None:
        if self.ignore_titles is None:
            self.ignore_titles = []


def _win(
    hwnd: int,
    title: str = "Ok",
    class_name: str = "Chrome_WidgetWin_1",
    rect: Rect | None = None,
    **kwargs: object,
) -> WindowInfo:
    return WindowInfo(
        hwnd,
        title,
        class_name,
        rect or Rect(0, 0, 400, 300),
        False,
        **kwargs,  # type: ignore[arg-type]
    )


class FakeApi:
    def __init__(self, windows: list[WindowInfo] | None = None) -> None:
        self.windows = list(windows or [])
        self.attach_calls: list[tuple[int, int]] = []
        self.detach_calls: list[int] = []
        self.point_hwnd: int = 42  # по умолчанию в точке — наша муха
        self.z_above_ok: bool = True  # по умолчанию z-порядок верный

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows)

    def refresh_window(self, hwnd: int) -> WindowInfo | None:
        for info in self.windows:
            if info.hwnd == hwnd:
                return info
        return None

    def title_bar_strip(self, info: WindowInfo) -> Rect | None:
        return title_bar_strip_from_metrics(
            info.rect,
            has_caption=info.has_caption,
            caption_plus_frame=31,
            client_top_screen=info.client_top_screen,
        )

    def attach_above(self, our_hwnd: int, target_hwnd: int) -> None:
        if not self.is_window(target_hwnd):
            return
        self.attach_calls.append((our_hwnd, target_hwnd))

    def detach_to_desktop(self, our_hwnd: int) -> None:
        self.detach_calls.append(our_hwnd)

    def find_desktop_hwnd(self) -> int | None:
        return 1

    def window_from_point(self, x: int, y: int) -> int:
        del x, y
        return int(self.point_hwnd)

    def is_window(self, hwnd: int) -> bool:
        if hwnd == 42:
            return True
        return any(info.hwnd == hwnd for info in self.windows)

    def is_our_window(self, hwnd: int, our_hwnd: int) -> bool:
        return hwnd == our_hwnd

    def is_immediately_above(self, our_hwnd: int, target_hwnd: int) -> bool:
        del our_hwnd, target_hwnd
        return bool(self.z_above_ok)

    def move_window(self, hwnd: int, dx: int, dy: int) -> None:
        updated: list[WindowInfo] = []
        for info in self.windows:
            if info.hwnd != hwnd:
                updated.append(info)
                continue
            new_rect = info.rect.shifted(dx, dy)
            client = None
            if info.client_top_screen is not None:
                client = info.client_top_screen + dy
            updated.append(
                replace(info, rect=new_rect, client_top_screen=client)
            )
        self.windows = updated


def _make_loco(
    *,
    cfg: FakeWalk | None = None,
    api: FakeApi | None = None,
    desktop: Rect | None = None,
    pet_w: int = 96,
    pet_h: int = 96,
    seed: int = 1,
) -> tuple[Locomotion, FakeApi, FakeWalk]:
    walk = cfg or FakeWalk(seed=seed)
    fake = api or FakeApi()
    desk = desktop or Rect(0, 0, 1000, 800)
    loco = Locomotion(
        walk,
        pet_width=pet_w,
        pet_height=pet_h,
        desktop=desk,
        api=fake,
        our_hwnd_getter=lambda: 42,
        rng=random.Random(seed),
    )
    return loco, fake, walk


def test_filter_empty_stays_desktop() -> None:
    loco, api, cfg = _make_loco(cfg=FakeWalk(desktop_stay_sec=(0.01, 0.01), seed=7))
    api.windows = []
    # Дождаться взлёта: пустой список → остаёмся на столе
    for _ in range(5):
        loco.step(0.02)
    assert loco.state == LocomotionState.ON_DESKTOP


def test_filter_by_title_size_class() -> None:
    windows = [
        _win(1, "Ok"),
        _win(2, "Tiny", rect=Rect(0, 0, 100, 100)),
        _win(3, "Secret"),
        _win(4, "Desktop", "Progman", Rect(0, 0, 800, 600)),
        _win(5, ""),
    ]
    got = filter_window_candidates(
        windows,
        min_width=320,
        min_height=160,
        ignore_titles=["Secret"],
    )
    assert [w.hwnd for w in got] == [1]


def test_filter_rejects_each_rule() -> None:
    """По одному кейсу на каждый пункт жёсткого фильтра."""
    good = _win(1, "Good", has_caption=True, has_sysmenu=True)
    cases = [
        replace(good, hwnd=2, visible=False),
        replace(good, hwnd=3, iconic=True),
        replace(good, hwnd=4, rect=Rect(0, 0, 0, 0)),
        replace(good, hwnd=5, rect=Rect(0, 0, 100, 100)),  # меньше min
        replace(good, hwnd=6, intersects_screen=False),
        replace(good, hwnd=7, has_owner=True),
        replace(good, hwnd=8, is_tool_window=True),
        replace(good, hwnd=9, has_caption=False, has_sysmenu=False),
        replace(good, hwnd=10, class_name="IME"),
        replace(good, hwnd=11, class_name="ASUS AsHotkeyExec App Class"),
        replace(good, hwnd=12, class_name="ApplicationFrameWindow"),
        replace(good, hwnd=13, title=""),
        replace(good, hwnd=14, title="Secret"),
    ]
    got = filter_window_candidates(
        [good, *cases],
        min_width=320,
        min_height=160,
        ignore_titles=["Secret"],
    )
    assert [w.hwnd for w in got] == [1]


def test_filter_accepts_sysmenu_without_caption() -> None:
    win = _win(1, "Borderless", has_caption=False, has_sysmenu=True)
    got = filter_window_candidates(
        [win], min_width=320, min_height=160, ignore_titles=[]
    )
    assert [w.hwnd for w in got] == [1]


def test_title_bar_strip_caption_and_custom() -> None:
    rect = Rect(100, 200, 500, 600)
    caption = title_bar_strip_from_metrics(
        rect, has_caption=True, caption_plus_frame=31, client_top_screen=None
    )
    assert caption is not None
    assert caption.top == 200
    assert caption.bottom == 231
    assert caption.height == 31

    custom_low = title_bar_strip_from_metrics(
        rect, has_caption=False, caption_plus_frame=31, client_top_screen=210
    )
    assert custom_low is not None
    assert custom_low.height == 24  # clamp min

    custom_high = title_bar_strip_from_metrics(
        rect, has_caption=False, caption_plus_frame=31, client_top_screen=280
    )
    assert custom_high is not None
    assert custom_high.height == 44  # clamp max

    degenerate = title_bar_strip_from_metrics(
        Rect(0, 0, 0, 10),
        has_caption=True,
        caption_plus_frame=31,
        client_top_screen=None,
    )
    assert degenerate is None


def test_desktop_walk_distance_and_bounce() -> None:
    loco, _api, _cfg = _make_loco(
        cfg=FakeWalk(desktop_speed_px_s=100.0, pause_chance=0.0, seed=1),
        desktop=Rect(0, 0, 500, 400),
        pet_w=50,
        pet_h=50,
    )
    loco.resume_from_desktop(0.0, 0.0)
    # принудительно направление вправо
    loco._facing = 1
    loco._vx = 100.0
    x0 = loco.pose.x
    for _ in range(10):
        loco.step(0.1)  # 10 * 10px = 100px
    assert loco.pose.x == pytest.approx(x0 + 100.0, abs=0.5)

    # Упираемся в правый край и отражаемся
    loco._x = 450.0  # max = 500-50=450
    loco._vx = 100.0
    loco.step(0.1)
    assert loco._vx < 0
    assert loco.pose.facing == -1


def test_flight_approaches_and_lands() -> None:
    win = WindowInfo(
        10,
        "Target",
        "Chrome_WidgetWin_1",
        Rect(400, 100, 900, 500),
        False,
        has_caption=True,
    )
    api = FakeApi([win])
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            desktop_stay_sec=(0.01, 0.01),
            pause_chance=0.0,
            fly_speed_px_s=400.0,
            seed=2,
        ),
        api=api,
        desktop=Rect(0, 0, 1000, 800),
    )
    loco.resume_from_desktop(50.0, 0.0)
    # форсируем взлёт к окну
    loco._begin_takeoff(to_window=True)
    assert loco.state == LocomotionState.TAKEOFF
    # добираем takeoff
    loco.step(_TAKEOFF := 0.4)
    assert loco.state == LocomotionState.IN_FLIGHT
    prev_dist = None
    target = (loco._flight_x1, loco._flight_y1)
    for _ in range(40):
        pose = loco.step(0.05)
        dist = ((pose.x - target[0]) ** 2 + (pose.y - target[1]) ** 2) ** 0.5
        if prev_dist is not None and loco.state == LocomotionState.IN_FLIGHT:
            assert dist <= prev_dist + 1e-6
        prev_dist = dist
        if loco.state in {LocomotionState.LANDING, LocomotionState.ON_WINDOW}:
            break
    assert loco.state in {LocomotionState.LANDING, LocomotionState.ON_WINDOW}


def test_on_window_follows_target_move() -> None:
    win = WindowInfo(
        10,
        "Target",
        "Chrome_WidgetWin_1",
        Rect(200, 100, 700, 500),
        False,
        has_caption=True,
    )
    api = FakeApi([win])
    loco, _, _ = _make_loco(
        cfg=FakeWalk(pause_chance=0.0, window_stay_sec=(1000, 1000), seed=3),
        api=api,
    )
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 40.0
    loco._vx = 0.0
    loco._anim = "idle"
    loco._sync_to_window()
    x0, y0 = loco.pose.x, loco.pose.y
    api.move_window(10, 30, -15)
    assert loco._sync_to_window() is True
    assert loco.pose.x == pytest.approx(x0 + 30, abs=0.5)
    assert loco.pose.y == pytest.approx(y0 - 15, abs=0.5)


def test_missing_window_triggers_takeoff() -> None:
    win = WindowInfo(
        10,
        "Target",
        "Chrome_WidgetWin_1",
        Rect(200, 100, 700, 500),
        False,
        has_caption=True,
    )
    api = FakeApi([win])
    loco, _, _ = _make_loco(api=api, cfg=FakeWalk(seed=4))
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 10.0
    api.windows = []
    loco.step(0.05)
    assert loco.state == LocomotionState.TAKEOFF


def test_seed_determinism() -> None:
    def run(seed: int) -> list[str]:
        api = FakeApi(
            [
                WindowInfo(
                    10,
                    "A",
                    "X",
                    Rect(0, 0, 500, 400),
                    False,
                    has_caption=True,
                )
            ]
        )
        loco, _, _ = _make_loco(
            cfg=FakeWalk(
                desktop_stay_sec=(0.2, 0.2),
                window_stay_sec=(0.2, 0.2),
                pause_chance=0.2,
                fly_speed_px_s=800.0,
                seed=seed,
            ),
            api=api,
            seed=seed,
        )
        loco.resume_from_desktop(10.0, 0.0)
        seq: list[str] = []
        for _ in range(80):
            pose = loco.step(0.05)
            seq.append(pose.state.value)
        return seq

    assert run(42) == run(42)
    assert run(42) != run(43)


def test_failed_attach_detaches_and_retries() -> None:
    """Неудачная посадка (сбит z-порядок) → detach и выбор другого окна."""
    win_a = _win(10, "A", rect=Rect(200, 100, 700, 500), has_caption=True)
    win_b = _win(20, "B", rect=Rect(100, 50, 600, 450), has_caption=True)
    api = FakeApi([win_a, win_b])
    api.z_above_ok = False
    api.point_hwnd = 999  # чужой рендер поверх
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            pause_chance=0.0,
            window_stay_sec=(1000, 1000),
            max_attach_attempts=3,
            seed=5,
        ),
        api=api,
    )
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 10.0
    loco._attach_attempts = 1
    loco._attach_verify_left = 1
    loco._sync_to_window()
    loco.step(0.05)
    assert 10 in loco._failed_attach_hwnds
    assert api.detach_calls  # откат на стол
    assert loco.state in {
        LocomotionState.TAKEOFF,
        LocomotionState.ON_DESKTOP,
        LocomotionState.IN_FLIGHT,
    }
    # Не возвращаемся на проваленный hwnd в этой серии
    if loco._flight_target_hwnd is not None:
        assert loco._flight_target_hwnd != 10


def test_max_attach_attempts_stays_on_desktop() -> None:
    """После max_attach_attempts подряд — только ходьба по столу."""
    win = _win(10, "A", rect=Rect(200, 100, 700, 500), has_caption=True)
    api = FakeApi([win])
    api.z_above_ok = False
    api.point_hwnd = 999
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            pause_chance=0.0,
            desktop_stay_sec=(1000, 1000),
            window_stay_sec=(1000, 1000),
            max_attach_attempts=2,
            seed=6,
        ),
        api=api,
    )
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 10.0
    loco._attach_attempts = 2  # лимит уже исчерпан
    loco._failed_attach_hwnds.add(10)
    loco._attach_verify_left = 1
    loco._sync_to_window()
    loco.step(0.05)
    assert loco.state == LocomotionState.ON_DESKTOP
    assert loco._attached_hwnd is None


def test_successful_attach_verify_clears_attempts() -> None:
    win = _win(10, "A", rect=Rect(200, 100, 700, 500), has_caption=True)
    api = FakeApi([win])
    api.z_above_ok = True
    api.point_hwnd = 999  # прозрачность: в точке не мы, но z-порядок ок
    loco, _, _ = _make_loco(
        cfg=FakeWalk(pause_chance=0.0, window_stay_sec=(1000, 1000), seed=8),
        api=api,
    )
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 10.0
    loco._attach_attempts = 2
    loco._attach_verify_left = 1
    loco._sync_to_window()
    loco.step(0.05)
    assert loco.state == LocomotionState.ON_WINDOW
    assert loco._attach_attempts == 0
    assert not loco._failed_attach_hwnds
