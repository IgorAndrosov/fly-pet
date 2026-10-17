"""Выбор монитора и фильтр окон без GUI: два фейковых экрана."""

from __future__ import annotations

import logging

from fly_pet.locomotion import (
    Rect,
    filter_window_candidates,
    strip_lies_on_screen,
    title_bar_strip_from_metrics,
)
from fly_pet.screens import (
    MonitorDesc,
    ScreenDesc,
    ScreenSession,
    format_monitors_log,
    format_screen_log,
    icon_on_screen,
    pick_screen_rect,
    select_screen,
)

# ('\\\\.\\DISPLAY1', (0,0,1920,1080), True), ('\\\\.\\DISPLAY2', (1920,0,1920,1080), False)
FAKE_SCREENS = [
    ("\\\\.\\DISPLAY1", (0, 0, 1920, 1080), True),
    ("\\\\.\\DISPLAY2", (1920, 0, 1920, 1080), False),
]


def _win(hwnd: int, rect: Rect, title: str = "Ok"):
    from fly_pet.locomotion import WindowInfo

    return WindowInfo(
        hwnd,
        title,
        "Chrome_WidgetWin_1",
        rect,
        False,
        has_caption=True,
    )


def _strip(rect: Rect) -> Rect:
    strip = title_bar_strip_from_metrics(
        rect, has_caption=True, caption_plus_frame=31, client_top_screen=None
    )
    assert strip is not None
    return strip


def test_pick_screen_rect_monitor_modes() -> None:
    first = pick_screen_rect(1, FAKE_SCREENS)
    second = pick_screen_rect(2, FAKE_SCREENS)
    primary = pick_screen_rect("primary", FAKE_SCREENS)
    union = pick_screen_rect("all", FAKE_SCREENS)
    assert first == Rect(0, 0, 1920, 1080)
    assert second == Rect(1920, 0, 3840, 1080)
    assert primary == first
    assert union == Rect(0, 0, 3840, 1080)


def test_screen_log_uses_available_geometry() -> None:
    screens = [
        ("\\\\.\\DISPLAY1", (0, 0, 1920, 1080), True, (0, 0, 1920, 1032)),
        ("\\\\.\\DISPLAY2", (1920, 0, 1920, 1080), False, (1920, 0, 1920, 1080)),
    ]
    choice = select_screen(1, screens)
    assert choice.walk == Rect(0, 0, 1920, 1032)
    assert choice.match == "name"
    assert format_screen_log(choice) == (
        "монитор 1: \\\\.\\DISPLAY1 (по имени Qt), "
        "область ходьбы: (0,0)-(1920,1032)"
    )


def test_missing_monitor_falls_back_once(caplog) -> None:
    session = ScreenSession(3)
    with caplog.at_level(logging.WARNING, logger="fly_pet"):
        first = session.resolve(FAKE_SCREENS)
        second = session.resolve(FAKE_SCREENS)
    assert first.fallback is True
    assert first.walk == Rect(0, 0, 1920, 1080)
    assert second.walk == first.walk
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "DISPLAY3" in warnings[0].message


def test_window_on_other_monitor_is_not_candidate() -> None:
    screen = Rect(0, 0, 1920, 1080)
    on_first = _win(1, Rect(80, 40, 900, 700), "One")
    on_second = _win(2, Rect(2000, 40, 2800, 700), "Two")
    # Полоса заголовка частично на первом экране, центр на втором.
    straddling = _win(3, Rect(1500, 40, 2600, 700), "Edge")
    windows = [on_first, on_second, straddling]
    strips = {info.hwnd: _strip(info.rect) for info in windows}
    assert not strip_lies_on_screen(strips[2], screen)
    assert not strip_lies_on_screen(strips[3], screen)
    assert strips[3].left < screen.right < strips[3].right
    got = filter_window_candidates(
        windows,
        min_width=320,
        min_height=160,
        ignore_titles=[],
        screen=screen,
        strips=strips,
    )
    assert [info.hwnd for info in got] == [1]


def test_strip_center_inside_but_overlap_below_60_percent() -> None:
    screen = Rect(0, 0, 1920, 1080)
    # Ширина 1000, на экране 550 px (55%), центр x=50 внутри.
    thin = Rect(-450, 10, 550, 40)
    assert (thin.left + thin.right) / 2 == 50
    assert not strip_lies_on_screen(thin, screen)
    enough = Rect(-400, 10, 600, 40)
    assert strip_lies_on_screen(enough, screen)


