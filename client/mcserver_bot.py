from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget, QGridLayout, QScrollArea
)

APP_TITLE = "PocketTerm - MCServer Bot"
DEFAULT_HOST = "NahaSMP.aternos.me"
DEFAULT_PORT = 25565

# A practical selection list. "Auto" lets Mineflayer detect the server version.
VERSION_OPTIONS = [
    "Auto", "1.21.8", "1.21.5", "1.21.4", "1.21.1",
    "1.20.6", "1.20.4", "1.20.1", "1.19.4", "1.18.2",
    "1.16.5", "1.12.2", "1.8.8",
]

BG = "#10141c"
PANEL = "#171d29"
PANEL_ALT = "#1d2533"
BORDER = "#303b4d"
TEXT = "#f3f0ff"
MUTED = "#a69fc4"
ACCENT = "#8b5cf6"
ACCENT_HOVER = "#a78bfa"
GREEN = "#6ee7b7"
RED = "#fda4af"
YELLOW = "#fcd34d"


def qss():
    return f"""
    QWidget {{
        background: {BG}; color: {TEXT};
        font-family: 'Segoe UI'; font-size: 10pt;
    }}
    QMainWindow {{ background: {BG}; }}
    QFrame#Panel, QFrame#Card {{
        background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
    }}
    QLabel#PageTitle {{ font-size: 20pt; font-weight: 700; color: {TEXT}; }}
    QLabel#Description, QLabel#Subtitle {{ color: {MUTED}; font-size: 9pt; }}
    QLabel#SectionTitle, QLabel#Section {{ color: {ACCENT_HOVER}; font-size: 10pt; font-weight: 700; }}
    QLabel#Status {{ color: {GREEN}; font-weight: 600; }}
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
        background: #0c1119; border: 1px solid {BORDER}; border-radius: 6px;
        padding: 8px; selection-background-color: {ACCENT};
    }}
    QLineEdit, QComboBox, QSpinBox {{
        min-height: 22px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {ACCENT};
    }}
    QComboBox::drop-down {{ border: 0; width: 24px; }}
    QPushButton {{
        background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 7px;
        padding: 8px 13px; font-weight: 600;
    }}
    QPushButton:hover {{ border-color: {ACCENT}; background: #252d3d; }}
    QPushButton:pressed {{ background: #302448; }}
    QPushButton:disabled {{ color: #716987; }}
    QPushButton#Primary {{
        background: {ACCENT}; color: white; border: 1px solid {ACCENT};
    }}
    QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
    QScrollBar:vertical {{ background: {BG}; width: 9px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 20px; }}
    """

def find_main_app_file():
    here = Path(__file__).resolve().parent
    names = ("PocketTerm.py", "PocketTerm_Enhanced.py", "PocketTerm_Main.py",
             "PocketTermLauncher.py")
    for folder in (here, *here.parents):
        for name in names:
            candidate = folder / name
            if candidate.is_file() and candidate.resolve() != Path(__file__).resolve():
                return candidate
        for candidate in sorted(folder.glob("PocketTerm*.py")):
            if candidate.is_file() and candidate.resolve() != Path(__file__).resolve():
                return candidate
    return None


def load_pocketterm_theme(window):
    """Reuse PocketTerm's own icon and stylesheet when its main module is available."""
    main_file = find_main_app_file()
    if not main_file:
        return "PocketTerm main source not found; using the matching fallback dark theme."
    try:
        spec = importlib.util.spec_from_file_location("pocketterm_theme_runtime", main_file)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load PocketTerm main module.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        icon_path, icon_status = module.find_icon()
        if icon_path:
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                window.setWindowIcon(icon)
                app = QApplication.instance()
                if app:
                    app.setWindowIcon(icon)

        settings = module.Settings(module.SETTINGS_FILE)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(module.build_style(settings.data))
        return "Loaded PocketTerm's exact icon and stylesheet."
    except Exception as exc:
        return f"Could not load PocketTerm theme ({exc}); using fallback dark theme."


class MCServerBotWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(760, 650)
        self.setMinimumSize(620, 540)
        self.process = None
        self.bot_script = Path(__file__).resolve().with_name("mcserver_bot.js")
        self._build_ui()
        theme_status = load_pocketterm_theme(self)
        if theme_status.startswith("Could not load") or theme_status.startswith("PocketTerm main source not found"):
            QApplication.instance().setStyleSheet(qss())
        self._log(theme_status)
        self._load_pocketterm_icon()
        self._find_node()
        self._set_connected(False)

    def _build_ui(self):
        # Keep the page usable on short displays: the content keeps its natural
        # height and the user can scroll instead of having controls compressed.
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        root = QWidget()
        self.scroll_area.setWidget(root)
        self.setCentralWidget(self.scroll_area)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)

        heading = QVBoxLayout()
        title = QLabel("MCServer Bot")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Connect an offline-mode bot and let it roam around the world.")
        subtitle.setObjectName("Description")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        outer.addLayout(heading)

        config = QFrame()
        config.setObjectName("Card")
        form = QGridLayout(config)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(12)

        section = QLabel("SERVER CONFIGURATION")
        section.setObjectName("SectionTitle")
        form.addWidget(section, 0, 0, 1, 3)

        form.addWidget(QLabel("Server address"), 1, 0)
        self.host = QLineEdit(DEFAULT_HOST)
        self.host.setPlaceholderText("Enter server address")
        form.addWidget(self.host, 1, 1, 1, 2)

        form.addWidget(QLabel("Port"), 2, 0)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(DEFAULT_PORT)
        form.addWidget(self.port, 2, 1, 1, 2)

        form.addWidget(QLabel("Minecraft version"), 3, 0)
        self.version = QComboBox()
        self.version.addItems(VERSION_OPTIONS)
        self.version.setCurrentText("Auto")
        form.addWidget(self.version, 3, 1, 1, 2)

        form.addWidget(QLabel("Bot username"), 4, 0)
        self.username = QLineEdit("PocketTermBot")
        self.username.setMaxLength(16)
        self.username.setPlaceholderText("Enter bot username")
        form.addWidget(self.username, 4, 1, 1, 2)

        form.addWidget(QLabel("Wander radius"), 5, 0)
        self.radius = QSpinBox()
        self.radius.setRange(5, 128)
        self.radius.setValue(24)
        self.radius.setSuffix(" blocks")
        form.addWidget(self.radius, 5, 1, 1, 2)

        auth = QLabel("Authentication: Offline / cracked mode")
        auth.setObjectName("Description")
        form.addWidget(auth, 6, 0, 1, 3)
        outer.addWidget(config)

        actions = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("Primary")
        self.connect_btn.clicked.connect(self.connect_bot)
        actions.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect_bot)
        self.disconnect_btn.setEnabled(False)
        actions.addWidget(self.disconnect_btn)

        clear_btn = QPushButton("Clear log")
        clear_btn.clicked.connect(lambda: self.log.clear())
        actions.addWidget(clear_btn)
        actions.addStretch()
        outer.addLayout(actions)

        status_row = QHBoxLayout()
        self.status_text = QLabel("Disconnected")
        self.status_text.setObjectName("Status")
        status_row.addWidget(self.status_text)
        status_row.addStretch()
        self.node_status = QLabel("Checking Node.js...")
        self.node_status.setObjectName("Description")
        status_row.addWidget(self.node_status)
        outer.addLayout(status_row)

        log_panel = QFrame()
        log_panel.setObjectName("Card")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(14, 12, 14, 14)
        log_head = QHBoxLayout()
        log_title = QLabel("BOT ACTIVITY LOG")
        log_title.setObjectName("SectionTitle")
        log_head.addWidget(log_title)
        log_head.addStretch()
        log_layout.addLayout(log_head)

        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.log.setPlaceholderText("Connection and movement messages will appear here.")
        log_layout.addWidget(self.log, 1)
        outer.addWidget(log_panel, 1)

        foot = QLabel(
            "The server must permit offline-mode connections. Only connect bots to servers "
            "where you have permission."
        )
        foot.setObjectName("Description")
        foot.setWordWrap(True)
        outer.addWidget(foot)

    def _load_pocketterm_icon(self):
        main_file = find_main_app_file()
        if not main_file:
            self._log("PocketTerm main source not found; using the default application icon.")
            return
        try:
            spec = importlib.util.spec_from_file_location("pocketterm_icon_runtime", main_file)
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not load PocketTerm main module.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            icon_path, status = module.find_icon()
            if icon_path:
                icon = QIcon(str(icon_path))
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    app = QApplication.instance()
                    if app:
                        app.setWindowIcon(icon)
                    self._log(f"PocketTerm icon loaded: {icon_path}")
                else:
                    self._log(f"PocketTerm icon could not be read: {icon_path}")
            else:
                self._log(f"PocketTerm icon not found: {status}")
        except Exception as exc:
            self._log(f"Could not load PocketTerm icon: {exc}")

    def _find_node(self):
        here = Path(__file__).resolve().parent
        candidates = [
            here / "node.exe",
            here / "Node" / "node.exe",
            here / "node" / "node.exe",
        ]

        # Search the PocketTerm/GitPortable tree without hardcoding a drive letter.
        # Git for Windows installations may keep node.exe beside its other tools,
        # while separate portable Node distributions use their own folder.
        for folder in (here, *here.parents):
            portable_roots = (
                folder / "GitPortable",
                folder,
            )
            for root in portable_roots:
                candidates.extend([
                    root / "App" / "Git" / "bin" / "node.exe",
                    root / "App" / "Git" / "usr" / "bin" / "node.exe",
                    root / "App" / "Node" / "node.exe",
                    root / "App" / "nodejs" / "node.exe",
                    root / "Node" / "node.exe",
                    root / "nodejs" / "node.exe",
                ])

        # Remove duplicate paths while preserving search order.
        seen = set()
        unique_candidates = []
        for candidate in candidates:
            key = str(candidate).casefold()
            if key not in seen:
                seen.add(key)
                unique_candidates.append(candidate)

        found = next((p for p in unique_candidates if p.is_file()), None)
        if found:
            self.node = str(found)
            self.node_status.setText(f"Node.js: {self.node}")
            self._log(f"Using portable Node.js: {self.node}")
        else:
            self.node = shutil.which("node")
            if self.node:
                self.node_status.setText(f"Node.js: {self.node}")
                self._log(f"Using Node.js from PATH: {self.node}")
            else:
                self.node_status.setText(
                    "Node.js not found — expected it inside GitPortable."
                )

    def _log(self, message):
        self.log.appendPlainText(message)

    def _set_connected(self, connected):
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        self.host.setEnabled(not connected)
        self.port.setEnabled(not connected)
        self.version.setEnabled(not connected)
        self.username.setEnabled(not connected)
        self.radius.setEnabled(not connected)
        self.status_text.setText("Connected" if connected else "Disconnected")
        self.status_text.setStyleSheet(f"color: {GREEN if connected else MUTED}; font-weight: 600;")

    def connect_bot(self):
        host = self.host.text().strip()
        username = self.username.text().strip()
        if not host or not username:
            QMessageBox.warning(self, "Missing information", "Enter a server address and bot username.")
            return
        if len(username) > 16 or not username.replace("_", "").isalnum():
            QMessageBox.warning(self, "Invalid username", "Use a Minecraft username with letters, numbers, and underscores (max 16 characters).")
            return
        if not self.node:
            QMessageBox.critical(self, "Node.js required",
                "Node.js was not found. Install Node.js or place node.exe in PocketTerm's portable Node folder.")
            return
        if not self.bot_script.is_file():
            QMessageBox.critical(self, "Missing bot engine",
                f"Could not find the companion file:\n{self.bot_script}")
            return

        version = self.version.currentText()
        args = [
            self.node, str(self.bot_script), host, str(self.port.value()),
            username, version, str(self.radius.value())
        ]
        self.process = QProcess(self)
        self.process.setProgram(args[0])
        self.process.setArguments(args[1:])
        self.process.setWorkingDirectory(str(self.bot_script.parent))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._process_finished)
        self._log(f"Starting bot: {username}@{host}:{self.port.value()} | version {version}")
        self._log("Authentication: offline mode")
        self.process.start()
        if not self.process.waitForStarted(3000):
            self._log(f"Could not start Node.js: {self.process.errorString()}")
            self._set_connected(False)
            self.process = None
            return
        self._set_connected(True)

    def _read_output(self):
        if self.process:
            data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if data:
                for line in data.rstrip().splitlines():
                    self._log(line)

    def _process_error(self, error):
        if self.process:
            self._log(f"Process error: {self.process.errorString()}")

    def _process_finished(self, exit_code, exit_status):
        self._read_output()
        self._log(f"Bot process ended (exit code {exit_code}).")
        self._set_connected(False)
        self.process = None

    def disconnect_bot(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self._log("Disconnect requested.")
            self.process.terminate()
            if not self.process.waitForFinished(2500):
                self.process.kill()
        self._set_connected(False)

    def closeEvent(self, event):
        self.disconnect_bot()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")
    window = MCServerBotWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
