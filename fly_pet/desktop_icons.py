"""Иконки рабочего стола: путь Desktop из реестра и экранные координаты через ListView."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger("fly_pet")

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
LVM_GETITEMCOUNT = 0x1004
LVM_GETITEMPOSITION = 0x1010
LVM_GETITEMSPACING = 0x1033
LVM_GETITEMTEXTW = 0x1073
LVIF_TEXT = 0x0001
SHCNE_CREATE = 0x00000002
SHCNE_UPDATEITEM = 0x00002000
SHCNF_PATHW = 0x0005


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _LVITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", ctypes.c_uint),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("stateMask", ctypes.c_uint),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_ssize_t),
        ("iIndent", ctypes.c_int),
        ("iGroupId", ctypes.c_int),
        ("cColumns", ctypes.c_uint),
        ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p),
        ("iGroup", ctypes.c_int),
    ]


def ensure_dpi_aware() -> str:
    """PER_MONITOR_AWARE до координатных вызовов (иначе DPI виртуализирует)."""
    if sys.platform != "win32":
        return "none"
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return "system"
        except Exception:
            return "none"


def user_desktop_path() -> Path:
    """Личный рабочий стол из ``User Shell Folders\\Desktop`` (с expandvars)."""
    if sys.platform != "win32":
        return Path.home() / "Desktop"
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        raw, _ = winreg.QueryValueEx(key, "Desktop")
    return Path(os.path.expandvars(str(raw))).expanduser().resolve()


def notify_shell_create(path: Path) -> None:
    """Сообщить explorer'у о новом файле."""
    if sys.platform != "win32":
        return
    shell32 = ctypes.windll.shell32
    shell32.SHChangeNotify(SHCNE_CREATE, SHCNF_PATHW, str(path), None)
    shell32.SHChangeNotify(SHCNE_UPDATEITEM, SHCNF_PATHW, str(path), None)


def _desktop_listview() -> int:
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    ]
    progman = user32.FindWindowW("Progman", None)
    defview = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)
    if not defview:
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd: int, _lparam: int) -> bool:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value == "WorkerW":
                dv = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
                if dv:
                    found.append(int(dv))
            return True

        user32.EnumWindows(_enum, 0)
        defview = found[0] if found else 0
    if not defview:
        raise RuntimeError("SHELLDLL_DefView не найден")
    lv = int(user32.FindWindowExW(defview, 0, "SysListView32", None) or 0)
    if not lv:
        raise RuntimeError("SysListView32 не найден")
    return lv


def list_desktop_icons() -> list[dict]:
    """Список иконок: name, screen_x/y (клетка), cell [w,h]."""
    if sys.platform != "win32":
        return []
    ensure_dpi_aware()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    lv = _desktop_listview()
    count = int(user32.SendMessageW(lv, LVM_GETITEMCOUNT, 0, 0) or 0)
    spacing = int(user32.SendMessageW(lv, LVM_GETITEMSPACING, 0, 0) or 0)
    cell_w, cell_h = spacing & 0xFFFF, spacing >> 16

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(lv, ctypes.byref(pid))
    access = (
        PROCESS_VM_OPERATION
        | PROCESS_VM_READ
        | PROCESS_VM_WRITE
        | PROCESS_QUERY_INFORMATION
    )
    handle = kernel32.OpenProcess(access, False, pid.value)
    if not handle:
        raise RuntimeError(f"OpenProcess не удался ({ctypes.get_last_error()})")

    item_size = ctypes.sizeof(_LVITEMW)
    text_size = 260 * 2
    remote = kernel32.VirtualAllocEx(
        handle, None, item_size + text_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    )
    if not remote:
        kernel32.CloseHandle(handle)
        raise RuntimeError("VirtualAllocEx не удался")
    remote_text = remote + item_size
    icons: list[dict] = []
    try:
        for i in range(count):
            pt = _POINT()
            kernel32.WriteProcessMemory(
                handle, remote, ctypes.byref(pt), ctypes.sizeof(pt), None
            )
            user32.SendMessageW(lv, LVM_GETITEMPOSITION, i, remote)
            kernel32.ReadProcessMemory(
                handle, remote, ctypes.byref(pt), ctypes.sizeof(pt), None
            )
            item = _LVITEMW(
                mask=LVIF_TEXT,
                iItem=i,
                iSubItem=0,
                pszText=remote_text,
                cchTextMax=260,
            )
            kernel32.WriteProcessMemory(
                handle, remote, ctypes.byref(item), item_size, None
            )
            got = user32.SendMessageW(lv, LVM_GETITEMTEXTW, i, remote)
            buf = ctypes.create_unicode_buffer(260)
            kernel32.ReadProcessMemory(handle, remote_text, buf, text_size, None)
            screen = _POINT(pt.x, pt.y)
            user32.ClientToScreen(lv, ctypes.byref(screen))
            icons.append(
                {
                    "index": i,
                    "name": buf.value if got else "",
                    "client_x": int(pt.x),
                    "client_y": int(pt.y),
                    "screen_x": int(screen.x),
                    "screen_y": int(screen.y),
                    "cell": [int(cell_w), int(cell_h)],
                }
            )
    finally:
        kernel32.VirtualFreeEx(handle, remote, 0, MEM_RELEASE)
        kernel32.CloseHandle(handle)
    return icons


def icon_display_names(filename: str) -> set[str]:
    """Возможные подписи иконки в ListView (с расширением и без)."""
    path = Path(filename)
    names = {path.name, path.stem}
    return {n.casefold() for n in names if n}


def find_icon_by_filename(filename: str) -> dict | None:
    """Найти иконку по имени файла; None если нет."""
    wanted = icon_display_names(filename)
    for icon in list_desktop_icons():
        name = str(icon.get("name") or "")
        if name.casefold() in wanted:
            return icon
    return None


def wait_for_icon(
    filename: str,
    *,
    timeout_sec: float = 5.0,
    poll_sec: float = 0.2,
) -> dict | None:
    """Ждать появления иконки до timeout (блокирующе)."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            found = find_icon_by_filename(filename)
        except Exception as exc:  # noqa: BLE001
            logger.debug("поиск иконки: %s", exc)
            found = None
        if found is not None:
            return found
        time.sleep(poll_sec)
    return None


def icon_center_screen(icon: dict) -> tuple[int, int]:
    """Центр клетки иконки в экранных координатах."""
    cell = icon.get("cell") or [76, 100]
    cw = int(cell[0]) if cell else 76
    ch = int(cell[1]) if cell else 100
    if cw <= 0:
        cw = 76
    if ch <= 0:
        ch = 100
    cx = int(icon["screen_x"]) + cw // 2
    cy = int(icon["screen_y"]) + ch // 2
    return cx, cy