EDID_SCREENS = [
    ScreenDesc(
        "C30SK PRO",
        Rect(0, 0, 2560, 1080),
        Rect(0, 0, 2560, 1032),
        True,
    ),
    ScreenDesc(
        "DELL U2412",
        Rect(2560, 0, 4480, 1080),
        Rect(2560, 0, 4480, 1080),
        False,
    ),
]
WIN32_MONITORS = [
    MonitorDesc(r"\\.\DISPLAY1", Rect(0, 0, 2560, 1080), Rect(0, 0, 2560, 1032), True),
    MonitorDesc(r"\\.\DISPLAY2", Rect(2560, 0, 4480, 1080), Rect(2560, 0, 4480, 1080), False),
]


def test_edid_names_match_win32_rect() -> None:
    first = select_screen(1, EDID_SCREENS, WIN32_MONITORS)
    second = select_screen(2, EDID_SCREENS, WIN32_MONITORS)
    primary = select_screen("primary", EDID_SCREENS, WIN32_MONITORS)
    union = select_screen("all", EDID_SCREENS, WIN32_MONITORS)
    assert first.fallback is False
    assert first.match == "win32"
    assert first.name == "C30SK PRO"
    assert first.walk == Rect(0, 0, 2560, 1032)
    assert format_screen_log(first) == (
        "монитор 1: C30SK PRO (по Win32 \\\\.\\DISPLAY1), "
        "область ходьбы: (0,0)-(2560,1032)"
    )
    assert second.fallback is False
    assert second.name == "DELL U2412"
    assert second.walk == Rect(2560, 0, 4480, 1080)
    assert primary.name == "C30SK PRO"
    assert primary.primary is True
    assert primary.fallback is False
    assert union.walk == Rect(0, 0, 4480, 1080)
    assert union.match == "all"
    assert format_monitors_log(WIN32_MONITORS) == (
        "мониторы: \\\\.\\DISPLAY1 2560x1080 at (0,0) work=(0,0,2560,1032) primary; "
        "\\\\.\\DISPLAY2 1920x1080 at (2560,0) work=(2560,0,4480,1080)"
    )


def test_no_name_or_rect_falls_back_to_primary(caplog) -> None:
    qt = [
        ScreenDesc("C30SK PRO", Rect(0, 0, 2560, 1080), Rect(0, 0, 2560, 1032), True),
        ScreenDesc("DELL U2412", Rect(2560, 0, 4480, 1080), Rect(2560, 0, 4480, 1080), False),
    ]
    win32 = [
        MonitorDesc(r"\\.\DISPLAY1", Rect(9000, 9000, 10000, 10000), Rect(9000, 9000, 10000, 9900), True),
    ]
    session = ScreenSession(1)
    with caplog.at_level(logging.WARNING, logger="fly_pet"):
        choice = session.resolve(qt, win32)
    assert choice.fallback is True
    assert choice.name == "C30SK PRO"
    assert choice.primary is True
    assert choice.walk == Rect(0, 0, 2560, 1032)
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "DISPLAY1" in warnings[0]


def test_dpi_scale_matches_and_warns(caplog) -> None:
    qt = [
        ScreenDesc(
            "C30SK PRO",
            Rect(0, 0, 1280, 720),
            Rect(0, 0, 1280, 680),
            True,
            device_pixel_ratio=2.0,
        )
    ]
    win32 = [
        MonitorDesc(
            r"\\.\DISPLAY1",
            Rect(0, 0, 2560, 1440),
            Rect(0, 0, 2560, 1360),
            True,
        )
    ]
    session = ScreenSession(1)
    with caplog.at_level(logging.WARNING, logger="fly_pet"):
        choice = session.resolve(qt, win32)
    assert choice.fallback is False
    assert choice.match == "win32"
    assert choice.name == "C30SK PRO"
    assert choice.walk == Rect(0, 0, 1280, 680)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    text = warnings[0].message
    assert "1280x720" in text
    assert "2560x1440" in text
    assert r"\\.\DISPLAY1" in text or "DISPLAY1" in text


def test_shifted_rect_matches_by_center() -> None:
    qt = [
        ScreenDesc("C30SK PRO", Rect(0, 0, 2560, 1080), Rect(0, 0, 2560, 1032), True),
    ]
    win32 = [
        MonitorDesc(r"\\.\DISPLAY1", Rect(10, 10, 2570, 1090), Rect(10, 10, 2570, 1042), True),
    ]
    choice = select_screen(1, qt, win32)
    assert choice.fallback is False
    assert choice.match == "win32"
    assert choice.name == "C30SK PRO"
    assert choice.dpi_note is not None


def test_icon_on_second_monitor_is_rejected() -> None:
    screen = Rect(0, 0, 1920, 1080)
    on_second = {"screen_x": 2000, "screen_y": 80, "cell": [76, 100]}
    on_first = {"screen_x": 120, "screen_y": 80, "cell": [76, 100]}
    assert icon_on_screen(on_first, screen) is True
    assert icon_on_screen(on_second, screen) is False
