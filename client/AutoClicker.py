"""
PocketTerm Auto Clicker
A small, always-on-top helper window designed to sit beside PocketTerm.

Requirements:
    - Python 3.10+
    - PySide6

Uses the Windows API for mouse clicks and global F6 detection, so no
additional mouse/keyboard packages are needed.

Speed notes:
    - Clicks run on a background thread with a high-resolution timer, so the
      UI can no longer slow them down.
    - Each click is a single SendInput call (down + up together).
    - Windows timer resolution is raised to 1 ms while the app is open.
    - Interval can go as low as 1 ms (about 500-1000 clicks/sec in practice).
"""

import sys
import time
import ctypes
import threading
from pathlib import Path
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QFrame,
    QComboBox,
)
from PySide6.QtGui import QIcon


# ---------------------------------------------------------------------
# PocketTerm theme palette
# These match PocketTerm's dark background and accent palette.
# Choose the same accent as PocketTerm's Settings page.
# ---------------------------------------------------------------------

BACKGROUND = "#08070d"
PANEL = "#130e21"
PANEL_ALT = "#0e0a18"
TEXT = "#f3f0ff"
TEXT_BODY = "#d9d3f2"
TEXT_MUTED = "#a69fc4"

ACCENTS = {
    "Violet": {
        "accent": "#8b5cf6", "strong": "#7c3aed", "soft": "#a78bfa",
        "border": "#3b2a66", "hover": "#5b3fb5",
    },
    "Magenta": {
        "accent": "#d946ef", "strong": "#c026d3", "soft": "#e879f9",
        "border": "#5b2a66", "hover": "#a03fb5",
    },
    "Indigo": {
        "accent": "#6366f1", "strong": "#4f46e5", "soft": "#818cf8",
        "border": "#2a2f66", "hover": "#3f4fb5",
    },
    "Emerald": {
        "accent": "#10b981", "strong": "#059669", "soft": "#34d399",
        "border": "#1f4a3c", "hover": "#2f8f6f",
    },
}


# ---------------------------------------------------------------------
# Windows SendInput structures
# ---------------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    # MOUSEINPUT is the largest member, so this gives the correct
    # sizeof(INPUT) on both 32-bit and 64-bit Windows.
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


INPUT_MOUSE = 0
LEFTDOWN = 0x0002
LEFTUP = 0x0004


