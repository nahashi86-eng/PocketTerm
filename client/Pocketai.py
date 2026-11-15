"""PocketAi - native Groq chat window for PocketTerm (PySide6, same theme as the main app)."""
import json
import os
import re
import shutil
import subprocess
import sys
import ctypes
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QLineEdit, QPlainTextEdit, QScrollArea, QSizePolicy, QMessageBox,
)

SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent

# Same icon lookup as the main PocketTerm launcher
ICON_NAME = "icon.png"
ICON_DIRS = [APP_DIR / "Images", SCRIPT_DIR / "Images", SCRIPT_DIR.parent / "Images", Path.cwd() / "Images"]


def find_icon():
    """Return the launcher's icon path (Images/icon.png, or the first .png in an Images folder), or None."""
    seen = []
    for folder in ICON_DIRS:
        if folder in seen:
            continue
        seen.append(folder)
        if not folder.is_dir():
            continue
        if (folder / ICON_NAME).is_file():
            return folder / ICON_NAME
        pngs = sorted(folder.glob("*.png")) + sorted(folder.glob("*.PNG"))
        if pngs:
            return pngs[0]
    return None


# ---------- API key from the GitPortable terminal ----------
KEY_VAR = "GROQ_API_KEY"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_RC_FILES = (".bashrc", ".bash_profile", ".bash_login", ".profile")
_RC_LINE = re.compile(r"""^\s*(?:export\s+)?GROQ_API_KEY=(?P<val>"[^"]*"|'[^']*'|[^\s#]+)""")


def discover_portable_root():
    """Find the GitPortable root by walking upward from this script (same logic as PocketTerm)."""
    def is_root(f):
        return any((f / r).is_file() for r in ("GitBashPortable.exe", "App/Git/bin/bash.exe", "App/Git/cmd/git.exe"))
    for folder in (SCRIPT_DIR, *SCRIPT_DIR.parents):
        if is_root(folder):
            return folder
    for folder in (SCRIPT_DIR, *SCRIPT_DIR.parents):
        c = folder / "GitPortable"
        if any((c / r).is_file() for r in ("GitBashPortable.exe", "App/Git/bin/bash.exe")):
            return c
    return APP_DIR


def find_bash(root):
    """bash.exe: PocketTerm's custom setting, the portable bundle, or PATH."""
    try:
        raw = json.loads((APP_DIR / "Config" / "settings.json").read_text(encoding="utf-8")).get("bash_path", "")
    except (OSError, ValueError, AttributeError):
        raw = ""
    raw = str(raw or "").strip().strip('"')
    if raw:
        p = Path(raw)
        for cand in ([p] if p.is_absolute() else [root / p, APP_DIR / p]):
            if cand.is_file():
                return cand
    for rel in ("App/Git/bin/bash.exe", "App/Git/usr/bin/bash.exe", "App/git/bin/bash.exe", "bin/bash.exe"):
        if (root / rel).is_file():
            return root / rel
    w = shutil.which("bash.exe") or shutil.which("bash")
    return Path(w) if w else None


def key_from_rc_files(home):
    """Read `export GROQ_API_KEY=...` from the Git Bash startup files. The last assignment wins."""
    found = ""
    for name in _RC_FILES:
        try:
            lines = (home / name).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _RC_LINE.match(line)
            if m:
                val = m.group("val").strip("\"'")
                if val and "$" not in val and "`" not in val:  # skip values that need a shell to expand
                    found = val
    return found


