"""Облачко реплики рядом с питомцем."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QPolygon
from PyQt6.QtWidgets import QApplication, QWidget

if TYPE_CHECKING:
    from fly_pet.config import BubbleConfig

_TAIL_W = 14
_TAIL_H = 10
_RADIUS = 10
_BG = QColor(32, 32, 36, 210)
_FG = QColor(240, 240, 242)


class SpeechBubble(QWidget):
    """Frameless облачко с текстом; не поверх всех окон, Qt.Tool."""

    def __init__(self, config: BubbleConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._text = ""
        self._anchor: QWidget | None = None
        self._tail_bottom = True  # хвост вниз (облачко сверху питомца)

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        font = QFont(self.font())
        font.setPointSize(config.font_pt)
        self.setFont(font)
        self.hide()

    def say(self, text: str, ttl_ms: int | None = None) -> None:
        """Показать реплику; пустой текст — скрыть и выйти."""
        text = (text or "").strip()
        if not text:
            self._hide_timer.stop()
            self.hide()
            self._text = ""
            return

        self._text = text
        self._relayout()
        if self._anchor is not None:
            self.place_near(self._anchor)
        self.show()
        self.raise_()

        if ttl_ms is None:
            ttl_ms = int(round(self._config.ttl_sec * 1000))
        ttl_ms = max(1, int(ttl_ms))
        self._hide_timer.start(ttl_ms)

    def attach_to(self, window: QWidget) -> None:
        """Привязать облачко к окну питомца."""
        self._anchor = window

    def place_near(self, anchor: QWidget) -> None:
        """Поставить облачко рядом с якорем внутри availableGeometry."""
        self._anchor = anchor
        if not self._text:
            return
        self._relayout()

        screen = QApplication.primaryScreen()
        if screen is None:
            self.move(anchor.pos())
            return
        geo = screen.availableGeometry()
        aw = anchor.frameGeometry()
        bw, bh = self.width(), self.height()
        gap = self._config.offset_px
        # Предпочтительно сверху по центру
        candidates: list[tuple[QPoint, bool]] = [
            (QPoint(aw.center().x() - bw // 2, aw.top() - bh - gap), True),
            (QPoint(aw.center().x() - bw // 2, aw.bottom() + gap), False),
            (QPoint(aw.left() - bw - gap, aw.center().y() - bh // 2), True),
            (QPoint(aw.right() + gap, aw.center().y() - bh // 2), True),
        ]

        chosen_pos = candidates[0][0]
        chosen_tail = candidates[0][1]
        for pos, tail_bottom in candidates:
            rect = QRect(pos, QSize(bw, bh))
            if geo.contains(rect):
                chosen_pos, chosen_tail = pos, tail_bottom
                break
        else:
            # Зажать в availableGeometry
            x = min(max(chosen_pos.x(), geo.left()), geo.right() - bw)
            y = min(max(chosen_pos.y(), geo.top()), geo.bottom() - bh)
            chosen_pos = QPoint(x, y)
            chosen_tail = chosen_pos.y() + bh <= aw.top()

        self._tail_bottom = chosen_tail
        self.move(chosen_pos)

    def follow_anchor(self) -> None:
        """Перепозиционировать, если облачко видно и якорь задан."""
        if self.isVisible() and self._anchor is not None and self._text:
            self.place_near(self._anchor)

    def _text_pad(self) -> int:
        # offset_px — зазор до питомца; для текста берём его же, но не меньше 8
        return max(8, min(self._config.offset_px, 12))

    def _relayout(self) -> None:
        pad = self._text_pad()
        max_w = self._config.max_width
        fm = QFontMetrics(self.font())
        text_max = max(40, max_w - 2 * pad)
        br = fm.boundingRect(0, 0, text_max, 10_000, int(Qt.TextFlag.TextWordWrap), self._text)
        w = min(max_w, max(br.width() + 2 * pad, 48))
        h = br.height() + 2 * pad + _TAIL_H
        self.resize(w, h)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pad = self._text_pad()
        body = QRect(0, 0, self.width(), self.height() - _TAIL_H)
        if not self._tail_bottom:
            body.moveTop(_TAIL_H)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_BG)
        painter.drawRoundedRect(body, _RADIUS, _RADIUS)

        # Хвостик к питомцу (отдельно от united — на offscreen path.united падает)
        cx = body.center().x()
        if self._tail_bottom:
            tip = QPoint(int(cx), self.height() - 1)
            left = QPoint(int(cx - _TAIL_W // 2), body.bottom())
            right = QPoint(int(cx + _TAIL_W // 2), body.bottom())
        else:
            tip = QPoint(int(cx), 0)
            left = QPoint(int(cx - _TAIL_W // 2), body.top())
            right = QPoint(int(cx + _TAIL_W // 2), body.top())
        painter.drawPolygon(QPolygon([left, tip, right]))

        painter.setPen(_FG)
        text_rect = body.adjusted(pad, pad, -pad, -pad)
        painter.drawText(
            text_rect,
            int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter),
            self._text,
        )
