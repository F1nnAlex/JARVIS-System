"""Native-drawn visuals (QPainter): the arc reactor, the backdrop, the
panel frames and the CPU sparkline."""

from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from . import theme

TAU = math.pi * 2

PALETTE = {
    "standby": ((63, 216, 255), (63, 216, 255)),
    "listening": ((120, 230, 255), (217, 247, 255)),
    "transcribing": ((63, 216, 255), (255, 182, 72)),
    "thinking": ((63, 216, 255), (255, 182, 72)),
    "speaking": ((90, 224, 255), (217, 247, 255)),
    "error": ((255, 77, 90), (255, 140, 120)),
    "boot": ((63, 216, 255), (63, 216, 255)),
    "muted": ((70, 120, 150), (110, 150, 170)),
}


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _c(rgb, alpha: float) -> QColor:
    return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), max(0, min(255, int(alpha * 255))))


class Reactor(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setCursor(Qt.PointingHandCursor)
        self.state = "boot"
        self.main, self.accent = PALETTE["boot"]
        self.level = 0.0
        self.spectrum = [0.0] * 96
        self.power = 0.0
        self.spin = 0.0
        self.spin_speed = 0.2
        self.level_source = lambda: (0.0, None)
        self._last = time.monotonic()

    def set_state(self, state: str) -> None:
        self.state = state

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        main, accent = PALETTE.get(self.state, PALETTE["standby"])
        k = min(1.0, dt * 4)
        self.main = _mix(self.main, main, k)
        self.accent = _mix(self.accent, accent, k)
        target_power = self.power if self.state == "boot" else 0.55 if self.state == "muted" else 1.0
        self.power += (target_power - self.power) * min(1.0, dt * 1.5)
        target_spin = {"thinking": 2.6, "transcribing": 1.8, "listening": 0.6, "speaking": 0.8}.get(self.state, 0.25)
        self.spin_speed += (target_spin - self.spin_speed) * min(1.0, dt * 3)
        self.spin += self.spin_speed * dt

        level, spectrum = self.level_source()
        rate = 18 if level > self.level else 5
        self.level += (level - self.level) * min(1.0, dt * rate)
        n = len(self.spectrum)
        for i in range(n):
            if spectrum is not None and len(spectrum):
                half = n // 2
                j = i if i < half else n - 1 - i
                target = float(spectrum[min(len(spectrum) - 1, int(j / half * len(spectrum) * 0.8))]) * min(1.0, 0.4 + self.level * 2)
            else:
                wob = math.sin(i * 0.9 + now * 11) * math.sin(i * 0.37 - now * 7)
                target = self.level * (0.55 + 0.45 * abs(wob))
            idle = (0.04 + 0.03 * math.sin(i * 0.5 + now * 2)) * self.power
            target = max(idle, target)
            cur = self.spectrum[i]
            self.spectrum[i] = cur + (target - cur) * min(1.0, dt * (20 if target > cur else 6))
        self.update()

    # ---- drawing ------------------------------------------------------------------

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        R = min(w, h) * 0.46
        p.translate(w / 2, h / 2)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        pw, main, accent = self.power, self.main, self.accent
        t = time.monotonic()

        halo = QRadialGradient(QPointF(0, 0), R * 1.1)
        halo.setColorAt(0, _c(main, 0.16 * pw + self.level * 0.12))
        halo.setColorAt(0.5, _c(main, 0.05 * pw))
        halo.setColorAt(1, _c(main, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(0, 0), R * 1.1, R * 1.1)

        self._tick_ring(p, R * 0.985, main, pw, self.spin * 0.15)
        self._segment_ring(p, R * 0.92, R * 0.012, main, pw, -self.spin * 0.35, [0.0, 0.22, 0.3, 0.47, 0.55, 0.8, 0.86, 0.97])
        self._text_ring(p, R * 0.86, main, pw, self.spin * 0.08)
        if self.state in ("thinking", "transcribing"):
            self._sweep(p, R * 0.86, accent, t)
        self._spectrum_ring(p, R * 0.6, R * 0.2, main, accent, pw)
        self._dashed_ring(p, R * 0.58, main, pw * 0.6, self.spin * 0.6)
        self._segment_ring(p, R * 0.535, R * 0.01, accent, pw * 0.8, self.spin * 1.2, [0.05, 0.2, 0.3, 0.45, 0.55, 0.7, 0.8, 0.95])
        self._coil(p, R * 0.32, R * 0.48, main, pw)
        self._core(p, R * 0.3, main, accent, pw, t)
        p.end()

    def _glow_arc(self, p, r, width, color, alpha, start, span):
        rect = QRectF(-r, -r, r * 2, r * 2)
        for mult, a in ((3.2, 0.12), (1.9, 0.22), (1.0, 1.0)):
            pen = QPen(_c(color, alpha * a), width * mult)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.drawArc(rect, int(-math.degrees(start) * 16), int(-math.degrees(span) * 16))

    def _tick_ring(self, p, r, color, pw, rot):
        p.save()
        p.rotate(math.degrees(rot))
        count = 144
        for i in range(count):
            a = i / count * TAU
            long = i % 12 == 0
            length = r * (0.05 if long else 0.022)
            p.setPen(QPen(_c(color, (0.8 if long else 0.35) * pw), max(1.0, r * 0.004)))
            c, s = math.cos(a), math.sin(a)
            p.drawLine(QPointF(c * r, s * r), QPointF(c * (r - length), s * (r - length)))
        p.restore()

    def _segment_ring(self, p, r, width, color, alpha, rot, stops):
        for i in range(0, len(stops), 2):
            self._glow_arc(p, r, width, color, 0.75 * alpha, stops[i] * TAU + rot, (stops[i + 1] - stops[i]) * TAU)

    def _dashed_ring(self, p, r, color, alpha, rot):
        p.save()
        p.rotate(math.degrees(rot))
        pen = QPen(_c(color, alpha), max(1.0, r * 0.006))
        pen.setDashPattern([3, 4.5])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)
        p.restore()

    def _text_ring(self, p, r, color, pw, rot):
        label = "J.A.R.V.I.S. • NEURAL INTERFACE ONLINE • VOICE CORE ACTIVE • "
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(max(9, int(r * 0.034)))
        p.setFont(font)
        p.setPen(_c(color, 0.45 * pw))
        span = TAU * 0.42
        for side in range(2):
            for i, ch in enumerate(label):
                a = side * math.pi + i / len(label) * span - span / 2 + rot
                p.save()
                p.rotate(math.degrees(a))
                p.translate(0, -r)
                p.drawText(QRectF(-8, -8, 16, 16), Qt.AlignCenter, ch)
                p.restore()

    def _sweep(self, p, r, color, t):
        start = (t * 3.2) % TAU
        for i in range(2):
            s = start + i * math.pi
            steps = 14
            for k in range(steps):
                frac = k / steps
                self._glow_arc(p, r, r * 0.03, color, 0.9 * frac, s + frac * TAU * 0.18, TAU * 0.18 / steps + 0.002)

    def _spectrum_ring(self, p, r, max_len, main, accent, pw):
        n = len(self.spectrum)
        width = max(1.5, TAU * r / n * 0.45)
        for i, v in enumerate(self.spectrum):
            v = min(1.0, v)
            a = i / n * TAU - math.pi / 2
            length = r * 0.02 + v * max_len
            pen = QPen(_c(_mix(main, accent, min(1.0, v * 1.4)), (0.35 + v * 0.65) * pw), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            c, s = math.cos(a), math.sin(a)
            p.drawLine(QPointF(c * r, s * r), QPointF(c * (r + length), s * (r + length)))

    def _coil(self, p, r0, r1, color, pw):
        segments, gap = 10, 0.06
        p.save()
        p.rotate(math.degrees(-math.pi / 2 + self.spin * 0.05))
        grad = QRadialGradient(QPointF(0, 0), r1, QPointF(0, 0), r0)
        grad.setColorAt(0, _c(color, 0.55 * pw + self.level * 0.3))
        grad.setColorAt(1, _c(color, 0.12 * pw))
        for i in range(segments):
            a0 = i / segments * TAU + gap
            a1 = (i + 1) / segments * TAU - gap
            path = QPainterPath()
            outer = QRectF(-r1, -r1, r1 * 2, r1 * 2)
            inner = QRectF(-r0, -r0, r0 * 2, r0 * 2)
            path.arcMoveTo(outer, -math.degrees(a0 + 0.015))
            path.arcTo(outer, -math.degrees(a0 + 0.015), -math.degrees(a1 - a0 - 0.03))
            path.arcTo(inner, -math.degrees(a1), math.degrees(a1 - a0))
            path.closeSubpath()
            p.setBrush(QBrush(grad))
            p.setPen(QPen(_c(color, 0.8 * pw), max(1.0, r1 * 0.012)))
            p.drawPath(path)
            p.setPen(QPen(_c(color, 0.22 * pw), max(1.0, r1 * 0.006)))
            for k in range(1, 6):
                a = a0 + (a1 - a0) * k / 6
                c, s = math.cos(a), math.sin(a)
                p.drawLine(QPointF(c * (r0 + 2), s * (r0 + 2)), QPointF(c * (r1 - 2), s * (r1 - 2)))
        p.restore()

    def _core(self, p, r, main, accent, pw, t):
        pulse = 0.5 + 0.5 * math.sin(t * 1.8)
        glow = min(1.0, 0.55 * pw + self.level * 0.7 + pulse * 0.08 * pw)
        for mult, a in ((2.6, 0.08), (1.6, 0.2), (1.0, 0.9)):
            p.setPen(QPen(_c(main, a * pw), r * 0.07 * mult))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(0, 0), r * 0.92, r * 0.92)
        grad = QRadialGradient(QPointF(0, 0), r * 0.85)
        grad.setColorAt(0, _c((255, 255, 255), glow))
        grad.setColorAt(0.35, _c(_mix(_mix(main, accent, 0.5), (255, 255, 255), 0.5), glow * 0.85))
        grad.setColorAt(0.75, _c(main, glow * 0.35))
        grad.setColorAt(1, _c(main, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(0, 0), r * 0.85, r * 0.85)
        p.save()
        p.rotate(math.degrees(-self.spin * 0.4))
        lit = int(t * 6) % 18
        for i in range(18):
            a = i / 18 * TAU
            p.setBrush(_c(accent, (1.0 if i == lit else 0.45) * pw))
            p.drawEllipse(QPointF(math.cos(a) * r * 0.62, math.sin(a) * r * 0.62), r * 0.025, r * 0.025)
        p.restore()


class Backdrop:
    """Paints the window background: gradient, hex grid, motes, scan line."""

    def __init__(self):
        self._cache: QPixmap | None = None
        self._size = None
        self.motes = [
            [random.random(), random.random(), (random.random() - 0.5) * 0.006, (random.random() - 0.5) * 0.006,
             random.random() * 1.4 + 0.4, random.random() * 0.5 + 0.1]
            for _ in range(70)
        ]
        self._last = time.monotonic()

    def _build(self, w, h) -> QPixmap:
        pix = QPixmap(w, h)
        g = QPainter(pix)
        g.setRenderHint(QPainter.Antialiasing)
        bg = QRadialGradient(QPointF(w / 2, h * 0.45), max(w, h) * 0.75)
        bg.setColorAt(0, QColor("#06192a"))
        bg.setColorAt(0.55, QColor("#030c16"))
        bg.setColorAt(1, QColor("#010409"))
        g.fillRect(0, 0, w, h, QBrush(bg))
        size = 26
        hex_w, hex_h = math.sqrt(3) * size, 1.5 * size
        row = -1
        while row * hex_h < h + size:
            col = -1
            while col * hex_w < w + hex_w:
                x = col * hex_w + (hex_w / 2 if row % 2 else 0)
                y = row * hex_h
                dx, dy = (x - w / 2) / (w / 2), (y - h * 0.45) / (h / 2)
                fade = max(0.0, 1 - math.hypot(dx, dy) * 0.8)
                g.setPen(QPen(QColor(63, 216, 255, int((0.05 * fade + 0.012) * 255)), 1))
                poly = QPolygonF([
                    QPointF(x + math.cos(math.pi / 3 * i + math.pi / 6) * size * 0.96,
                            y + math.sin(math.pi / 3 * i + math.pi / 6) * size * 0.96)
                    for i in range(6)
                ])
                g.drawPolygon(poly)
                col += 1
            row += 1
        g.end()
        return pix

    def paint(self, p: QPainter, w: int, h: int) -> None:
        if self._size != (w, h):
            self._cache = self._build(w, h)
            self._size = (w, h)
        p.drawPixmap(0, 0, self._cache)
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        p.setPen(Qt.NoPen)
        for m in self.motes:
            m[0] = (m[0] + m[2] * dt) % 1
            m[1] = (m[1] + m[3] * dt) % 1
            p.setBrush(QColor(120, 225, 255, int(m[5] * 255)))
            p.drawEllipse(QPointF(m[0] * w, m[1] * h), m[4], m[4])
        y = (now / 9 % 1) * h
        scan = QLinearGradient(0, y - 60, 0, y)
        scan.setColorAt(0, QColor(63, 216, 255, 0))
        scan.setColorAt(1, QColor(63, 216, 255, 13))
        p.fillRect(QRectF(0, y - 60, w, 60), QBrush(scan))


class Panel(QFrame):
    """A HUD panel: clipped corners, corner brackets and a titled header."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(16, 12, 16, 14)
        self.layout_.setSpacing(6)
        header = QLabel(f"■  {title.upper()}")
        header.setObjectName("panelTitle")
        self.layout_.addWidget(header)
        self.header = header

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, c = self.width() - 1, self.height() - 1, 14
        shape = QPolygonF([QPointF(0, 0), QPointF(w - c, 0), QPointF(w, c), QPointF(w, h), QPointF(c, h), QPointF(0, h - c)])
        p.setBrush(QColor(6, 22, 36, 150))
        p.setPen(QPen(QColor(63, 216, 255, 28), 1))
        p.drawPolygon(shape)
        p.setPen(QPen(QColor(63, 216, 255, 170), 1))
        p.drawLine(QPointF(0, 0), QPointF(18, 0))
        p.drawLine(QPointF(0, 0), QPointF(0, 18))
        p.drawLine(QPointF(w, h), QPointF(w - 18, h))
        p.drawLine(QPointF(w, h), QPointF(w, h - 18))
        p.end()


class Meter(QWidget):
    """Segmented horizontal bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.warn = False
        self.setFixedHeight(7)

    def set_value(self, value: float, warn: bool = False) -> None:
        self.value, self.warn = max(0.0, min(1.0, value)), warn
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        seg, gap = 6, 2
        count = max(1, self.width() // (seg + gap))
        lit = round(count * self.value)
        on = QColor(255, 182, 72) if self.warn else QColor(63, 216, 255)
        off = QColor(63, 216, 255, 26)
        for i in range(count):
            p.fillRect(i * (seg + gap), 0, seg, self.height(), on if i < lit else off)
        p.end()


class Sparkline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[float] = []
        self.setFixedHeight(44)

    def push(self, value: float) -> None:
        self.values = (self.values + [value])[-60:]
        self.update()

    def paintEvent(self, _event):
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        step = w / (len(self.values) - 1)
        path = QPainterPath(QPointF(0, h - 2 - self.values[0] * (h - 4)))
        for i, v in enumerate(self.values[1:], 1):
            path.lineTo(i * step, h - 2 - v * (h - 4))
        p.setPen(QPen(QColor(63, 216, 255, 230), 1.5))
        p.drawPath(path)
        fill = QPainterPath(path)
        fill.lineTo(w, h)
        fill.lineTo(0, h)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(63, 216, 255, 64))
        grad.setColorAt(1, QColor(63, 216, 255, 0))
        p.fillPath(fill, QBrush(grad))
        p.end()
