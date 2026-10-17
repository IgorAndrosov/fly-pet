"""Оверлей клетки иконки: фон + маскированные укусы-полукруги."""

from __future__ import annotations

import ctypes
import logging
import math
import random
import sys
from dataclasses import dataclass
from typing import Sequence

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger("fly_pet")

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOP = 0
HWND_TOPMOST = -1

_ALPHA_THRESH = 28
_DIFF_THRESH = 18
_BITE_R = (8, 14)
_TARGET_BITES = (15, 30)


@dataclass(frozen=True)
class Bite:
    """Укус в координатах клетки иконки (локальных)."""

    x: int
    y: int
    r: int
    angle_deg: float  # направление «внутрь» (куда смотрит полудиск)


def grab_screen() -> QPixmap:
    """Снимок всего виртуального экрана (grabWindow(0))."""
    screen = QApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    return screen.grabWindow(0)


def icon_cell_rect(icon: dict) -> QRect:
    """Прямоугольник клетки иконки в экранных координатах."""
    cell = icon.get("cell") or [76, 100]
    cw = max(1, int(cell[0]) if cell else 76)
    ch = max(1, int(cell[1]) if cell else 100)
    return QRect(int(icon["screen_x"]), int(icon["screen_y"]), cw, ch)


def grab_cell(rect: QRect) -> QImage:
    """Снимок клетки: полный grabWindow(0) + crop (иконки стола так видны)."""
    screen = QApplication.primaryScreen()
    if screen is None or rect.width() <= 0 or rect.height() <= 0:
        return QImage()
    full = screen.grabWindow(0)
    if full.isNull():
        return QImage()
    # Координаты ListView/ClientToScreen и grab — в одних px при dpr=1;
    # при dpr>1 Qt иногда отдаёт физический буфер.
    dpr = float(screen.devicePixelRatio())
    if dpr > 1.01 and full.width() > screen.virtualGeometry().width() + 2:
        x = int(rect.x() * dpr)
        y = int(rect.y() * dpr)
        w = max(1, int(rect.width() * dpr))
        h = max(1, int(rect.height() * dpr))
        img = full.copy(x, y, w, h).toImage().convertToFormat(QImage.Format.Format_ARGB32)
        if img.width() != rect.width() or img.height() != rect.height():
            img = img.scaled(
                rect.width(),
                rect.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return img
    return (
        full.copy(rect.x(), rect.y(), rect.width(), rect.height())
        .toImage()
        .convertToFormat(QImage.Format.Format_ARGB32)
    )


def grab_icon_cell(icon: dict) -> QImage:
    """Снимок клетки иконки по экранному прямоугольнику."""
    return grab_cell(icon_cell_rect(icon))


def extract_icon_layers_from_cells(
    bg: QImage,
    fg_raw: QImage,
) -> tuple[QImage, QImage]:
    """Фон клетки + иконка (diff after−before)."""
    if bg.isNull() or fg_raw.isNull():
        return bg, QImage()
    w = min(bg.width(), fg_raw.width())
    h = min(bg.height(), fg_raw.height())
    if w <= 0 or h <= 0:
        return bg, QImage()
    icon = QImage(w, h, QImage.Format.Format_ARGB32)
    icon.fill(Qt.GlobalColor.transparent)
    for y in range(h):
        for x in range(w):
            c0 = bg.pixelColor(x, y)
            c1 = fg_raw.pixelColor(x, y)
            if (
                abs(c0.red() - c1.red()) > _DIFF_THRESH
                or abs(c0.green() - c1.green()) > _DIFF_THRESH
                or abs(c0.blue() - c1.blue()) > _DIFF_THRESH
            ):
                icon.setPixelColor(x, y, c1)
    return bg.copy(0, 0, w, h), icon


def count_opaque(icon: QImage) -> int:
    """Число непрозрачных пикселей."""
    if icon.isNull():
        return 0
    n = 0
    for y in range(icon.height()):
        for x in range(icon.width()):
            if icon.pixelColor(x, y).alpha() >= _ALPHA_THRESH:
                n += 1
    return n


def extract_icon_layers(
    before: QPixmap,
    after: QPixmap,
    cell: QRect,
) -> tuple[QImage, QImage]:
    """Вырезка фона (до) и иконки (diff) из полных снимков экрана."""
    bg = grab_cell(cell) if before.isNull() else _crop_from_full(before, cell)
    # after полный снимок — кроп; для совместимости тестов/старого API.
    fg_raw = _crop_from_full(after, cell) if not after.isNull() else grab_cell(cell)
    return extract_icon_layers_from_cells(bg, fg_raw)


def _crop_from_full(pm: QPixmap, rect: QRect) -> QImage:
    if pm.isNull() or rect.width() <= 0 or rect.height() <= 0:
        return QImage()
    screen = QApplication.primaryScreen()
    origin = QPoint(0, 0)
    dpr = 1.0
    if screen is not None:
        geo = screen.virtualGeometry()
        origin = QPoint(geo.x(), geo.y())
        dpr = float(screen.devicePixelRatio())
    local = QRect(
        int((rect.x() - origin.x()) * dpr),
        int((rect.y() - origin.y()) * dpr),
        max(1, int(rect.width() * dpr)),
        max(1, int(rect.height() * dpr)),
    )
    local = local.intersected(QRect(0, 0, pm.width(), pm.height()))
    if local.isEmpty():
        return QImage()
    img = pm.copy(local).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    if dpr > 1.01 and (
        abs(img.width() - rect.width()) > 2 or abs(img.height() - rect.height()) > 2
    ):
        img = img.scaled(
            rect.width(),
            rect.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return img


def opaque_bbox(icon: QImage) -> QRect | None:
    """BBox непрозрачных пикселей иконки; None если пусто."""
    if icon.isNull():
        return None
    min_x, min_y = icon.width(), icon.height()
    max_x, max_y = -1, -1
    for y in range(icon.height()):
        for x in range(icon.width()):
            if icon.pixelColor(x, y).alpha() >= _ALPHA_THRESH:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return None
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _opaque_set(icon: QImage) -> set[tuple[int, int]]:
    pts: set[tuple[int, int]] = set()
    for y in range(icon.height()):
        for x in range(icon.width()):
            if icon.pixelColor(x, y).alpha() >= _ALPHA_THRESH:
                pts.add((x, y))
    return pts


def _contour_points(opaque: set[tuple[int, int]]) -> list[tuple[int, int]]:
    pts: list[tuple[int, int]] = []
    for x, y in opaque:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if (x + dx, y + dy) not in opaque:
                pts.append((x, y))
                break
    return pts


def _order_clockwise(
    pts: Sequence[tuple[int, int]], cx: float, cy: float
) -> list[tuple[int, int]]:
    return sorted(pts, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def _subsample(pts: Sequence[tuple[int, int]], step: float) -> list[tuple[int, int]]:
    if not pts:
        return []
    out = [pts[0]]
    for p in pts[1:]:
        ox, oy = out[-1]
        if math.hypot(p[0] - ox, p[1] - oy) >= step:
            out.append(p)
    return out


def _zigzag(
    opaque: set[tuple[int, int]],
    box: QRect,
    step: int,
) -> list[tuple[int, int]]:
    pts: list[tuple[int, int]] = []
    y = box.top() + step
    ltr = True
    while y <= box.bottom():
        row = [x for x in range(box.left(), box.right() + 1, step) if (x, y) in opaque]
        if not row:
            # ближайшие непрозрачные в полосе ±step/2
            band: list[tuple[int, int]] = []
            for yy in range(max(box.top(), y - step // 2), min(box.bottom(), y + step // 2) + 1):
                for x in range(box.left(), box.right() + 1, max(1, step // 2)):
                    if (x, yy) in opaque:
                        band.append((x, yy))
            row_pts = _subsample(band, float(step))
        else:
            row_pts = [(x, y) for x in row]
        if not ltr:
            row_pts = list(reversed(row_pts))
        pts.extend(row_pts)
        y += step
        ltr = not ltr
    return pts


def _inward_angle(x: int, y: int, cx: float, cy: float) -> float:
    return math.degrees(math.atan2(cy - y, cx - x))


def plan_bites(icon: QImage, *, radius: int | None = None) -> list[Bite]:
    """Маршрут: периметр → сетка внутрь; целимся покрыть почти всё (15–30 укусов)."""
    opaque = _opaque_set(icon)
    box = opaque_bbox(icon)
    if box is None or not opaque:
        return []
    r = int(radius) if radius is not None else 11
    r = max(6, min(16, r))
    cx = box.center().x()
    cy = box.center().y()
    lo, hi = _TARGET_BITES

    contour = _order_clockwise(_contour_points(opaque), cx, cy)
    # Шаг ≈ 0.75r, чтобы полудиски перекрывались и не оставляли целых кусков.
    step = max(4, int(round(r * 0.75)))
    peri = _subsample(contour, float(step))
    grid = _zigzag(opaque, box, step)
    taken: list[tuple[int, int]] = []
    for p in list(peri) + list(grid):
        if all(math.hypot(p[0] - t[0], p[1] - t[1]) >= step * 0.9 for t in taken):
            taken.append(p)

    if len(taken) > hi:
        # равномерно проредить, но оставить hi точек
        idxs = [int(round(i * (len(taken) - 1) / (hi - 1))) for i in range(hi)]
        taken = [taken[i] for i in idxs]
    elif 0 < len(taken) < lo:
        step2 = max(3, step // 2)
        denser = _zigzag(opaque, box, step2)
        for p in denser:
            if all(math.hypot(p[0] - t[0], p[1] - t[1]) >= step2 * 0.85 for t in taken):
                taken.append(p)
            if len(taken) >= hi:
                break

    # Симуляция покрытия: если остаётся много — добить центрами непрозрачных зон.
    sim = make_full_mask(icon)
    bites: list[Bite] = []
    for x, y in taken:
        br = random.randint(*_BITE_R)
        bite = Bite(x=x, y=y, r=br, angle_deg=_inward_angle(x, y, cx, cy))
        apply_bite(sim, bite)
        bites.append(bite)

    while remaining_ratio(sim, icon) > 0.08 and len(bites) < hi:
        # точка с максимальной «массой» непрозрачных соседей ещё видимых
        best = None
        best_score = -1
        for y in range(box.top(), box.bottom() + 1, max(1, step // 2)):
            for x in range(box.left(), box.right() + 1, max(1, step // 2)):
                if (x, y) not in opaque:
                    continue
                if sim.pixelColor(x, y).alpha() < _ALPHA_THRESH:
                    continue
                score = 0
                for dy in range(-r, r + 1, 2):
                    for dx in range(-r, r + 1, 2):
                        xx, yy = x + dx, y + dy
                        if (xx, yy) in opaque and sim.pixelColor(xx, yy).alpha() >= _ALPHA_THRESH:
                            score += 1
                if score > best_score:
                    best_score = score
                    best = (x, y)
        if best is None:
            break
        br = random.randint(*_BITE_R)
        bite = Bite(
            x=best[0],
            y=best[1],
            r=br,
            angle_deg=_inward_angle(best[0], best[1], cx, cy),
        )
        apply_bite(sim, bite)
        bites.append(bite)

    return bites


def make_full_mask(icon: QImage) -> QImage:
    """Маска видимости: белый = ещё видно, прозрачный = съедено."""
    w, h = icon.width(), icon.height()
    mask = QImage(w, h, QImage.Format.Format_ARGB32)
    mask.fill(Qt.GlobalColor.transparent)
    white = QColor(255, 255, 255, 255)
    for y in range(h):
        for x in range(w):
            if icon.pixelColor(x, y).alpha() >= _ALPHA_THRESH:
                mask.setPixelColor(x, y, white)
    return mask


def apply_bite(mask: QImage, bite: Bite) -> None:
    """Вырезать полудиск (+ небольшой запас) из маски."""
    if mask.isNull():
        return
    painter = QPainter(mask)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 255))
    rect = QRect(bite.x - bite.r, bite.y - bite.r, bite.r * 2, bite.r * 2)
    # Qt drawPie: start/span в 1/16°, 0° = 3 часа, против часовой; полудиск «внутрь».
    # 200° вместо 180 — чуть больше покрытие, визуально всё ещё «укус».
    qt_start = int((-bite.angle_deg - 100) * 16)
    painter.drawPie(rect, qt_start, 200 * 16)
    painter.end()


def remaining_ratio(mask: QImage, icon: QImage) -> float:
    """Доля ещё видимых непрозрачных пикселей иконки (0…1)."""
    if mask.isNull() or icon.isNull():
        return 0.0
    total = 0
    left = 0
    w = min(mask.width(), icon.width())
    h = min(mask.height(), icon.height())
    for y in range(h):
        for x in range(w):
            if icon.pixelColor(x, y).alpha() < _ALPHA_THRESH:
                continue
            total += 1
            if mask.pixelColor(x, y).alpha() >= _ALPHA_THRESH:
                left += 1
    if total <= 0:
        return 0.0
    return left / total


def compose_frame(bg: QImage, icon: QImage, mask: QImage) -> QPixmap:
    """Фон клетки + иконка, обрезанная маской укусов."""
    w = min(bg.width(), icon.width(), mask.width())
    h = min(bg.height(), icon.height(), mask.height())
    out = QImage(w, h, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawImage(0, 0, bg)
    masked = QImage(w, h, QImage.Format.Format_ARGB32)
    masked.fill(Qt.GlobalColor.transparent)
    for y in range(h):
        for x in range(w):
            if mask.pixelColor(x, y).alpha() < _ALPHA_THRESH:
                continue
            c = icon.pixelColor(x, y)
            if c.alpha() < _ALPHA_THRESH:
                continue
            masked.setPixelColor(x, y, c)
    painter.drawImage(0, 0, masked)
    painter.end()
    return QPixmap.fromImage(out)


class IconEatOverlay(QWidget):
    """Прозрачное для мыши окно ровно по клетке иконки."""

    def __init__(
        self,
        cell: QRect,
        bg: QImage,
        icon: QImage,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cell = QRect(cell)
        self._bg = bg
        self._icon = icon
        self._mask = make_full_mask(icon)
        self._frame = compose_frame(bg, icon, self._mask)

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(self._cell)

    def mask_image(self) -> QImage:
        return self._mask

    def icon_image(self) -> QImage:
        return self._icon

    def apply_bite_and_repaint(self, bite: Bite) -> float:
        """Применить укус; вернуть оставшуюся долю (0…1)."""
        apply_bite(self._mask, bite)
        self._frame = compose_frame(self._bg, self._icon, self._mask)
        self.update()
        return remaining_ratio(self._mask, self._icon)

    def clear_mask(self) -> None:
        """Добить хвост маски: иконка на оверлее полностью съедена."""
        if self._mask.isNull():
            return
        self._mask.fill(Qt.GlobalColor.transparent)
        self._frame = compose_frame(self._bg, self._icon, self._mask)
        self.update()

    def show_click_through(self) -> None:
        self.show()
        self._apply_exstyles()
        self.raise_()

    def _apply_exstyles(self) -> None:
        if sys.platform != "win32":
            return
        hwnd = int(self.winId() or 0)
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
            get_long.restype = ctypes.c_longlong
            set_long.restype = ctypes.c_longlong
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
        else:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
        style = int(get_long(hwnd, GWL_EXSTYLE))
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
        set_long(hwnd, GWL_EXSTYLE, style)

    def raise_below(self, above_hwnd: int, *, topmost: bool = False) -> None:
        """Оверлей под ``above_hwnd``; оба могут быть topmost."""
        if sys.platform != "win32":
            self.raise_()
            return
        user32 = ctypes.windll.user32
        overlay = int(self.winId() or 0)
        if not overlay:
            return
        insert = HWND_TOPMOST if topmost else HWND_TOP
        user32.SetWindowPos(
            overlay, insert, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        )
        if above_hwnd:
            user32.SetWindowPos(
                above_hwnd,
                insert,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        if self._frame.isNull():
            return
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._frame)


def bite_interval_sec(n_bites: int) -> float:
    """Интервал между укусами: цель 6–10 с суммарно, clamp 0.25–0.4."""
    n = max(1, int(n_bites))
    total = random.uniform(6.0, 10.0)
    return max(0.25, min(0.4, total / n))