def key_from_bash(bash, home):
    """Ask a real Git Bash login shell for the variable (handles keys set by scripts or expansions)."""
    env = dict(os.environ)
    if home.is_dir():
        env["HOME"] = str(home)
    marker = "__POCKETAI_KEY__"
    try:
        r = subprocess.run([str(bash), "-l", "-i", "-c", f'printf "{marker}%s{marker}" "${KEY_VAR}"'],
                           capture_output=True, text=True, errors="replace", timeout=20, env=env,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(marker + r"(.*?)" + marker, r.stdout, re.S)
    return m.group(1).strip() if m else ""


def load_key():
    """Return (key, source): the environment first, then Git Bash startup files, then a Git Bash login shell."""
    key = os.environ.get(KEY_VAR, "").strip()
    if key:
        return key, "the environment"
    root = discover_portable_root()
    home = root / "Data" / "home"
    key = key_from_rc_files(home)
    if key:
        return key, "your Git Bash startup file"
    bash = find_bash(root)
    if bash:
        key = key_from_bash(bash, home)
        if key:
            return key, "Git Bash"
    return "", ""


DEFAULT_MODEL = "qwen/qwen3.8-27b"
TEMPERATURE = 0.6
TOP_P = 0.95
MAX_TOKENS = 2048
REASONING_EFFORT = "none"

# ---------- theme (matches PocketTerm's Violet palette) ----------
ACCENT, STRONG, SOFT, LIGHT, LIGHTER = "#8b5cf6", "#7c3aed", "#a78bfa", "#c4b5fd", "#ddd6fe"
BORDER, HOVERBORDER = "#3b2a66", "#5b3fb5"
TEXT, TEXT_BODY, TEXT_MUTED, TEXT_FAINT = "#f3f0ff", "#d9d3f2", "#a69fc4", "#857ca8"
OK_COLOR, ERR_COLOR = "#6ee7b7", "#fda4af"

STYLE = """
QMainWindow, QWidget { background-color: #08070d; color: @TEXT_BODY@; font-family: 'Segoe UI'; }
QToolTip { background-color: #1e1535; color: @TEXT@; border: 1px solid @HOVERBORDER@; padding: 4px 8px; }
QLabel { background: transparent; }
QLabel#Logo { font-family: 'Consolas'; font-size: 26px; font-weight: 700; color: @TEXT@; }
QLabel#PageTitle { font-size: 24px; font-weight: 700; color: @TEXT@; }
QLabel#Description, QLabel#Muted { color: @TEXT_MUTED@; font-size: 12px; }
QLabel#Status { color: @SOFT@; font-weight: bold; }
QFrame#Card { background-color: #130e21; border: 1px solid #2a1f45; border-radius: 12px; }
QFrame#UserCard { background-color: #1e1535; border: 1px solid @HOVERBORDER@; border-radius: 12px; }

QPushButton { background-color: #1a1230; color: #e4dffa; border: 1px solid #2a1f45; border-radius: 8px;
    padding: 10px 15px; font-size: 12px; }
QPushButton:hover { background-color: #2a1d4d; border: 1px solid @HOVERBORDER@; }
QPushButton:disabled { background-color: #120d20; color: #6f6590; border: 1px solid #1f1736; }
QPushButton#Primary { background-color: @STRONG@; color: #ffffff; border: none; font-weight: bold; }
QPushButton#Primary:hover { background-color: @ACCENT@; }
QPushButton#Primary:disabled { background-color: #2a1d4d; color: #8f84b3; }
QPushButton#Danger { background-color: #2a1220; border: 1px solid #5b1f3a; color: @ERR@; }
QPushButton#Danger:hover { background-color: #3d1730; border: 1px solid #be3a68; }

QLineEdit, QPlainTextEdit#Input { background-color: #0e0a18; color: @TEXT@; border: 1px solid #2a1f45;
    border-radius: 8px; font-size: 12px; selection-background-color: @STRONG@; selection-color: #ffffff;
    placeholder-text-color: #7d739c; }
QLineEdit { padding: 9px 12px; }
QPlainTextEdit#Input { padding: 9px 12px; font-size: 14px; }
QLineEdit:focus, QPlainTextEdit#Input:focus { border: 1px solid @ACCENT@; }

QScrollArea { border: none; }
QScrollBar:vertical { background: #08070d; width: 10px; }
QScrollBar::handle:vertical { background: @BORDER@; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: @HOVERBORDER@; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
"""


def build_style():
    style = STYLE
    for token, value in (("@ACCENT@", ACCENT), ("@STRONG@", STRONG), ("@SOFT@", SOFT), ("@BORDER@", BORDER),
                         ("@HOVERBORDER@", HOVERBORDER), ("@TEXT_BODY@", TEXT_BODY), ("@TEXT_MUTED@", TEXT_MUTED),
                         ("@TEXT@", TEXT), ("@ERR@", ERR_COLOR)):
        style = style.replace(token, value)
    return style


# ---------- background worker: streams one reply from Groq ----------
class ChatWorker(QThread):
    chunk = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key, model, messages, parent=None):
        super().__init__(parent)
        self.api_key, self.model, self.messages = api_key, model, messages
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            from groq import Groq
        except ImportError:
            self.failed.emit("The 'groq' package isn't installed. Run:  pip install groq")
            return
        stream = None
        try:
            client = Groq(api_key=self.api_key)
            stream = client.chat.completions.create(
                model=self.model, messages=self.messages, temperature=TEMPERATURE,
                max_completion_tokens=MAX_TOKENS, top_p=TOP_P, reasoning_effort=REASONING_EFFORT, stream=True)
            for part in stream:
                if self._stop:
                    break
                if not part.choices:
                    continue
                delta = part.choices[0].delta.content or ""
                if delta:
                    self.chunk.emit(delta)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


class KeyLoader(QThread):
    """Looks for the API key in the background so the window opens instantly."""
    loaded = Signal(str, str)

    def run(self):
        try:
            key, source = load_key()
        except Exception:
            key, source = "", ""
        self.loaded.emit(key, source)


