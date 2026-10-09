"""Тесты локомоции (headless, Win32 подменён)."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

import pytest

from fly_pet.locomotion import (
    Locomotion,
    LocomotionMode,
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
    desktop_speed_px_s: float = 100.0  # устарело
    window_speed_px_s: float = 50.0  # устарело
    burst_px: tuple[float, float] = (40.0, 40.0)
    burst_speed_px_s: float = 100.0
    dash_animation_fps: int = 16
    pause_sec: tuple[float, float] = (0.2, 0.2)
    groom_chance: float = 0.0
    groom_sec: tuple[float, float] = (1.0, 1.0)
    long_burst_chance: float = 0.0
    long_burst_px: tuple[float, float] = (200.0, 200.0)
    turn_on_pause_chance: float = 0.0
    fly_to_window_chance: float = 0.5
    leave_to_desktop_chance: float = 0.5
    desktop_turn_deg: tuple[float, float] = (20.0, 90.0)
    desktop_fly_margin_px: int = 80
    fly_speed_px_s: float = 500.0
    desktop_margin_px: int = 10
    desktop_stay_sec: tuple[float, float] = (1000.0, 1000.0)
    window_stay_sec: tuple[float, float] = (1000.0, 1000.0)
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
    loco, api, cfg = _make_loco(
        cfg=FakeWalk(
            desktop_stay_sec=(0.01, 0.01),
            fly_to_window_chance=1.0,
            seed=7,
        )
    )
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


def test_desktop_burst_distance_and_bounce() -> None:
    loco, _api, _cfg = _make_loco(
        cfg=FakeWalk(
            burst_px=(100.0, 100.0),
            burst_speed_px_s=100.0,
            pause_sec=(10.0, 10.0),  # длинная пауза после рывка
            desktop_margin_px=0,
            seed=1,
        ),
        desktop=Rect(0, 0, 500, 400),
        pet_w=50,
        pet_h=50,
    )
    loco.resume_from_desktop(0.0, 100.0)
    loco._heading_deg = 0.0
    loco._begin_burst()
    x0 = loco.pose.x
    y0 = loco.pose.y
    for _ in range(10):
        loco.step(0.1)  # 10 * 10px = 100px рывок вправо
    assert loco.pose.x == pytest.approx(x0 + 100.0, abs=0.5)
    assert loco.pose.y == pytest.approx(y0, abs=0.5)
    assert loco._walk_phase == "pause"

    # Упираемся в правый край и отражаемся во время рывка
    loco._x = 450.0  # max = 500-50=450
    loco._heading_deg = 0.0
    loco._begin_burst()
    loco._burst_left = 50.0
    loco.step(0.1)
    assert loco.pose.facing == -1
    assert loco.pose.x <= 450.0


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
    for _ in range(80):
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
        cfg=FakeWalk(window_stay_sec=(1000, 1000), seed=3),
        api=api,
    )
    loco._mode = LocomotionMode.WINDOW
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
    loco._mode = LocomotionMode.WINDOW
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
                pause_sec=(0.05, 0.15),
                groom_chance=0.2,
                turn_on_pause_chance=0.3,
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
            window_stay_sec=(1000, 1000),
            max_attach_attempts=3,
            seed=5,
        ),
        api=api,
    )
    loco._mode = LocomotionMode.WINDOW
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
            desktop_stay_sec=(1000, 1000),
            window_stay_sec=(1000, 1000),
            max_attach_attempts=2,
            seed=6,
        ),
        api=api,
    )
    loco._mode = LocomotionMode.WINDOW
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 10.0
    loco._attach_attempts = 2  # лимит уже исчерпан
    loco._failed_attach_hwnds.add(10)
    loco._attach_verify_left = 1
    loco._sync_to_window()
    loco.step(0.05)
    assert loco.state == LocomotionState.ON_DESKTOP
    assert loco.mode == LocomotionMode.DESKTOP
    assert loco._attached_hwnd is None


def test_successful_attach_verify_clears_attempts() -> None:
    win = _win(10, "A", rect=Rect(200, 100, 700, 500), has_caption=True)
    api = FakeApi([win])
    api.z_above_ok = True
    api.point_hwnd = 999  # прозрачность: в точке не мы, но z-порядок ок
    loco, _, _ = _make_loco(
        cfg=FakeWalk(window_stay_sec=(1000, 1000), seed=8),
        api=api,
    )
    loco._mode = LocomotionMode.WINDOW
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


def test_burst_planner_lengths_and_pauses() -> None:
    cfg = FakeWalk(
        burst_px=(28.0, 120.0),
        long_burst_px=(180.0, 380.0),
        long_burst_chance=0.12,
        pause_sec=(0.25, 1.4),
        groom_chance=0.0,
        seed=11,
    )
    loco, _, _ = _make_loco(cfg=cfg, seed=11)
    lengths: list[float] = []
    longs = 0
    for _ in range(300):
        dist, is_long = loco._roll_burst_distance()
        lengths.append(dist)
        if is_long:
            longs += 1
            assert 180.0 <= dist <= 380.0
        else:
            assert 28.0 <= dist <= 120.0
    assert 0.02 <= longs / 300 <= 0.25  # редко, допуск шире ±10 п.п.

    # Между рывками есть паузы ненулевой длины
    loco.resume_from_desktop(100.0, 0.0)
    loco._begin_burst()
    saw_pause = False
    for _ in range(200):
        loco.step(0.05)
        if loco._walk_phase == "pause":
            assert loco._pause_left > 0
            saw_pause = True
            break
    assert saw_pause


def test_pause_position_frozen() -> None:
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            burst_px=(30.0, 30.0),
            burst_speed_px_s=300.0,
            pause_sec=(2.0, 2.0),
            groom_chance=0.0,
            seed=12,
        ),
        seed=12,
    )
    loco.resume_from_desktop(200.0, 0.0)
    loco._begin_burst()
    # Дождаться паузы
    for _ in range(50):
        loco.step(0.05)
        if loco._walk_phase == "pause":
            break
    assert loco._walk_phase == "pause"
    x0 = loco.pose.x
    for _ in range(8):
        loco.step(0.05)
        assert loco.pose.x == x0
        assert loco._walk_phase == "pause"


def test_groom_and_turn_rates() -> None:
    groom_chance = 0.45
    turn_chance = 0.35
    cfg = FakeWalk(
        groom_chance=groom_chance,
        turn_on_pause_chance=turn_chance,
        pause_sec=(0.5, 0.5),
        groom_sec=(0.5, 0.5),
        seed=99,
    )
    loco, _, _ = _make_loco(cfg=cfg, seed=99)
    loco._mode = LocomotionMode.WINDOW
    n = 200
    grooms = 0
    turns = 0
    for _ in range(n):
        loco._heading_deg = 0.0
        loco._facing = 1
        loco._begin_pause()
        if loco._last_pause_groomed:
            grooms += 1
            assert loco.pose.anim == "rub"
        else:
            assert loco.pose.anim == "idle"
        if loco._last_pause_turned:
            turns += 1
            assert loco._facing == -1
    assert abs(grooms / n - groom_chance) <= 0.10
    assert abs(turns / n - turn_chance) <= 0.10


def test_burst_anim_states() -> None:
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            burst_px=(50.0, 50.0),
            burst_speed_px_s=200.0,
            pause_sec=(0.5, 0.5),
            groom_chance=1.0,
            seed=13,
        ),
        seed=13,
    )
    loco.resume_from_desktop(100.0, 0.0)
    loco._begin_burst()
    assert loco.pose.anim == "walk"
    loco.step(0.3)  # рывок закончится → пауза с rub
    assert loco._walk_phase == "pause"
    assert loco.pose.anim == "rub"


def test_burst_stays_inside_desktop_bounds() -> None:
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            burst_px=(300.0, 300.0),
            long_burst_chance=1.0,
            long_burst_px=(400.0, 400.0),
            burst_speed_px_s=500.0,
            pause_sec=(0.01, 0.01),
            desktop_margin_px=10,
            seed=14,
        ),
        desktop=Rect(0, 0, 400, 300),
        pet_w=50,
        pet_h=50,
        seed=14,
    )
    loco.resume_from_desktop(10.0, 0.0)
    x_lo, x_hi, y_lo, y_hi = loco._desktop_walk_bounds()
    for _ in range(200):
        pose = loco.step(0.05)
        assert x_lo <= pose.x <= x_hi
        assert y_lo <= pose.y <= y_hi


def test_burst_stays_inside_window_strip() -> None:
    win = WindowInfo(
        10,
        "Target",
        "Chrome_WidgetWin_1",
        Rect(100, 50, 500, 400),
        False,
        has_caption=True,
    )
    api = FakeApi([win])
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            burst_px=(250.0, 250.0),
            burst_speed_px_s=400.0,
            pause_sec=(0.01, 0.01),
            window_stay_sec=(1000, 1000),
            seed=15,
        ),
        api=api,
        pet_w=50,
        pet_h=50,
        seed=15,
    )
    loco._mode = LocomotionMode.WINDOW
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 10.0
    loco._begin_burst()
    loco._sync_to_window()
    max_off = float(400 - 50)  # strip width 400, pet 50
    for _ in range(150):
        loco.step(0.05)
        assert 0.0 <= loco._window_offset_x <= max_off


def test_desktop_mode_moves_xy() -> None:
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            burst_px=(80.0, 80.0),
            burst_speed_px_s=200.0,
            pause_sec=(0.05, 0.05),
            turn_on_pause_chance=1.0,
            desktop_turn_deg=(40.0, 90.0),
            desktop_margin_px=5,
            seed=21,
        ),
        desktop=Rect(0, 0, 800, 600),
        pet_w=40,
        pet_h=40,
        seed=21,
    )
    loco.resume_from_desktop(200.0, 200.0)
    xs: set[int] = set()
    ys: set[int] = set()
    for _ in range(300):
        pose = loco.step(0.05)
        xs.add(int(pose.x))
        ys.add(int(pose.y))
    assert loco.mode == LocomotionMode.DESKTOP
    assert len(xs) > 1
    assert len(ys) > 1


def test_window_mode_y_frozen() -> None:
    win = WindowInfo(
        10,
        "Target",
        "Chrome_WidgetWin_1",
        Rect(100, 80, 700, 480),
        False,
        has_caption=True,
    )
    api = FakeApi([win])
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            burst_px=(60.0, 60.0),
            burst_speed_px_s=200.0,
            pause_sec=(0.05, 0.05),
            window_stay_sec=(1000, 1000),
            seed=22,
        ),
        api=api,
        pet_w=40,
        pet_h=40,
        seed=22,
    )
    loco._mode = LocomotionMode.WINDOW
    loco._state = LocomotionState.ON_WINDOW
    loco._attached_hwnd = 10
    loco._window_offset_x = 20.0
    loco._heading_deg = 0.0
    loco._begin_burst()
    loco._sync_to_window()
    y0 = loco.pose.y
    xs: set[int] = set()
    for _ in range(120):
        pose = loco.step(0.05)
        xs.add(int(pose.x))
        assert pose.y == pytest.approx(y0, abs=0.5)
    assert len(xs) > 1


def test_desktop_turn_on_pause_within_range() -> None:
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            turn_on_pause_chance=1.0,
            desktop_turn_deg=(20.0, 90.0),
            seed=23,
        ),
        seed=23,
    )
    assert loco.mode == LocomotionMode.DESKTOP
    for _ in range(50):
        loco._heading_deg = 0.0
        loco._begin_pause()
        assert loco._last_pause_turned
        delta = abs(loco._last_pause_turn_delta)
        assert 20.0 <= delta <= 90.0


def test_desktop_bounce_all_four_edges() -> None:
    loco, _, _ = _make_loco(
        cfg=FakeWalk(
            burst_px=(500.0, 500.0),
            long_burst_chance=0.0,
            burst_speed_px_s=800.0,
            pause_sec=(0.01, 0.01),
            turn_on_pause_chance=0.0,
            desktop_margin_px=8,
            seed=24,
        ),
        desktop=Rect(0, 0, 300, 250),
        pet_w=40,
        pet_h=40,
        seed=24,
    )
    x_lo, x_hi, y_lo, y_hi = loco._desktop_walk_bounds()
    headings = (0.0, 90.0, 180.0, -90.0)
    starts = (
        (x_hi - 5, (y_lo + y_hi) / 2),
        ((x_lo + x_hi) / 2, y_hi - 5),
        (x_lo + 5, (y_lo + y_hi) / 2),
        ((x_lo + x_hi) / 2, y_lo + 5),
    )
    for heading, (sx, sy) in zip(headings, starts, strict=True):
        loco.resume_from_desktop(sx, sy)
        loco._heading_deg = heading
        loco._begin_burst()
        loco._burst_left = 400.0
        for _ in range(40):
            pose = loco.step(0.05)
            assert x_lo <= pose.x <= x_hi
            assert y_lo <= pose.y <= y_hi


def test_flight_decision_rates_and_exclude_same_window() -> None:
    win_a = _win(10, "A", rect=Rect(0, 0, 500, 400), has_caption=True)
    win_b = _win(20, "B", rect=Rect(100, 50, 600, 450), has_caption=True)
    api = FakeApi([win_a, win_b])
    loco, _, cfg = _make_loco(
        cfg=FakeWalk(
            fly_to_window_chance=0.4,
            leave_to_desktop_chance=0.3,
            seed=25,
        ),
        api=api,
        seed=25,
    )
    n = 200
    to_win = 0
    loco._mode = LocomotionMode.DESKTOP
    for _ in range(n):
        if loco._choose_flight_kind() == "to_window":
            to_win += 1
    assert abs(to_win / n - cfg.fly_to_window_chance) <= 0.10

    to_desk = 0
    loco._mode = LocomotionMode.WINDOW
    for _ in range(n):
        if loco._choose_flight_kind() == "to_desktop":
            to_desk += 1
    assert abs(to_desk / n - cfg.leave_to_desktop_chance) <= 0.10

    loco._mode = LocomotionMode.WINDOW
    loco._attached_hwnd = 10
    loco._prev_window_hwnd = 10
    for _ in range(40):
        pick = loco._pick_window_target(exclude_hwnds={10})
        assert pick is not None
        assert pick[0] != 10


def test_mode_changes_only_on_landing() -> None:
    win = _win(10, "A", rect=Rect(300, 100, 800, 500), has_caption=True)
    api = FakeApi([win])
    loco, _, _ = _make_loco(
        cfg=FakeWalk(fly_speed_px_s=900.0, seed=26),
        api=api,
        seed=26,
    )
    loco.resume_from_desktop(50.0, 200.0)
    assert loco.mode == LocomotionMode.DESKTOP
    loco._begin_takeoff(to_window=True)
    assert loco.mode == LocomotionMode.DESKTOP
    for _ in range(80):
        loco.step(0.05)
        if loco.state in {LocomotionState.TAKEOFF, LocomotionState.IN_FLIGHT}:
            assert loco.mode == LocomotionMode.DESKTOP
        if loco.state == LocomotionState.ON_WINDOW:
            break
    assert loco.state == LocomotionState.ON_WINDOW
    assert loco.mode == LocomotionMode.WINDOW

    loco._begin_takeoff(to_window=False)
    assert loco.mode == LocomotionMode.WINDOW
    for _ in range(80):
        loco.step(0.05)
        if loco.state in {
            LocomotionState.TAKEOFF,
            LocomotionState.IN_FLIGHT,
            LocomotionState.LANDING,
        }:
            assert loco.mode == LocomotionMode.WINDOW
        if loco.state == LocomotionState.ON_DESKTOP:
            break
    assert loco.state == LocomotionState.ON_DESKTOP
    assert loco.mode == LocomotionMode.DESKTOP


def test_sprite_angle_cardinals() -> None:
    loco, _, _ = _make_loco(seed=27)
    loco._state = LocomotionState.ON_DESKTOP
    loco._heading_deg = 0.0
    assert loco.sprite_angle_deg() == pytest.approx(0.0, abs=1.0)
    loco._heading_deg = 180.0
    assert abs(loco.sprite_angle_deg()) == pytest.approx(180.0, abs=1.0)
    loco._heading_deg = -90.0
    assert loco.sprite_angle_deg() == pytest.approx(-90.0, abs=1.0)
    loco._heading_deg = 90.0
    assert loco.sprite_angle_deg() == pytest.approx(90.0, abs=1.0)

    loco._state = LocomotionState.IN_FLIGHT
    loco._x, loco._y = 100.0, 100.0
    loco._flight_x1, loco._flight_y1 = 200.0, 100.0
    assert loco.sprite_angle_deg() == pytest.approx(0.0, abs=1.0)
    loco._flight_x1, loco._flight_y1 = 100.0, 0.0
    assert loco.sprite_angle_deg() == pytest.approx(-90.0, abs=1.0)
    loco._flight_x1, loco._flight_y1 = 100.0, 200.0
    assert loco.sprite_angle_deg() == pytest.approx(90.0, abs=1.0)
    loco._flight_x1, loco._flight_y1 = 0.0, 100.0
    assert abs(loco.sprite_angle_deg()) == pytest.approx(180.0, abs=1.0)
