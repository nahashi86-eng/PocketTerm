"""Performance Overlay - a PocketTerm plugin.

Put this file in the Plugins folder, open the Plugins page, press Scan, select it and press Enable.
Requires psutil:  pip install psutil

A transparent, always-on-top overlay shows live CPU, memory, disk and network usage,
each with a scrolling graph:
  * drag it             -> move it anywhere on screen
  * right-click it      -> menu: refresh rate, background opacity, reset position, hide
  * "Toggle click-through" (Plugin actions) -> mouse clicks pass straight through the overlay

Three buttons appear under "Plugin actions": "Toggle performance overlay",
"Toggle click-through" and "Reset overlay position".
"""
import time
from collections import deque

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer
from PySide6.QtGui import (
    QActionGroup, QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QMenu, QWidget

try:
    import psutil
except ImportError:           # reported in register()
    psutil = None

WIDTH = 252
PAD = 12
HEADER_H = 22
ROW_H = 50
GRAPH_H = 20
HISTORY = 60                  # samples kept for the graphs
MARGIN = 24
DRAG_THRESHOLD = 4            # pixels the mouse must move before a click becomes a drag

# PocketTerm palette
TEXT = QColor("#f3f0ff")
MUTED = QColor("#a69fc4")
ACCENT = QColor("#8b5cf6")
ACCENT_SOFT = QColor("#c4b5fd")
OK = QColor("#6ee7b7")
WARN = QColor("#fcd34d")
ERROR = QColor("#fda4af")
SHADOW = QColor(0, 0, 0, 210)

REFRESH_CHOICES = [("0.5 seconds", 500), ("1 second", 1000), ("2 seconds", 2000)]
BACKGROUND_CHOICES = [("Fully transparent", 0), ("Light", 70), ("Medium", 140), ("Dark", 210)]

_overlay = None


# =====================================
# HELPERS
# =====================================

def _fmt_rate(value):
    """Bytes per second -> short human string."""
    for unit in ("B/s", "KB/s", "MB/s"):
        if value < 1024 or unit == "MB/s":
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB/s"


def _level_color(percent):
    if percent >= 90:
        return ERROR
    if percent >= 70:
        return WARN
    return None               # caller keeps its own color


def _draw_text(p, rect, flags, text, color):
    """Text with a dark drop shadow so it stays readable on a transparent background."""
    p.setPen(SHADOW)
    p.drawText(rect.translated(1, 1), flags, text)
    p.setPen(color)
    p.drawText(rect, flags, text)


# =====================================
# OVERLAY WIDGET
# =====================================

class PerformanceOverlay(QWidget):

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._press_global = None
        self._press_window_pos = None
        self._dragged = False
        self._click_through = False
        self._bg_alpha = 70
        self._interval = 1000

        self.cpu = deque(maxlen=HISTORY)
        self.mem = deque(maxlen=HISTORY)
        self.disk_r = deque(maxlen=HISTORY)
        self.disk_w = deque(maxlen=HISTORY)
        self.net_d = deque(maxlen=HISTORY)
        self.net_u = deque(maxlen=HISTORY)

        self._last_time = time.monotonic()
        self._last_net = psutil.net_io_counters()
        self._last_disk = psutil.disk_io_counters()
        psutil.cpu_percent(None)          # prime: the first call always returns 0.0

        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Performance overlay (drag to move, right-click for menu)")
        self.setFixedSize(WIDTH, PAD * 2 + HEADER_H + ROW_H * 4)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._interval)

        self.reset_position()

    # ---------- window setup ----------

    def _apply_flags(self):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if self._click_through:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)

    def set_click_through(self, enabled):
        self._click_through = enabled
        was_visible = self.isVisible()
        pos = self.pos()
        self._apply_flags()               # changing flags hides the window
        self.move(pos)
        if was_visible:
            self.show()

    def toggle_click_through(self):
        self.set_click_through(not self._click_through)
        return self._click_through

    def reset_position(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.move(100, 100)
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - MARGIN, area.top() + MARGIN)

    def _set_interval(self, ms):
        self._interval = ms
        self._timer.start(ms)

    def _set_background(self, alpha):
        self._bg_alpha = alpha
        self.update()

    # ---------- sampling ----------

    def _tick(self):
        now = time.monotonic()
        elapsed = max(now - self._last_time, 0.001)
        self._last_time = now

        self.cpu.append(psutil.cpu_percent(None))
        self.mem.append(psutil.virtual_memory().percent)

        net = psutil.net_io_counters()
        if net and self._last_net:
            self.net_d.append(max(net.bytes_recv - self._last_net.bytes_recv, 0) / elapsed)
            self.net_u.append(max(net.bytes_sent - self._last_net.bytes_sent, 0) / elapsed)
        self._last_net = net

        disk = psutil.disk_io_counters()
        if disk and self._last_disk:
            self.disk_r.append(max(disk.read_bytes - self._last_disk.read_bytes, 0) / elapsed)
            self.disk_w.append(max(disk.write_bytes - self._last_disk.write_bytes, 0) / elapsed)
        self._last_disk = disk

        self.update()

    # ---------- painting ----------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        panel = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        border = QColor(ACCENT)
        border.setAlpha(60 + self._bg_alpha // 3)
        p.setPen(QPen(border, 1))
        p.setBrush(QColor(8, 7, 13, self._bg_alpha))
        p.drawRoundedRect(panel, 12, 12)

        label_font = QFont("Segoe UI", 8)
        label_font.setBold(True)
        label_font.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        value_font = QFont("Consolas", 10)
        value_font.setBold(True)

        inner_w = WIDTH - PAD * 2
        left = PAD
        y = PAD

        # header
        p.setFont(label_font)
        _draw_text(p, QRectF(left, y, inner_w, HEADER_H),
                   Qt.AlignLeft | Qt.AlignVCenter, "PERFORMANCE", ACCENT_SOFT)
        y += HEADER_H

        cpu_now = self.cpu[-1] if self.cpu else 0.0
        mem_now = self.mem[-1] if self.mem else 0.0
        d_now = self.disk_r[-1] if self.disk_r else 0.0
        w_now = self.disk_w[-1] if self.disk_w else 0.0
        nd_now = self.net_d[-1] if self.net_d else 0.0
        nu_now = self.net_u[-1] if self.net_u else 0.0

        rows = [
            ("CPU", f"{cpu_now:4.1f}%", _level_color(cpu_now) or ACCENT_SOFT,
             [self.cpu], 100.0, [ACCENT_SOFT]),
            ("MEMORY", f"{mem_now:4.1f}%", _level_color(mem_now) or OK,
             [self.mem], 100.0, [OK]),
            ("DISK", f"R {_fmt_rate(d_now)}  W {_fmt_rate(w_now)}", TEXT,
             [self.disk_r, self.disk_w], self._scale(self.disk_r, self.disk_w), [OK, WARN]),
            ("NETWORK", f"↓ {_fmt_rate(nd_now)}  ↑ {_fmt_rate(nu_now)}", TEXT,
             [self.net_d, self.net_u], self._scale(self.net_d, self.net_u), [ACCENT_SOFT, ERROR]),
        ]

        for label, value, value_color, series, vmax, colors in rows:
            p.setFont(label_font)
            _draw_text(p, QRectF(left, y, 70, 16), Qt.AlignLeft | Qt.AlignVCenter, label, MUTED)
            p.setFont(value_font if len(value) < 12 else QFont("Consolas", 8, QFont.Bold))
            _draw_text(p, QRectF(left + 60, y, inner_w - 60, 16),
                       Qt.AlignRight | Qt.AlignVCenter, value, value_color)
            self._draw_graph(p, QRectF(left, y + 20, inner_w, GRAPH_H), series, vmax, colors)
            y += ROW_H

    @staticmethod
    def _scale(*series_list):
        """Graph ceiling for throughput charts: rolling max with a 100 KB/s floor."""
        peak = max((max(s) for s in series_list if s), default=0.0)
        return max(peak * 1.1, 100 * 1024.0)

    @staticmethod
    def _draw_graph(p, rect, series_list, vmax, colors):
        base = QColor(255, 255, 255, 28)
        p.setPen(QPen(base, 1))
        p.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom()))

        step = rect.width() / (HISTORY - 1)
        for data, color in zip(series_list, colors):
            n = len(data)
            if n < 2:
                continue
            x0 = rect.right() - (n - 1) * step
            pts = [
                QPointF(x0 + i * step, rect.bottom() - min(v / vmax, 1.0) * rect.height())
                for i, v in enumerate(data)
            ]
            line = QPainterPath(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)

            area = QPainterPath(line)
            area.lineTo(pts[-1].x(), rect.bottom())
            area.lineTo(pts[0].x(), rect.bottom())
            area.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(45)
            p.fillPath(area, fill)

            p.setPen(QPen(color, 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawPath(line)

    # ---------- mouse: drag to move ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_window_pos = self.pos()
            self._dragged = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_global is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragged and delta.manhattanLength() < DRAG_THRESHOLD:
            return
        self._dragged = True
        self.move(self._clamped(self._press_window_pos + delta))
        event.accept()

    def mouseReleaseEvent(self, event):
        self._press_global = None
        self._dragged = False
        event.accept()

    def _clamped(self, pos):
        """Keep the overlay fully on one of the connected screens."""
        center = pos + QPoint(self.width() // 2, self.height() // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            return pos
        area = screen.availableGeometry()
        x = min(max(pos.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(pos.y(), area.top()), area.bottom() - self.height() + 1)
        return QPoint(x, y)

    # ---------- right-click menu ----------

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        refresh = menu.addMenu("Refresh rate")
        refresh_group = QActionGroup(refresh)
        for text, ms in REFRESH_CHOICES:
            action = refresh.addAction(text)
            action.setCheckable(True)
            action.setChecked(ms == self._interval)
            refresh_group.addAction(action)
            action.triggered.connect(lambda _=False, v=ms: self._set_interval(v))

        background = menu.addMenu("Background")
        bg_group = QActionGroup(background)
        for text, alpha in BACKGROUND_CHOICES:
            action = background.addAction(text)
            action.setCheckable(True)
            action.setChecked(alpha == self._bg_alpha)
            bg_group.addAction(action)
            action.triggered.connect(lambda _=False, v=alpha: self._set_background(v))

        menu.addSeparator()
        menu.addAction("Reset position", self.reset_position)
        menu.addAction("Hide overlay", self.hide)
        menu.exec(event.globalPos())

    def shutdown(self):
        self._timer.stop()
        self.close()
        self.deleteLater()


# =====================================
# ACTIONS
# =====================================

def _toggle(app):
    if _overlay is None:
        return
    if _overlay.isVisible():
        _overlay.hide()
        app.log("Performance overlay hidden")
    else:
        _overlay.show()
        app.log("Performance overlay shown")


def _toggle_click_through(app):
    if _overlay is None:
        return
    enabled = _overlay.toggle_click_through()
    app.log("Overlay click-through " + ("on (clicks pass through)" if enabled else "off (draggable)"))


def _reset(app):
    if _overlay is not None:
        _overlay.reset_position()
        _overlay.show()


# =====================================
# PLUGIN HOOKS
# =====================================

def register(app):
    """Called by PocketTerm when the plugin is enabled."""
    global _overlay
    if psutil is None:
        app.notify("psutil is not installed. Run: pip install psutil", "Performance Overlay")
        app.log("Performance Overlay needs psutil (pip install psutil)")
        return
    _overlay = PerformanceOverlay(app)
    _overlay.show()
    app.add_action("Toggle performance overlay", lambda: _toggle(app), "Show or hide the overlay")
    app.add_action("Toggle click-through", lambda: _toggle_click_through(app),
                   "Let mouse clicks pass through the overlay (turn off to drag it again)")
    app.add_action("Reset overlay position", lambda: _reset(app), "Move the overlay back to the top-right corner")
    app.log("Performance Overlay plugin registered")


def unregister(app):
    """Remove the overlay window; PocketTerm removes the action buttons itself."""
    global _overlay
    if _overlay is not None:
        _overlay.shutdown()
        _overlay = None
    app.log("Performance Overlay plugin unregistered")