# ---------- widgets ----------
class ChatInput(QPlainTextEdit):
    """Enter sends, Shift+Enter inserts a new line."""
    submit = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            event.accept()
            self.submit.emit()
            return
        super().keyPressEvent(event)


class MessageBubble(QFrame):
    def __init__(self, role):
        super().__init__()
        user = role == "user"
        self.setObjectName("UserCard" if user else "Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(6)
        title = QLabel("You" if user else "PocketAi")
        title.setStyleSheet(f"color: {LIGHT if user else OK_COLOR}; font-weight: 700; font-size: 12px;")
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.PlainText)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setStyleSheet(f"color: {TEXT_BODY}; font-size: 13px;")
        lay.addWidget(title)
        lay.addWidget(self.body)
        self.text = ""

    def set_text(self, text):
        self.text = text
        self.body.setText(text)

    def append(self, text):
        self.set_text(self.text + text)

    def show_error(self, message):
        self.body.setStyleSheet(f"color: {ERR_COLOR}; font-size: 13px; font-style: italic;")
        self.body.setText("Error: " + message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PocketAi")
        self.setStyleSheet(build_style())
        self.resize(1000, 720)
        self.setMinimumSize(640, 480)

        self.messages = []
        self.worker = None
        self.key_loader = None
        self.ai_bubble = None
        self._failed = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(30, 26, 30, 22)
        root.setSpacing(14)

        # header
        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("PocketAi")
        title.setObjectName("PageTitle")
        desc = QLabel("Chat with a model on Groq. Enter sends, Shift+Enter adds a new line.")
        desc.setObjectName("Description")
        header.addWidget(title)
        header.addWidget(desc)
        root.addLayout(header)

        # connection card
        card = QFrame()
        card.setObjectName("Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(10)
        row.addWidget(self._muted("API key"))
        self.key_edit = QLineEdit(os.environ.get(KEY_VAR, ""))
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("Paste gsk_... key (or it loads from your Git Bash GROQ_API_KEY)")
        self.key_edit.setMinimumWidth(260)
        row.addWidget(self.key_edit, stretch=2)
        self.show_btn = self._button("Show", self.toggle_key)
        row.addWidget(self.show_btn)
        git_btn = self._button("From Git Bash", lambda: self.load_key_from_terminal(True))
        git_btn.setToolTip("Reload GROQ_API_KEY from your GitPortable setup")
        row.addWidget(git_btn)
        row.addWidget(self._muted("Model"))
        self.model_edit = QLineEdit(DEFAULT_MODEL)
        self.model_edit.setMinimumWidth(180)
        row.addWidget(self.model_edit, stretch=1)
        row.addWidget(self._button("Clear Chat", self.clear_chat))
        root.addWidget(card)

        # chat area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, 12, 0)
        self.column = QWidget()
        self.column.setMaximumWidth(860)
        self.column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.col_layout = QVBoxLayout(self.column)
        self.col_layout.setContentsMargins(0, 0, 0, 0)
        self.col_layout.setSpacing(12)
        outer.addWidget(self.column, alignment=Qt.AlignHCenter)
        outer.addStretch(1)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, stretch=1)

        self.empty_label = QLabel("Say something to get started.")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setMinimumHeight(120)
        self.col_layout.addWidget(self.empty_label)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        root.addWidget(self.status)

        # input bar
        bar = QFrame()
        bar.setObjectName("Card")
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(14, 12, 14, 12)
        bar_l.setSpacing(10)
        self.input = ChatInput()
        self.input.setObjectName("Input")
        self.input.setPlaceholderText("Type a message...  (Enter to send, Shift+Enter for new line)")
        self.input.setFixedHeight(70)
        self.input.submit.connect(self.send_or_stop)
        bar_l.addWidget(self.input, stretch=1)
        self.send_btn = self._button("Send", self.send_or_stop, "Primary")
        self.send_btn.setMinimumWidth(96)
        self.send_btn.setMinimumHeight(70)
        bar_l.addWidget(self.send_btn)
        root.addWidget(bar)

        self.input.setFocus()
        if not self.key_edit.text().strip():
            QTimer.singleShot(200, self.load_key_from_terminal)

    # ----- helpers -----
    @staticmethod
    def _muted(text):
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    @staticmethod
    def _button(text, handler, object_name=None):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        if object_name:
            b.setObjectName(object_name)
        b.clicked.connect(lambda _=False: handler())
        return b

    def _set_send_mode(self, streaming):
        self.send_btn.setText("Stop" if streaming else "Send")
        self.send_btn.setObjectName("Danger" if streaming else "Primary")
        self.send_btn.setEnabled(True)
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)

    def _say(self, text, ok=None):
        self.status.setText(text)
        color = {True: OK_COLOR, False: ERR_COLOR}.get(ok)
        self.status.setStyleSheet(f"color: {color};" if color else "")

    def _scroll_bottom(self):
        QTimer.singleShot(15, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _add_bubble(self, role):
        if self.empty_label is not None:
            self.empty_label.hide()
        bubble = MessageBubble(role)
        self.col_layout.addWidget(bubble)
        self._scroll_bottom()
        return bubble

    # ----- API key from Git Bash -----
    def load_key_from_terminal(self, force=False):
        if self.key_loader is not None:
            return
        self._say(f"Looking for {KEY_VAR} in your Git Bash setup...")
        self.key_loader = KeyLoader(self)
        self.key_loader.loaded.connect(lambda key, source, f=force: self._on_key_loaded(key, source, f))
        self.key_loader.finished.connect(self._key_loader_done)
        self.key_loader.start()

    def _on_key_loaded(self, key, source, force):
        if not key:
            self._say(f"No {KEY_VAR} found in your Git Bash setup. Add  export {KEY_VAR}=...  to "
                      "Data/home/.bashrc, or paste the key above.", False)
        elif force or not self.key_edit.text().strip():
            self.key_edit.setText(key)
            self._say(f"API key loaded from {source}.", True)
        else:
            self._say("")

    def _key_loader_done(self):
        loader, self.key_loader = self.key_loader, None
        if loader is not None:
            loader.deleteLater()

    # ----- actions -----
    def toggle_key(self):
        hidden = self.key_edit.echoMode() == QLineEdit.Password
        self.key_edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        self.show_btn.setText("Hide" if hidden else "Show")

    def clear_chat(self):
        if self.worker is not None:
            self._say("Stop the current reply before clearing the chat.", False)
            return
        self.messages = []
        while self.col_layout.count():
            widget = self.col_layout.takeAt(0).widget()
            if widget is not None and widget is not self.empty_label:
                widget.deleteLater()
        self.col_layout.addWidget(self.empty_label)
        self.empty_label.show()
        self._say("")

    def send_or_stop(self):
        if self.worker is not None:
            self.worker.stop()
            self.send_btn.setText("Stopping...")
            self.send_btn.setEnabled(False)
            return
        self.send_message()

    def send_message(self):
        api_key = self.key_edit.text().strip()
        model = self.model_edit.text().strip() or DEFAULT_MODEL
        text = self.input.toPlainText().strip()
        if not api_key:
            QMessageBox.warning(self, "API key needed", "Please enter your Groq API key first.")
            return
        if not text:
            return

        self.input.clear()
        self._add_bubble("user").set_text(text)
        self.messages.append({"role": "user", "content": text})
        self.ai_bubble = self._add_bubble("ai")
        self._failed = False

        self.worker = ChatWorker(api_key, model, list(self.messages), self)
        self.worker.chunk.connect(self._on_chunk)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_done)
        self._set_send_mode(True)
        self._say("Thinking...")
        self.worker.start()

    def _on_chunk(self, text):
        if self.ai_bubble is not None:
            self.ai_bubble.append(text)
            self._say("Streaming...")
            self._scroll_bottom()

    def _on_failed(self, message):
        self._failed = True
        if self.ai_bubble is not None:
            self.ai_bubble.show_error(message)
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()  # the failed prompt isn't part of the history
        self._say("The request failed.", False)

    def _on_done(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        if not self._failed:
            reply = self.ai_bubble.text if self.ai_bubble is not None else ""
            if reply:
                self.messages.append({"role": "assistant", "content": reply})
                self._say("")
            else:
                if self.messages and self.messages[-1]["role"] == "user":
                    self.messages.pop()
                if self.ai_bubble is not None:
                    self.ai_bubble.set_text("(stopped)")
                self._say("Stopped.")
        self.ai_bubble = None
        self._set_send_mode(False)
        self.input.setFocus()
        self._scroll_bottom()

    def closeEvent(self, event):
        if self.key_loader is not None and not self.key_loader.wait(500):
            self.key_loader.terminate()
            self.key_loader.wait(500)
        if self.worker is not None:
            self.worker.stop()
            if not self.worker.wait(3000):
                self.worker.terminate()
                self.worker.wait(1000)
        super().closeEvent(event)


if __name__ == "__main__":
    if os.name == "nt":
        try:  # own taskbar identity instead of python.exe's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PocketTerm.PocketAi")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app_icon = None
    icon_path = find_icon()
    if icon_path:
        candidate = QIcon(str(icon_path))
        if not candidate.isNull():
            app_icon = candidate
            app.setWindowIcon(app_icon)
    window = MainWindow()
    if app_icon:
        window.setWindowIcon(app_icon)
    window.show()
    sys.exit(app.exec())