class WindowsMouse:
    """Small wrapper around the Windows mouse API."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("PocketTerm Auto Clicker currently supports Windows only.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = [
            wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int
        ]
        self.user32.SendInput.restype = wintypes.UINT

        # Pre-build the down+up pair once and reuse it for every click.
        self._events = (INPUT * 2)()
        for i, flag in enumerate((LEFTDOWN, LEFTUP)):
            self._events[i].type = INPUT_MOUSE
            self._events[i].mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
        self._size = ctypes.sizeof(INPUT)

    def click(self):
        # Click at the current pointer position (down + up in one call).
        self.user32.SendInput(2, self._events, self._size)


class ClickerThread(threading.Thread):
    """Background thread that clicks on a precise schedule."""

    def __init__(self, mouse, interval_ms):
        super().__init__(daemon=True)
        self.mouse = mouse
        self.interval = interval_ms / 1000.0
        self.stop_event = threading.Event()
        self.count = 0
        self.error = None

    def run(self):
        interval = self.interval
        clock = time.perf_counter
        next_time = clock()
        try:
            while not self.stop_event.is_set():
                self.mouse.click()
                self.count += 1

                next_time += interval
                now = clock()

                # If we fell far behind, resync instead of bursting.
                if now - next_time > interval * 5 + 0.05:
                    next_time = now + interval
                    continue

                # Sleep for the bulk of the wait, then spin for precision.
                while True:
                    remaining = next_time - clock()
                    if remaining <= 0 or self.stop_event.is_set():
                        break
                    if remaining > 0.002:
                        time.sleep(remaining - 0.001)
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)

    def stop(self):
        self.stop_event.set()


class AutoClickerWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.mouse = None
        self.running = False
        self.f6_was_down = False
        self.click_count = 0
        self.worker = None
        self._startup_error = None
        self._last_count = 0
        self._last_stat_time = time.perf_counter()
        self._timer_res_set = False

        self.setWindowTitle("PocketTerm Auto Clicker")
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinimizeButtonHint
        )
        self.setMinimumWidth(310)
        self.setMaximumWidth(370)

        try:
            self.mouse = WindowsMouse()
        except OSError as exc:
            self._startup_error = str(exc)

        # Raise Windows timer resolution to 1 ms for accurate fast clicking.
        if sys.platform == "win32":
            try:
                ctypes.WinDLL("winmm").timeBeginPeriod(1)
                self._timer_res_set = True
            except Exception:
                pass

        self._build_ui()
        self._apply_theme("Violet")

        # Poll F6 globally. Edge detection prevents repeated toggles
        # while the key is held down.
        self.hotkey_timer = QTimer(self)
        self.hotkey_timer.setTimerType(Qt.PreciseTimer)
        self.hotkey_timer.timeout.connect(self._poll_hotkey)
        self.hotkey_timer.start(20)

        # UI refresh for the click counter (kept off the click path so the
        # interface never slows the clicker down).
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._update_stats)
        self.stats_timer.start(100)

        self._set_running_ui(False)
        self.move(80, 100)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(11)

        header = QHBoxLayout()
        title = QLabel("POCKETTERM")
        title.setStyleSheet(
            "font-family: Consolas; font-size: 11px; "
            "font-weight: 700; letter-spacing: 1px;"
        )
        header.addWidget(title)
        header.addStretch(1)

        self.state_badge = QLabel("● STOPPED")
        self.state_badge.setObjectName("StateBadge")
        header.addWidget(self.state_badge)
        root.addLayout(header)

        heading = QLabel("Auto Clicker")
        heading.setObjectName("Heading")
        root.addWidget(heading)

        subtitle = QLabel("Floating desktop helper")
        subtitle.setObjectName("Muted")
        root.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(13, 12, 13, 12)
        card_layout.setSpacing(9)

        interval_label = QLabel("Click interval")
        interval_label.setObjectName("Body")
        card_layout.addWidget(interval_label)

        interval_row = QHBoxLayout()
        self.interval_input = QSpinBox()
        self.interval_input.setRange(1, 60000)
        self.interval_input.setSingleStep(10)
        self.interval_input.setValue(10)
        self.interval_input.setSuffix(" ms")
        self.interval_input.setToolTip(
            "Time between clicks. 1 ms is the fastest (roughly 500-1000 "
            "clicks per second, depending on your system)."
        )
        interval_row.addWidget(self.interval_input)
        interval_row.addStretch(1)
        card_layout.addLayout(interval_row)

        self.click_count_label = QLabel("Clicks: 0")
        self.click_count_label.setObjectName("Muted")
        card_layout.addWidget(self.click_count_label)
        root.addWidget(card)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start_clicking)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("Danger")
        self.stop_button.clicked.connect(self.stop_clicking)

        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        root.addLayout(buttons)

        self.status_label = QLabel("Ready. F6 toggles start/stop.")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        hotkey = QLabel("GLOBAL HOTKEY   F6")
        hotkey.setObjectName("Hotkey")
        hotkey.setAlignment(Qt.AlignCenter)
        root.addWidget(hotkey)

        theme_row = QHBoxLayout()
        theme_label = QLabel("Accent")
        theme_label.setObjectName("Muted")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(ACCENTS.keys()))
        self.theme_combo.currentTextChanged.connect(self._apply_theme)
        theme_row.addWidget(theme_label)
        theme_row.addStretch(1)
        theme_row.addWidget(self.theme_combo)
        root.addLayout(theme_row)

        if self._startup_error:
            self.status_label.setText(self._startup_error)

    def _apply_theme(self, name):
        palette = ACCENTS.get(name, ACCENTS["Violet"])
        accent = palette["accent"]
        strong = palette["strong"]
        soft = palette["soft"]
        border = palette["border"]
        hover = palette["hover"]

        self.setStyleSheet(f"""
            QWidget {{
                background-color: {BACKGROUND};
                color: {TEXT};
                font-family: 'Segoe UI';
                font-size: 12px;
            }}
            QLabel {{ background: transparent; }}
            QLabel#Heading {{
                color: {TEXT};
                font-size: 21px;
                font-weight: 700;
            }}
            QLabel#Body {{
                color: {TEXT_BODY};
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#Muted {{
                color: {TEXT_MUTED};
                font-size: 11px;
            }}
            QLabel#Hotkey {{
                color: {soft};
                background: {PANEL_ALT};
                border: 1px solid {border};
                border-radius: 7px;
                padding: 8px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#StateBadge {{
                color: {soft};
                font-size: 10px;
                font-weight: 700;
            }}
            QFrame#Card {{
                background-color: {PANEL};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            QSpinBox, QComboBox {{
                background-color: {PANEL_ALT};
                color: {TEXT};
                border: 1px solid {border};
                border-radius: 7px;
                padding: 7px 9px;
                min-width: 95px;
            }}
            QSpinBox:focus, QComboBox:focus {{
                border: 1px solid {hover};
            }}
            QPushButton {{
                background-color: #1a1230;
                color: {TEXT};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 9px 15px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: #2a1d4d;
                border: 1px solid {hover};
            }}
            QPushButton:disabled {{
                color: #706783;
                background-color: #120d20;
                border-color: #1f1736;
            }}
            QPushButton#Primary {{
                background-color: {strong};
                color: white;
                border: 1px solid {accent};
            }}
            QPushButton#Primary:hover {{
                background-color: {accent};
            }}
            QPushButton#Danger {{
                background-color: #281321;
                color: #fda4af;
                border: 1px solid #653044;
            }}
            QPushButton#Danger:hover {{
                background-color: #3a1928;
                border: 1px solid #a34b69;
            }}
        """)

    def start_clicking(self):
        if self.mouse is None:
            self.status_label.setText(
                self._startup_error or "Mouse input is unavailable."
            )
            return

        if self.running:
            return

        self.running = True
        self.worker = ClickerThread(self.mouse, self.interval_input.value())
        self.worker.count = self.click_count
        self._last_count = self.click_count
        self._last_stat_time = time.perf_counter()
        self.worker.start()
        self._set_running_ui(True)
        self.status_label.setText("Clicking at the current pointer position.")

    def stop_clicking(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker.join(timeout=0.5)
            self.click_count = self.worker.count
            self.worker = None
        self.running = False
        self._set_running_ui(False)
        self.click_count_label.setText(f"Clicks: {self.click_count:,}")
        self.status_label.setText("Stopped. Ready when you are.")

    def toggle_clicking(self):
        if self.running:
            self.stop_clicking()
        else:
            self.start_clicking()

    def _update_stats(self):
        worker = self.worker
        if worker is None:
            return

        if worker.error:
            err = worker.error
            self.stop_clicking()
            self.status_label.setText(f"Clicking stopped: {err}")
            return

        now = time.perf_counter()
        count = worker.count
        elapsed = now - self._last_stat_time
        rate = (count - self._last_count) / elapsed if elapsed > 0 else 0
        self._last_count = count
        self._last_stat_time = now
        self.click_count_label.setText(f"Clicks: {count:,}   ·   {rate:,.0f}/sec")

    def _set_running_ui(self, running):
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.interval_input.setEnabled(not running)

        if running:
            self.state_badge.setText("● RUNNING")
            self.state_badge.setStyleSheet("color: #6ee7b7;")
        else:
            self.state_badge.setText("● STOPPED")
            self.state_badge.setStyleSheet("color: #a69fc4;")

    def _poll_hotkey(self):
        if sys.platform != "win32":
            return
        try:
            # VK_F6 = 0x75. The high bit means the key is currently down.
            is_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x75) & 0x8000)
            if is_down and not self.f6_was_down:
                self.toggle_clicking()
            self.f6_was_down = is_down
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop_clicking()
        self.hotkey_timer.stop()
        self.stats_timer.stop()
        if self._timer_res_set:
            try:
                ctypes.WinDLL("winmm").timeEndPeriod(1)
            except Exception:
                pass
        event.accept()


def find_pocketterm_icon():
    """Find the shared PocketTerm icon without relying on a drive letter.

    With the usual layout, this script lives in:
        Assets/GitPortable/Data/home/Scripts/
    and the shared icon lives in:
        Assets/Images/icon.png

    Searching parent folders also tolerates small layout changes.
    """
    script_dir = Path(__file__).resolve().parent
    for folder in (script_dir, *script_dir.parents):
        candidate = folder / "Images" / "icon.png"
        if candidate.is_file():
            return candidate
    return None


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PocketTerm Auto Clicker")

    icon_path = find_pocketterm_icon()
    if icon_path is not None:
        shared_icon = QIcon(str(icon_path))
        app.setWindowIcon(shared_icon)
    else:
        shared_icon = QIcon()

    window = AutoClickerWindow()
    if not shared_icon.isNull():
        window.setWindowIcon(shared_icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()