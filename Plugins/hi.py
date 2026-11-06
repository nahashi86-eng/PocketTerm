import sys
import os
import re
import copy
import csv
import json
import shlex
import shutil
import logging
import threading
import time
import socket
import subprocess
import ctypes
import tempfile
import importlib.util
import urllib.request
import urllib.error
import urllib.parse
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

try:
    from ctypes import wintypes
except (ImportError, ValueError):  # non-Windows Python: the app only runs on Windows anyway
    wintypes = None

from PySide6.QtCore import (
    Qt, QTimer, QEvent, QProcess, QProcessEnvironment, Signal, QRect, QSize, QPointF,
    QStorageInfo, QUrl, QRectF,
)
from PySide6.QtGui import (
    QIcon, QColor, QKeySequence, QShortcut, QTextCursor, QAction,
    QPixmap, QPainter, QPen, QFont, QFontMetrics, QTextCharFormat, QDesktopServices,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QFrame,
    QStackedWidget,
    QButtonGroup,
    QLineEdit,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QTabWidget,
    QTabBar,
    QPlainTextEdit,
    QMessageBox,
    QColorDialog,
    QSystemTrayIcon,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QProgressBar,
    QSpinBox,
    QDialog,
    QSplitter,
    QSizeGrip,
    QTreeView,
    QFileSystemModel,
    QTreeWidget,
    QTreeWidgetItem,
)

# The built-in web view needs the QtWebEngine part of PySide6 (included with a normal
# `pip install PySide6`). If it is missing the Web page explains how to get it.
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage
    WEBVIEW_OK = True
except ImportError:
    QWebEngineView = None
    QWebEnginePage = None
    WEBVIEW_OK = False

# ============================================================
# CONFIGURATION
# ============================================================

# PocketTerm's own data directory. Keep this relative to the script so the
# existing Data/home layout continues to work when the USB drive letter changes.
SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent


def _discover_portable_root():
    """Find the USB root by walking upward from this script; never assume E:, F:, etc."""
    for folder in (SCRIPT_DIR, *SCRIPT_DIR.parents):
        if (
            (folder / "GitBashPortable.exe").is_file()
            or (folder / "App" / "Git" / "bin" / "bash.exe").is_file()
            or (folder / "App" / "Git" / "cmd" / "git.exe").is_file()
        ):
            return folder
    # A useful fallback for layouts where GitPortable is stored as a child folder.
    for folder in (SCRIPT_DIR, *SCRIPT_DIR.parents):
        candidate = folder / "GitPortable"
        if (
            (candidate / "GitBashPortable.exe").is_file()
            or (candidate / "App" / "Git" / "bin" / "bash.exe").is_file()
        ):
            return candidate
    return APP_DIR


PORTABLE_ROOT = _discover_portable_root()


def resolve_configured_path(value, *, relative_to_root=True):
    """Resolve saved paths, including old absolute paths from a moved USB drive."""
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None

    raw = os.path.expandvars(os.path.expanduser(raw))
    path = Path(raw)

    if path.is_absolute():
        if path.exists():
            return path

        # Migrate old settings such as E:\Assets\GitPortable\Tools\x.exe
        # by keeping the part below the detected portable root folder name.
        parts = list(path.parts)
        for index, part in enumerate(parts):
            if part.lower() == PORTABLE_ROOT.name.lower() and index < len(parts) - 1:
                rebased = PORTABLE_ROOT.joinpath(*parts[index + 1:])
                if rebased.exists():
                    return rebased
        return path

    roots = (PORTABLE_ROOT, APP_DIR) if relative_to_root else (APP_DIR, PORTABLE_ROOT)
    for root in roots:
        candidate = root / path
        if candidate.exists():
            return candidate
    return roots[0] / path


def portable_setting_path(value):
    """Store paths inside the USB as relative paths so they survive drive changes."""
    raw = str(value or "").strip().strip('"')
    if not raw:
        return ""
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(PORTABLE_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


FOLDERS = ["Tools", "Scripts", "Config", "Temp", "Projects", "GitPortable", "Images", "Plugins"]
for folder in FOLDERS:
    (APP_DIR / folder).mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = APP_DIR / "Config" / "settings.json"
LOG_FILE = APP_DIR / "Config" / "pocketterm.log"
UI_ICON_DIR = APP_DIR / "Temp" / "ui"
PROCESS_MANAGER_FILE = SCRIPT_DIR / "ProcessManager.py"


def setup_logging():
    logger = logging.getLogger("pocketterm")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            handler = RotatingFileHandler(
                LOG_FILE, maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


log = setup_logging()

# The app tries icon.png first, then falls back to the first .png it finds.
ICON_NAME = "icon.png"
ICON_DIRS = [
    APP_DIR / "Images",
    Path(__file__).resolve().parent / "Images",
    Path(__file__).resolve().parent.parent / "Images",
    Path.cwd() / "Images",
]
ICON_STATUS = "Icon: not checked yet."

# Used when no custom launcher path is set in Settings. First existing file wins.
LAUNCHER_CANDIDATES = [
    PORTABLE_ROOT / "GitBashPortable.exe",
    PORTABLE_ROOT / "GitPortable" / "GitBashPortable.exe",
    APP_DIR / "GitPortable" / "GitBashPortable.exe",
]

SCRIPT_EXTS = (".bat", ".cmd", ".ps1", ".py", ".sh")
SCRIPT_TYPES = {
    ".py": "Python script",
    ".bat": "Batch script",
    ".cmd": "Batch script",
    ".ps1": "PowerShell script",
    ".sh": "Shell script",
}

# Page indices (must match the order pages are added to the stack)
(PAGE_TERMINAL, PAGE_VSCODE, PAGE_PROJECTS, PAGE_SCRIPTS, PAGE_SNIPPETS,
 PAGE_TOOLS, PAGE_DRIVES, PAGE_PORTS, PAGE_WEB, PAGE_SETTINGS,
 PAGE_DASHBOARD, PAGE_FILES, PAGE_PLUGINS) = range(13)
PAGE_COUNT = 13

PLUGIN_REPO_API = "https://api.github.com/repos/nahashi86-eng/PocketTerm/git/trees/main?recursive=1"
PLUGIN_RAW_BASE = "https://raw.githubusercontent.com/nahashi86-eng/PocketTerm/main/"
PLUGIN_REPO_URL = "https://github.com/nahashi86-eng/PocketTerm.git"

# Sidebar drop-down groups: (group name, [(label, page index), ...])
NAV_GROUPS = [
    ("Workspace",  [("Dashboard", PAGE_DASHBOARD), ("Terminal View", PAGE_TERMINAL),
                    ("VS Code", PAGE_VSCODE), ("Projects", PAGE_PROJECTS),
                    ("Files", PAGE_FILES), ("Snippets", PAGE_SNIPPETS)]),
    ("Automation", [("Scripts", PAGE_SCRIPTS), ("Tools", PAGE_TOOLS)]),
    ("Extensions", [("Plugins", PAGE_PLUGINS)]),
    ("System",     [("Drives", PAGE_DRIVES), ("Ports", PAGE_PORTS)]),
    ("AI & Web",   [("PocketAi", PAGE_WEB)]),
]

DEFAULT_WEB_URL = "http://localhost:8080"

DRIVE_REFRESH_MS = 15000  # how often the Drives page re-scans while it is open

MAX_RECENT = 10

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

# Global hotkey (Ctrl+Alt+P) to show/hide the window
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_P = 0x50
HOTKEY_ID = 0xB001
HOTKEY_TEXT = "Ctrl+Alt+P"

# Custom item-data roles used by the list rows (plain ints so they work on any PySide6 version)
ROLE_PATH = 256      # == Qt.UserRole
ROLE_TITLE = 257
ROLE_SUB = 258
ROLE_BADGE = 259
ROLE_KIND = 260
ROLE_PIN = 261
ROLE_PORT = 262


def find_icon():
    """Return (path or None, human-readable status)."""
    seen = []
    for folder in ICON_DIRS:
        if folder in seen:
            continue
        seen.append(folder)
        if not folder.is_dir():
            continue
        exact = folder / ICON_NAME
        if exact.is_file():
            return exact, f"Icon loaded from: {exact}"
        pngs = sorted(folder.glob("*.png")) + sorted(folder.glob("*.PNG"))
        if pngs:
            return pngs[0], f"Icon loaded from: {pngs[0]} (no {ICON_NAME} found, used this instead)"
    looked = "\n".join(str(f) for f in seen)
    return None, f"No icon found. Put {ICON_NAME} in one of:\n{looked}"


# ============================================================
# SETTINGS STORAGE
# ============================================================

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class Settings:
    DEFAULTS = {
        "launcher_path": "",
        "auto_start": True,
        "accent": "Violet",
        "custom_accent": "#8b5cf6",
        "remember_size": True,
        "window_size": [1100, 650],
        "last_page": 0,
        "nav_collapsed": [],
        # VS Code
        "vscode_command": "code",
        "vscode_args": "--new-window",
        "vscode_exe": "",
        "bash_path": "",
        "vscode_auto_start": False,
        # Git
        "git_path": "",
        # Projects
        "pinned_projects": [],
        "recent_projects": [],
        # Scripts: remembered arguments per script file name
        "script_args": {},
        # Tools page: programs that can be embedded
        "tools": [
            {"name": "Notepad", "path": "notepad.exe", "args": ""},
        ],
        # Behavior
        "minimize_to_tray": False,
        "hotkey_enabled": True,
        "confirm_close": True,
        # Drives
        "low_space_alerts": True,
        "low_space_gb": 10,
        # PocketAi tab: last address shown, and the command that is run (hidden) in Git Bash
        "web_url": "http://localhost:8080",
        "pocketai_command": "PocketAi",
        "pocketai_auto_start": True,
        # Snippets: commands that can be sent to the terminal
        "snippets": [
            {"name": "Git status", "command": "git status"},
        ],
        # Only plugins explicitly enabled by the user are executed.
        "enabled_plugins": [],
    }

    def __init__(self, path):
        self.path = path
        self.backup_path = path.with_name(path.name + ".bak")
        self.data = copy.deepcopy(self.DEFAULTS)
        self.load()

    @staticmethod
    def _clean(key, value, default):
        """Return a sanitised copy of value, or None if it can't be used."""
        if isinstance(default, bool):
            return value if isinstance(value, bool) else None
        if isinstance(default, int):
            return value if _is_int(value) else None
        if isinstance(default, str):
            return value if isinstance(value, str) else None
        if key == "window_size":
            if isinstance(value, list) and len(value) == 2 and all(_is_int(v) for v in value):
                return list(value)
            return None
        if key in ("pinned_projects", "recent_projects", "nav_collapsed", "enabled_plugins"):
            if not isinstance(value, list):
                return None
            return [v for v in value if isinstance(v, str)]
        if key == "script_args":
            if not isinstance(value, dict):
                return None
            return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
        if key == "snippets":
            if not isinstance(value, list):
                return None
            return [
                {"name": t["name"], "command": t["command"]}
                for t in value
                if isinstance(t, dict) and isinstance(t.get("name"), str)
                and isinstance(t.get("command"), str)
            ]
        if key == "tools":
            if not isinstance(value, list):
                return None
            out = []
            for t in value:
                if isinstance(t, dict) and isinstance(t.get("name"), str) \
                        and isinstance(t.get("path"), str):
                    args = t.get("args", "")
                    out.append({
                        "name": t["name"],
                        "path": t["path"],
                        "args": args if isinstance(args, str) else "",
                    })
            return out
        return copy.deepcopy(value) if isinstance(value, type(default)) else None

    def _merge(self, raw):
        """Copy known keys (with valid values) from raw. Returns how many were applied."""
        count = 0
        for key, default in self.DEFAULTS.items():
            if key not in raw:
                continue
            cleaned = self._clean(key, raw[key], default)
            if cleaned is not None:
                self.data[key] = cleaned
                count += 1
        return count

    def load(self):
        for candidate in (self.path, self.backup_path):
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as e:
                log.warning("Could not read settings from %s: %s", candidate, e)
                continue
            if isinstance(raw, dict):
                self._merge(raw)
                return

    def save(self):
        """Write atomically; store USB-local executable paths relative to the USB root."""
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            data_to_save = copy.deepcopy(self.data)
            for key in ("launcher_path", "bash_path", "git_path", "vscode_exe"):
                data_to_save[key] = portable_setting_path(data_to_save.get(key, ""))
            for tool in data_to_save.get("tools", []):
                if isinstance(tool, dict) and isinstance(tool.get("path"), str):
                    tool["path"] = portable_setting_path(tool["path"])
            tmp.write_text(json.dumps(data_to_save, indent=2), encoding="utf-8")
            if self.path.exists():
                try:
                    shutil.copy2(self.path, self.backup_path)
                except OSError as e:
                    log.warning("Could not back up settings: %s", e)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("Could not save settings: %s", e)

    def get(self, key):
        return self.data.get(key, self.DEFAULTS[key])

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def export_to(self, path):
        try:
            Path(path).write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError as e:
            return f"Couldn't write file: {e}"
        return None

    def import_from(self, path):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return f"Couldn't read file: {e}"
        if not isinstance(raw, dict):
            return "That file isn't a PocketTerm settings file."
        if self._merge(raw) == 0:
            return "No recognised settings found in that file."
        self.save()
        return None


# ============================================================
# GIT BASH / GIT / VS CODE / SCRIPT HELPERS
# ============================================================

def find_launcher(settings):
    custom = str(settings.get("launcher_path") or "").strip().strip('"')
    if custom:
        configured = resolve_configured_path(custom)
        if configured and configured.is_file():
            return configured

    for candidate in LAUNCHER_CANDIDATES:
        if candidate.is_file():
            return candidate

    # Return the expected location to make any error message useful.
    return LAUNCHER_CANDIDATES[0]


def find_bash(settings):
    """Locate bash.exe in the portable bundle, a custom path, or Windows PATH."""
    custom = str(settings.get("bash_path") or "").strip().strip('"')
    if custom:
        p = resolve_configured_path(custom)
        if p and p.is_file():
            return p

    launcher = find_launcher(settings)
    bases = [PORTABLE_ROOT, launcher.parent, launcher.parent.parent]
    seen = set()
    for base in bases:
        try:
            marker = str(base.resolve()).lower()
        except OSError:
            marker = str(base).lower()
        if marker in seen:
            continue
        seen.add(marker)
        for rel in ("App/Git/bin/bash.exe", "App/Git/usr/bin/bash.exe",
                    "App/git/bin/bash.exe", "bin/bash.exe"):
            candidate = base / rel
            if candidate.is_file():
                return candidate

    found = shutil.which("bash.exe") or shutil.which("bash")
    return Path(found) if found else None


def find_git(settings):
    """Locate git.exe: custom setting, then next to the portable Git Bash, then PATH."""
    custom = str(settings.get("git_path") or "").strip().strip('"')
    if custom:
        p = resolve_configured_path(custom)
        if p and p.is_file():
            return p

    launcher = find_launcher(settings)
    for base in (PORTABLE_ROOT, launcher.parent, launcher.parent.parent):
        for rel in ("App/Git/cmd/git.exe", "App/Git/bin/git.exe",
                    "App/Git/mingw64/bin/git.exe", "App/git/cmd/git.exe",
                    "cmd/git.exe", "bin/git.exe"):
            p = base / rel
            if p.is_file():
                return p

    bash = find_bash(settings)
    if bash:
        for base in (bash.parent.parent, bash.parent.parent.parent):
            for rel in ("cmd/git.exe", "bin/git.exe", "mingw64/bin/git.exe"):
                p = base / rel
                if p.is_file():
                    return p

    found = shutil.which("git")
    return Path(found) if found else None


def git_env(settings):
    """Environment for bash: point HOME at the portable home so your aliases load."""
    env = os.environ.copy()
    home = PORTABLE_ROOT / "Data" / "home"
    if home.is_dir():
        env["HOME"] = str(home)
    return env


def git_summary(git_exe, path):
    """
    Return (branch_text, status_text, kind) for a project folder,
    e.g. ("main \u21911", "\u25cf 3 changed", "warn"), or None if it isn't a repo.
    """
    if not (Path(path) / ".git").exists():
        return None
    try:
        r = subprocess.run(
            [str(git_exe), "-c", "safe.directory=*", "-C", str(path),
             "status", "--porcelain=v1", "-b"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    lines = r.stdout.splitlines()
    if not lines or not lines[0].startswith("## "):
        return None

    head = lines[0][3:]
    branch = head.split("...")[0].split(" [")[0]
    if branch.startswith("No commits yet on "):
        branch = branch[len("No commits yet on "):] + " (no commits)"
    elif branch.startswith("HEAD (no branch)"):
        branch = "detached HEAD"

    left = [branch]
    m = re.search(r"\[(.*?)\]", head)
    if m:
        info = m.group(1)
        ahead = re.search(r"ahead (\d+)", info)
        behind = re.search(r"behind (\d+)", info)
        if ahead:
            left.append(f"\u2191{ahead.group(1)}")
        if behind:
            left.append(f"\u2193{behind.group(1)}")

    branch_text = " ".join(left)
    changes = len(lines) - 1
    if changes:
        return branch_text, f"\u25cf {changes} changed", "warn"
    return branch_text, "clean", "ok"


def repo_name_from_url(url):
    u = url.strip().replace("\\", "/").rstrip("/")
    u = u.split("/")[-1].split(":")[-1]
    if u.lower().endswith(".git"):
        u = u[:-4]
    return u


def split_args(text):
    """Split an argument string into a list, keeping quoted groups together."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        tokens = shlex.split(text, posix=False)
    except ValueError:
        return text.split()
    out = []
    for t in tokens:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            t = t[1:-1]
        out.append(t)
    return out


def python_exe():
    """python.exe (not pythonw.exe) so scripts get working stdout/stderr."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        alt = exe.with_name("python.exe")
        if alt.is_file():
            return str(alt)
    return str(exe)


def kill_tree(pid):
    """Kill a process and all its children (Windows). Returns True on success."""
    if not pid or os.name != "nt":
        return False
    try:
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode == 0
    except OSError as e:
        log.warning("taskkill failed for pid %s: %s", pid, e)
        return False


def vscode_launch_plan(settings, target=None):
    """Return (args, popen_kwargs, description). args is None on error (description = reason)."""
    extra = str(settings.get("vscode_args")).strip()
    quiet = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    exe = str(settings.get("vscode_exe") or "").strip().strip('"')
    if exe:
        p = resolve_configured_path(exe)
        if not p or not p.is_file():
            return None, None, f"VS Code executable not found: {p or exe}"
        # split_args (not shlex in POSIX mode) so Windows backslashes survive
        args = [str(p)] + split_args(extra) + ([str(target)] if target else [])
        kwargs = dict(quiet, cwd=str(p.parent))
        return args, kwargs, f"Runs directly: {p}"

    bash = find_bash(settings)
    if not bash:
        return None, None, (
            "Git Bash (bash.exe) not found. Set its path below, "
            "or set the VS Code executable instead."
        )

    cmd = str(settings.get("vscode_command")).strip() or "code"
    line = cmd + (f" {extra}" if extra else "")
    if target:
        line += " " + shlex.quote(Path(target).as_posix())

    args = [str(bash), "-l", "-i", "-c", line]
    kwargs = dict(
        quiet,
        cwd=str(APP_DIR / "Projects"),
        env=git_env(settings),
        creationflags=CREATE_NO_WINDOW,
    )
    return args, kwargs, f"Runs '{cmd}' through Git Bash: {bash}"


def run_script(path, settings, args_text=""):
    """Run a script in its own console window. Returns an error string or None."""
    ext = path.suffix.lower()
    env = None
    extra = f" {args_text.strip()}" if args_text.strip() else ""
    if ext in (".bat", ".cmd"):
        cmd = f'cmd.exe /k ""{path}"{extra}"'
    elif ext == ".py":
        cmd = f'cmd.exe /k ""{python_exe()}" "{path}"{extra}"'
    elif ext == ".ps1":
        cmd = f'powershell.exe -NoExit -ExecutionPolicy Bypass -File "{path}"{extra}'
    elif ext == ".sh":
        bash = find_bash(settings)
        if not bash:
            return "Git Bash not found. Set its path in Settings."
        env = git_env(settings)
        cmd = f'cmd.exe /k ""{bash}" --login "{path.as_posix()}"{extra}"'
    else:
        return f"Don't know how to run {ext} files."

    try:
        subprocess.Popen(
            cmd, cwd=str(path.parent), env=env,
            creationflags=CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        )
    except OSError as e:
        return f"Failed to start: {e}"
    return None


def script_command(path, settings, args):
    """Build (program, argv, env_dict_or_None, error) for running a script with captured output."""
    ext = path.suffix.lower()
    if ext in (".bat", ".cmd"):
        return "cmd.exe", ["/c", str(path)] + args, None, None
    if ext == ".py":
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return python_exe(), ["-u", str(path)] + args, env, None
    if ext == ".ps1":
        return ("powershell.exe",
                ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)] + args,
                None, None)
    if ext == ".sh":
        bash = find_bash(settings)
        if not bash:
            return None, None, None, "Git Bash not found. Set its path in Settings."
        return str(bash), ["--login", path.as_posix()] + args, git_env(settings), None
    return None, None, None, f"Don't know how to run {ext} files."


def open_path(path):
    """Open a folder in the system file browser (creating it first if it doesn't exist)."""
    path = Path(path)
    try:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        log.warning("Could not open %s: %s", path, e)


def format_size(n):
    """Human-readable size, e.g. 123.4 GB."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


DRIVE_KINDS = {2: "Removable", 3: "Local disk", 4: "Network", 5: "CD/DVD", 6: "RAM disk"}


def drive_kind(root):
    if os.name != "nt":
        return ""
    try:
        return DRIVE_KINDS.get(ctypes.windll.kernel32.GetDriveTypeW(root), "")
    except Exception:
        return ""


def collect_drives():
    """Return a list of dicts (root, name, fs, kind, total, free) for every ready drive."""
    drives = []
    seen = set()
    for v in QStorageInfo.mountedVolumes():
        if not v.isValid() or not v.isReady():
            continue
        total = int(v.bytesTotal())
        if total <= 0:
            continue
        root = os.path.normpath(v.rootPath())
        if root in seen:
            continue
        seen.add(root)
        drives.append({
            "root": root,
            "name": v.name() or "",
            "fs": bytes(v.fileSystemType()).decode("utf-8", errors="replace"),
            "kind": drive_kind(root),
            "total": total,
            "free": int(v.bytesAvailable()),
        })
    drives.sort(key=lambda d: d["root"].lower())
    return drives


def normalize_url(text):
    """Turn what the user typed into a URL: '3000', ':3000', 'localhost:3000', full URLs."""
    t = (text or "").strip()
    if not t:
        return DEFAULT_WEB_URL
    if re.fullmatch(r"\d{2,5}", t):
        return f"http://localhost:{t}"
    if t.startswith(":"):
        return "http://localhost" + t
    if "://" not in t:
        return "http://" + t
    return t


def drive_of(path):
    """'E:' for a path on drive E, or '' if the path has no drive letter."""
    return os.path.splitdrive(str(path))[0].upper()


EJECT_SCRIPT = (
    "$i = (New-Object -comObject Shell.Application).Namespace(17).ParseName('{drive}'); "
    "if ($i -eq $null) {{ exit 2 }}; $i.InvokeVerb('Eject')"
)


def eject_drive_now(drive):
    """Ask Windows to eject a drive like 'E:'. Returns (ok, message)."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", EJECT_SCRIPT.format(drive=drive)],
            capture_output=True, text=True, errors="replace", timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Couldn't run the eject command: {e}"
    if r.returncode == 2:
        return False, f"Windows doesn't list {drive} as a drive that can be ejected."
    time.sleep(2.5)  # the eject verb returns immediately; give Windows a moment, then check
    if os.path.exists(drive + "\\"):
        return False, (
            f"{drive} is still there. Something may be using it: close programs that "
            "have files open on it and try again."
        )
    return True, f"Ejected {drive}. It's safe to unplug it."


def schedule_eject_after_exit(drive):
    """Start a detached PowerShell that waits for PocketTerm to exit, then ejects the drive."""
    script = "Start-Sleep -Seconds 4; " + EJECT_SCRIPT.format(drive=drive)
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            cwd=os.environ.get("SystemRoot") or os.environ.get("WINDIR") or os.getcwd(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=0x00000008 | CREATE_NEW_PROCESS_GROUP,  # DETACHED_PROCESS
        )
    except OSError as e:
        return f"Couldn't start the eject helper: {e}"
    return None


def parse_listening(netstat_text, names):
    """Parse `netstat -ano` output into listening TCP ports. Language independent."""
    ports = []
    seen = set()
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        # A listening socket has no remote peer, which looks the same in every language.
        if parts[2] not in ("0.0.0.0:0", "[::]:0", "*:*"):
            continue
        addr, _, port = parts[1].rpartition(":")
        if not (port.isdigit() and parts[4].isdigit()):
            continue
        key = (int(port), int(parts[4]))
        if key in seen:
            continue
        seen.add(key)
        ports.append({
            "port": key[0],
            "pid": key[1],
            "name": names.get(key[1], "unknown"),
            "addr": addr.strip("[]"),
        })
    ports.sort(key=lambda p: (p["port"], p["pid"]))
    return ports


def list_listening_ports():
    """Listening TCP ports with the owning program (Windows only)."""
    if os.name != "nt":
        return []
    net = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, errors="replace",
        timeout=20, creationflags=CREATE_NO_WINDOW,
    )
    names = {}
    try:
        t = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
            errors="replace", timeout=20, creationflags=CREATE_NO_WINDOW,
        )
        for row in csv.reader(t.stdout.splitlines()):
            if len(row) >= 2 and row[1].isdigit():
                names[int(row[1])] = row[0]
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("tasklist failed: %s", e)
    return parse_listening(net.stdout, names)


# ============================================================
# THEME
# ============================================================

# Text scale: one clear hierarchy instead of scattered lavenders.
TEXT = "#f3f0ff"         # titles and primary content
TEXT_BODY = "#d9d3f2"    # normal labels and list items
TEXT_MUTED = "#a69fc4"   # descriptions and hints
TEXT_FAINT = "#857ca8"   # subtitles
TEXT_PLACEHOLDER = "#7d739c"
OK_COLOR = "#6ee7b7"
ERR_COLOR = "#fda4af"
WARN_COLOR = "#fcd34d"
LOG_COLOR = "#e4dffa"

ACCENTS = {
    #            accent     strong     soft       light      lighter    border     hover-border
    "Violet":  ("#8b5cf6", "#7c3aed", "#a78bfa", "#c4b5fd", "#ddd6fe", "#3b2a66", "#5b3fb5"),
    "Magenta": ("#d946ef", "#c026d3", "#e879f9", "#f0abfc", "#f5d0fe", "#5b2a66", "#a03fb5"),
    "Indigo":  ("#6366f1", "#4f46e5", "#818cf8", "#a5b4fc", "#c7d2fe", "#2a2f66", "#3f4fb5"),
    "Emerald": ("#10b981", "#059669", "#34d399", "#6ee7b7", "#a7f3d0", "#1f4a3c", "#2f8f6f"),
}
ACCENT_CHOICES = list(ACCENTS.keys()) + ["Custom"]
BACKGROUND = "#08070d"

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def mix(c1, c2, t):
    """Blend two #rrggbb colors. t=0 gives c1, t=1 gives c2."""
    a = c1.lstrip("#")
    b = c2.lstrip("#")
    out = ""
    for i in (0, 2, 4):
        va = int(a[i:i + 2], 16)
        vb = int(b[i:i + 2], 16)
        out += f"{round(va * (1 - t) + vb * t):02x}"
    return "#" + out


def _channel(v):
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(color):
    c = color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(c1, c2):
    la, lb = luminance(c1), luminance(c2)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def on_color(bg):
    """Readable text color (white or near-black) for text drawn on top of bg."""
    return "#ffffff" if contrast(bg, "#ffffff") >= 3.5 else "#0b0816"


def custom_palette(base):
    return (
        base,
        mix(base, "#000000", 0.15),
        mix(base, "#ffffff", 0.20),
        mix(base, "#ffffff", 0.45),
        mix(base, "#ffffff", 0.65),
        mix(base, BACKGROUND, 0.72),
        mix(base, BACKGROUND, 0.40),
    )


def get_palette(settings):
    name = settings.get("accent")
    if name == "Custom":
        base = str(settings.get("custom_accent"))
        if not HEX_RE.fullmatch(base):
            base = ACCENTS["Violet"][0]
        return custom_palette(base)
    return ACCENTS.get(name, ACCENTS["Violet"])


def ui_icon(name, color, size, points, width):
    """Draw a small line icon (checkmark / arrow) to a PNG once and return its QSS path."""
    try:
        UI_ICON_DIR.mkdir(parents=True, exist_ok=True)
        path = UI_ICON_DIR / f"{name}_{color.lstrip('#')}.png"
        if not path.exists():
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(color), width)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline([QPointF(x, y) for x, y in points])
            painter.end()
            if not pm.save(str(path), "PNG"):
                return "none"
        return f'url("{path.as_posix()}")'
    except Exception as e:  # never let a cosmetic icon stop the app from starting
        log.warning("Could not create %s icon: %s", name, e)
        return "none"


STYLE_TEMPLATE = """
QMainWindow, QWidget {
    background-color: #08070d;
    color: @TEXT_BODY@;
    font-family: 'Segoe UI';
}

QToolTip {
    background-color: #1e1535;
    color: @TEXT@;
    border: 1px solid @HOVERBORDER@;
    padding: 4px 8px;
}

QLabel { background: transparent; }

QFrame#Sidebar {
    background-color: #0e0a18;
    border: none;
    border-right: 1px solid #231a3a;
}

QLabel#Logo { font-family: 'Consolas'; font-size: 26px; font-weight: 700; color: @TEXT@; }
QLabel#Subtitle { color: @TEXT_FAINT@; font-size: 9px; font-weight: 600; }
QLabel#PageTitle { font-size: 24px; font-weight: 700; color: @TEXT@; }
QLabel#Description { color: @TEXT_MUTED@; font-size: 12px; }
QLabel#SectionTitle { font-size: 15px; font-weight: 700; color: @LIGHTER@; }
QLabel#Muted { color: @TEXT_MUTED@; font-size: 12px; }
QLabel#Status { color: @SOFT@; font-weight: bold; }

QFrame#Card {
    background-color: #130e21;
    border: 1px solid #2a1f45;
    border-radius: 12px;
}

QFrame#TerminalFrame {
    background-color: #000000;
    border: 1px solid @BORDER@;
    border-radius: 8px;
}

QDialog#Palette {
    background-color: #130e21;
    border: 1px solid @HOVERBORDER@;
}

QPushButton {
    background-color: #1a1230;
    color: #e4dffa;
    border: 1px solid #2a1f45;
    border-radius: 8px;
    padding: 10px 15px;
    font-size: 12px;
}
QPushButton:hover { background-color: #2a1d4d; border: 1px solid @HOVERBORDER@; }
QPushButton:disabled { background-color: #120d20; color: #6f6590; border: 1px solid #1f1736; }

QPushButton#Navigation {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    color: #b5acd4;
    text-align: left;
    padding: 9px 16px;
    font-size: 13px;
}
QPushButton#Navigation:hover {
    background-color: #181129;
    border: 1px solid transparent;
    color: @TEXT@;
}
QPushButton#Navigation:checked {
    background-color: #1e1535;
    border: 1px solid @HOVERBORDER@;
    border-left: 3px solid @ACCENT@;
    color: @LIGHTER@;
    font-weight: 600;
}

QPushButton#NavGroup {
    background-color: transparent;
    border: none;
    border-radius: 8px;
    color: @TEXT_FAINT@;
    text-align: left;
    padding: 8px 10px;
    font-size: 10px;
    font-weight: 700;
}
QPushButton#NavGroup:hover { background-color: #181129; color: @TEXT@; }
QFrame#NavGroupBody { background: transparent; border: none; }

QWidget#DevToolsPanel { background-color: #130e21; border: 1px solid @ACCENT@; }
QWidget#DevBar { background-color: #1e1535; }
QLabel#DevTitle { color: @LIGHTER@; font-weight: 700; font-size: 11px; }

QPushButton#Primary { background-color: @STRONG@; color: @ON_STRONG@; border: none; font-weight: bold; }
QPushButton#Primary:hover { background-color: @ACCENT@; color: @ON_ACCENT@; }

QPushButton#CornerButton {
    background-color: @STRONG@;
    color: @ON_STRONG@;
    border: none;
    border-radius: 6px;
    padding: 5px 12px;
    margin: 0px 0px 6px 6px;
    font-weight: bold;
}
QPushButton#CornerButton:hover { background-color: @ACCENT@; color: @ON_ACCENT@; }

QPushButton#Danger { background-color: #2a1220; border: 1px solid #5b1f3a; color: @ERR@; }
QPushButton#Danger:hover { background-color: #3d1730; border: 1px solid #be3a68; }

QPushButton#TabClose {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 0px;
    font-size: 15px;
    color: @TEXT_MUTED@;
}
QPushButton#TabClose:hover { background-color: #3d1730; color: @ERR@; }

QLineEdit {
    background-color: #0e0a18;
    color: @TEXT@;
    border: 1px solid #2a1f45;
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 12px;
    selection-background-color: @STRONG@;
    selection-color: @ON_STRONG@;
    placeholder-text-color: @TEXT_PLACEHOLDER@;
}
QLineEdit:focus { border: 1px solid @ACCENT@; }

QPlainTextEdit#Log {
    background-color: #0e0a18;
    border: 1px solid #2a1f45;
    border-radius: 8px;
    padding: 6px;
    font-family: 'Consolas';
    font-size: 12px;
    color: @LOG@;
    selection-background-color: @STRONG@;
    selection-color: @ON_STRONG@;
}

QComboBox {
    background-color: #0e0a18;
    color: @TEXT@;
    border: 1px solid #2a1f45;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
    min-width: 140px;
}
QComboBox:hover, QComboBox:focus { border: 1px solid @ACCENT@; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 26px;
}
QComboBox::down-arrow { image: @ARROW@; width: 10px; height: 10px; }
QComboBox QAbstractItemView {
    background-color: #130e21;
    border: 1px solid #2a1f45;
    selection-background-color: @STRONG@;
    selection-color: @ON_STRONG@;
    color: @TEXT@;
    outline: 0;
}

QListWidget {
    background-color: #0e0a18;
    border: 1px solid #2a1f45;
    border-radius: 8px;
    padding: 6px;
    font-size: 13px;
    outline: 0;
}
QListWidget::item { padding: 9px 10px; border-radius: 6px; }
QListWidget::item:hover { background-color: #181129; }
QListWidget::item:selected { background-color: @STRONG@; color: @ON_STRONG@; }

QTreeWidget {
    background-color: #0e0a18;
    alternate-background-color: #130e21;
    border: 1px solid #2a1f45;
    border-radius: 8px;
    padding: 5px;
    outline: 0;
}
QTreeWidget::item { padding: 5px 4px; border-radius: 4px; }
QTreeWidget::item:hover { background-color: #181129; }
QTreeWidget::item:selected { background-color: @STRONG@; color: @ON_STRONG@; }
QHeaderView::section {
    background-color: #171126;
    color: @TEXT_MUTED@;
    border: none;
    border-bottom: 1px solid #2a1f45;
    padding: 6px 8px;
    font-weight: bold;
}

QTabWidget#Tabs::pane {
    border: 1px solid @BORDER@;
    border-radius: 8px;
    background: #000000;
    top: 0px;
    padding: 4px;
}
QTabBar { background: transparent; }
QTabBar::tab { margin-bottom: 6px; }
QTabBar::tab {
    background-color: #130e21;
    color: @TEXT_MUTED@;
    border: 1px solid #2a1f45;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 12px;
    margin-right: 3px;
    margin-bottom: 6px;
    font-size: 12px;
}
QTabBar::tab:selected { background-color: #1e1535; color: @LIGHTER@; border-color: @HOVERBORDER@; }
QTabBar::tab:hover:!selected { background-color: #181129; color: @TEXT@; }

QMenu {
    background-color: #130e21;
    border: 1px solid #2a1f45;
    padding: 4px;
}
QMenu::item { padding: 7px 22px; border-radius: 6px; }
QMenu::item:selected { background-color: @STRONG@; color: @ON_STRONG@; }
QMenu::separator { height: 1px; background: #2a1f45; margin: 4px 8px; }

QCheckBox { background: transparent; spacing: 10px; font-size: 12px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid @BORDER@;
    border-radius: 4px;
    background: #0e0a18;
}
QCheckBox::indicator:hover { border: 1px solid @ACCENT@; }
QCheckBox::indicator:checked { background: @ACCENT@; border: 1px solid @ACCENT@; image: @CHECK@; }

QSpinBox {
    background-color: #0e0a18;
    color: @TEXT@;
    border: 1px solid #2a1f45;
    border-radius: 8px;
    padding: 7px 10px;
    min-width: 80px;
    selection-background-color: @STRONG@;
    selection-color: @ON_STRONG@;
}
QSpinBox:focus { border: 1px solid @ACCENT@; }

QProgressBar {
    background-color: #0e0a18;
    border: 1px solid #2a1f45;
    border-radius: 6px;
}
QProgressBar::chunk { background-color: @ACCENT@; border-radius: 5px; }

QSplitter::handle { background: transparent; }
QSplitter::handle:vertical { height: 8px; }

QScrollArea { border: none; }
QScrollBar:vertical { background: #08070d; width: 10px; }
QScrollBar::handle:vertical { background: @BORDER@; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: @HOVERBORDER@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background: #08070d; height: 10px; }
QScrollBar::handle:horizontal { background: @BORDER@; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: @HOVERBORDER@; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
"""


def build_style(settings):
    accent, strong, soft, light, lighter, border, hover_border = get_palette(settings)
    check = ui_icon("check", on_color(accent), 16,
                    [(3.5, 8.5), (6.5, 11.5), (12.0, 5.0)], 2.2)
    arrow = ui_icon("arrow", TEXT_MUTED, 10,
                    [(2.0, 3.5), (5.0, 6.5), (8.0, 3.5)], 1.6)
    style = STYLE_TEMPLATE
    for token, value in (
        ("@ACCENT@", accent), ("@STRONG@", strong), ("@SOFT@", soft),
        ("@LIGHT@", light), ("@LIGHTER@", lighter),
        ("@BORDER@", border), ("@HOVERBORDER@", hover_border),
        ("@ON_STRONG@", on_color(strong)), ("@ON_ACCENT@", on_color(accent)),
        ("@TEXT_BODY@", TEXT_BODY), ("@TEXT_MUTED@", TEXT_MUTED),
        ("@TEXT_FAINT@", TEXT_FAINT), ("@TEXT_PLACEHOLDER@", TEXT_PLACEHOLDER),
        ("@TEXT@", TEXT), ("@ERR@", ERR_COLOR), ("@LOG@", LOG_COLOR),
        ("@CHECK@", check), ("@ARROW@", arrow),
    ):
        style = style.replace(token, value)
    return style


# ============================================================
# LIST ROW DELEGATE (used by Projects and Scripts)
# ============================================================

class RowDelegate(QStyledItemDelegate):
    """Paints a row as: title + muted subtitle on the left, a colored badge on the right."""

    ROW_H = 54

    BADGES = {
        "ok": ("#12352a", OK_COLOR),
        "warn": ("#3a2d0f", WARN_COLOR),
        "info": ("#231a3a", "#c4b5fd"),
        "muted": ("#1a1230", TEXT_MUTED),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strong = "#7c3aed"
        self.on_strong = "#ffffff"

    def set_colors(self, strong, on_strong):
        self.strong = strong
        self.on_strong = on_strong

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)

        if selected:
            bg = QColor(self.strong)
        elif hover:
            bg = QColor("#181129")
        else:
            bg = QColor("#130e21")
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 8, 8)

        title = str(index.data(ROLE_TITLE) or "")
        sub = str(index.data(ROLE_SUB) or "")
        badge = str(index.data(ROLE_BADGE) or "")
        kind = str(index.data(ROLE_KIND) or "muted")
        if index.data(ROLE_PIN):
            title = "\u2605 " + title

        title_font = QFont(option.font)
        title_font.setPointSizeF(10.5)
        title_font.setBold(True)
        sub_font = QFont(option.font)
        sub_font.setPointSizeF(8.5)
        badge_font = QFont(option.font)
        badge_font.setPointSizeF(8.5)
        badge_font.setBold(True)

        right_edge = rect.right() - 12
        if badge:
            bfm = QFontMetrics(badge_font)
            bw = bfm.horizontalAdvance(badge) + 18
            brect = QRect(right_edge - bw, rect.center().y() - 11, bw, 22)
            if selected:
                badge_bg = QColor(0, 0, 0, 70)
                badge_fg = QColor(self.on_strong)
            else:
                bg_hex, fg_hex = self.BADGES.get(kind, self.BADGES["muted"])
                badge_bg, badge_fg = QColor(bg_hex), QColor(fg_hex)
            painter.setPen(Qt.NoPen)
            painter.setBrush(badge_bg)
            painter.drawRoundedRect(brect, 11, 11)
            painter.setFont(badge_font)
            painter.setPen(badge_fg)
            painter.drawText(brect, Qt.AlignCenter, badge)
            right_edge = brect.left() - 12

        left = rect.left() + 14
        text_w = max(40, right_edge - left)

        painter.setFont(title_font)
        painter.setPen(QColor(self.on_strong if selected else TEXT))
        shown = QFontMetrics(title_font).elidedText(title, Qt.ElideRight, text_w)
        painter.drawText(QRect(left, rect.top() + 7, text_w, 20),
                         Qt.AlignLeft | Qt.AlignVCenter, shown)

        if sub:
            painter.setFont(sub_font)
            painter.setPen(QColor(self.on_strong if selected else TEXT_MUTED))
            shown = QFontMetrics(sub_font).elidedText(sub, Qt.ElideRight, text_w)
            painter.drawText(QRect(left, rect.top() + 28, text_w, 16),
                             Qt.AlignLeft | Qt.AlignVCenter, shown)

        painter.restore()


# ============================================================
# COMMAND PALETTE (Ctrl+K)
# ============================================================

class CommandPalette(QDialog):
    """Type to filter, Up/Down to move, Enter to run."""

    def __init__(self, parent, commands):
        super().__init__(parent)
        self.setObjectName("Palette")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.resize(580, 400)
        self.commands = commands  # list of (label, callback)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Type a command, project or script...")
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run)
        self.edit.installEventFilter(self)
        layout.addWidget(self.edit)

        self.list = QListWidget()
        self.list.itemClicked.connect(lambda _item: self._run())
        layout.addWidget(self.list, stretch=1)

        self._filter("")

    def _filter(self, text):
        tokens = text.lower().split()
        self.list.clear()
        for i, (label, _cb) in enumerate(self.commands):
            low = label.lower()
            if all(t in low for t in tokens):
                item = QListWidgetItem(label)
                item.setData(ROLE_PATH, i)
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _move(self, step):
        count = self.list.count()
        if not count:
            return
        row = max(0, min(count - 1, self.list.currentRow() + step))
        self.list.setCurrentRow(row)

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Down:
                self._move(1)
                return True
            if event.key() == Qt.Key_Up:
                self._move(-1)
                return True
        return super().eventFilter(obj, event)

    def _run(self):
        item = self.list.currentItem()
        if item is None:
            return
        callback = self.commands[item.data(ROLE_PATH)][1]
        self.accept()
        QTimer.singleShot(0, callback)

    def open_centered(self):
        parent = self.parentWidget()
        if parent is not None:
            g = parent.frameGeometry()
            self.move(g.center().x() - self.width() // 2, g.top() + 90)
        self.edit.setFocus()
        self.exec()


# ============================================================
# EMBEDDED EXTERNAL WINDOWS (BASE CLASS + TERMINAL + VS CODE + TOOLS)
# ============================================================

class EmbeddedWindow(QWidget):
    """
    Launches an external program and embeds its top-level window into this widget.
    Subclasses define how to launch it and how to recognise its window.
    """

    NAME = "Application"
    TIMEOUT_MS = 15000
    TIMEOUT_MESSAGE = "Started, but no window was captured."
    IDLE_MESSAGE = "Nothing is running here yet."

    GWL_STYLE = -16

    WS_POPUP = 0x80000000
    WS_CHILD = 0x40000000
    WS_VISIBLE = 0x10000000
    WS_CAPTION = 0x00C00000
    WS_SYSMENU = 0x00080000
    WS_THICKFRAME = 0x00040000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000

    SWP_NOZORDER = 0x0004
    SWP_FRAMECHANGED = 0x0020
    SWP_SHOWWINDOW = 0x0040

    WM_CLOSE = 0x0010
    WM_CHAR = 0x0102

    # Windows already embedded by any EmbeddedWindow, so two tabs never grab the same one.
    _claimed = set()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

        self.setObjectName("EmbeddedHost")
        self.setMinimumSize(400, 300)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#EmbeddedHost { background-color: #000000; }")
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.hwnd = None
        self.process = None
        self.launcher_started = False
        self.previous_windows = set()
        self._attached = None

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(250)
        self.poll_timer.timeout.connect(self.find_window)

        self.timeout_timer = QTimer(self)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self.embedding_timeout)

        # Detects the program being closed after embedding.
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(1000)
        self.watch_timer.timeout.connect(self._check_session)

        self.status_label = QLabel(self.IDLE_MESSAGE)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; padding: 12px 20px; font-size: 14px; background: transparent;"
        )

        self.start_button = QPushButton(f"Start {self.NAME}")
        self.start_button.setObjectName("Primary")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.setMinimumWidth(180)
        self.start_button.clicked.connect(lambda _=False: self.start())

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.addStretch(1)
        self._root_layout.addWidget(self.status_label)
        self._root_layout.addWidget(self.start_button, alignment=Qt.AlignHCenter)
        self._root_layout.addStretch(1)

        if os.name == "nt":
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._setup_win32_signatures()

    # ---------- hooks for subclasses ----------

    def build_launch(self, target=None):
        """Return (args, popen_kwargs, error). args is None if it can't launch."""
        raise NotImplementedError

    def accepts(self, hwnd):
        """True if this top-level window is the one we should embed."""
        raise NotImplementedError

    # ---------- win32 plumbing ----------

    def _setup_win32_signatures(self):
        u, k = self.user32, self.kernel32
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        u.EnumWindows.restype = wintypes.BOOL
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL
        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsWindow.restype = wintypes.BOOL
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
        u.SetParent.restype = wintypes.HWND
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT,
        ]
        u.SetWindowPos.restype = wintypes.BOOL
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.restype = wintypes.BOOL
        u.SetFocus.argtypes = [wintypes.HWND]
        u.SetFocus.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        u.AttachThreadInput.restype = wintypes.BOOL

        k.GetCurrentThreadId.restype = wintypes.DWORD
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

    def window_class(self, hwnd):
        buf = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buf, len(buf))
        return buf.value.lower()

    def window_size(self, hwnd):
        rect = wintypes.RECT()
        if self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.right - rect.left, rect.bottom - rect.top
        return 0, 0

    def has_owner(self, hwnd):
        GW_OWNER = 4
        return bool(self.user32.GetWindow(hwnd, GW_OWNER))

    def process_name(self, hwnd):
        """Executable file name (lowercase) of the process that owns the window."""
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name.lower()
        finally:
            self.kernel32.CloseHandle(handle)
        return ""

    def get_candidate_windows(self):
        if os.name != "nt":
            return set()

        found = set()
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, lparam):
            if self.user32.IsWindowVisible(hwnd) and self.accepts(hwnd):
                found.add(int(hwnd))
            return True

        self.user32.EnumWindows(callback_type(callback), 0)
        return found

    # ---------- lifecycle ----------

    def set_message(self, text, offer_start=True):
        self.status_label.setText(text)
        self.status_label.show()
        self.start_button.setText(f"Start {self.NAME}")
        self.start_button.setVisible(offer_start)

    def _hide_message(self):
        self.status_label.hide()
        self.start_button.hide()

    def is_running(self):
        return bool(self.hwnd) or self.launcher_started

    def start(self, target=None):
        if os.name != "nt":
            self.set_message("Native window embedding is only supported on Windows.", False)
            return

        if self.hwnd or self.launcher_started:
            return

        args, kwargs, error = self.build_launch(target)
        if args is None:
            self.set_message(error)
            return

        self.launcher_started = True
        self.set_message(f"Starting {self.NAME}...", False)
        self.previous_windows = self.get_candidate_windows()

        try:
            self.process = subprocess.Popen(args, **kwargs)
            self.poll_timer.start()
            self.timeout_timer.start(self.TIMEOUT_MS)
        except Exception as e:
            log.exception("Failed to launch %s", self.NAME)
            self.launcher_started = False
            self.set_message(f"Failed to launch {self.NAME}: {e}")

    def stop(self, message=None):
        self.poll_timer.stop()
        self.timeout_timer.stop()
        self.watch_timer.stop()

        if self.hwnd and os.name == "nt":
            if self.user32.IsWindow(wintypes.HWND(self.hwnd)):
                self.user32.PostMessageW(wintypes.HWND(self.hwnd), self.WM_CLOSE, 0, 0)
        self._detach_input()
        EmbeddedWindow._claimed.discard(self.hwnd)
        self.hwnd = None

        if self.process and self.process.poll() is None:
            # Kill the whole tree so no orphaned bash.exe / mintty.exe is left behind.
            if not kill_tree(self.process.pid):
                self.process.terminate()
        self.process = None
        self.launcher_started = False
        self.set_message(message or f"{self.NAME} stopped.")

    def restart(self, target=None):
        self.stop("Restarting...")
        QTimer.singleShot(700, lambda: self.start(target))

    def shutdown(self):
        """Call from the main window's closeEvent (child widgets don't get one)."""
        self.stop("Closing...")

    def _check_session(self):
        if self.hwnd and not self.user32.IsWindow(wintypes.HWND(self.hwnd)):
            self.watch_timer.stop()
            self._detach_input()
            EmbeddedWindow._claimed.discard(self.hwnd)
            self.hwnd = None
            self.process = None
            self.launcher_started = False
            self.set_message(f"{self.NAME} was closed.")

    def find_window(self):
        if self.hwnd:
            self.poll_timer.stop()
            return

        new_windows = (
            self.get_candidate_windows() - self.previous_windows - EmbeddedWindow._claimed
        )
        if new_windows:
            self.hwnd = next(iter(new_windows))
            EmbeddedWindow._claimed.add(self.hwnd)
            self.poll_timer.stop()
            self.timeout_timer.stop()
            self._hide_message()
            self.embed_window()
            self.watch_timer.start()

    def embedding_timeout(self):
        if not self.hwnd:
            self.poll_timer.stop()
            self.launcher_started = False
            self.set_message(self.TIMEOUT_MESSAGE)

    # ---------- embedding ----------

    def embed_window(self):
        if not self.hwnd:
            return

        hwnd_w = wintypes.HWND(self.hwnd)
        parent_hwnd_w = wintypes.HWND(int(self.winId()))

        self.user32.SetParent(hwnd_w, parent_hwnd_w)

        style = self.user32.GetWindowLongPtrW(hwnd_w, self.GWL_STYLE)
        style &= ~(
            self.WS_POPUP | self.WS_CAPTION | self.WS_SYSMENU |
            self.WS_THICKFRAME | self.WS_MINIMIZEBOX | self.WS_MAXIMIZEBOX
        )
        style |= self.WS_CHILD | self.WS_VISIBLE
        self.user32.SetWindowLongPtrW(hwnd_w, self.GWL_STYLE, style)

        self.resize_embedded_window()
        self.focus_window()

    def _attach_input(self):
        """Share the input queue with the child's thread and keep it that way."""
        if self._attached or os.name != "nt" or not self.hwnd:
            return
        our_tid = self.kernel32.GetCurrentThreadId()
        their_tid = self.user32.GetWindowThreadProcessId(wintypes.HWND(self.hwnd), None)
        if their_tid and their_tid != our_tid:
            if self.user32.AttachThreadInput(our_tid, their_tid, True):
                self._attached = (our_tid, their_tid)

    def _detach_input(self):
        if self._attached and os.name == "nt":
            self.user32.AttachThreadInput(self._attached[0], self._attached[1], False)
        self._attached = None

    def focus_window(self):
        """Give keyboard focus to the embedded window."""
        if os.name != "nt" or not self.hwnd:
            return
        hwnd_w = wintypes.HWND(self.hwnd)
        if not self.user32.IsWindow(hwnd_w):
            return
        self._attach_input()
        self.user32.SetFocus(hwnd_w)

    def send_text(self, text):
        """Type text into the embedded window (best effort, Windows only)."""
        if os.name != "nt" or not self.hwnd:
            return False
        hwnd_w = wintypes.HWND(self.hwnd)
        if not self.user32.IsWindow(hwnd_w):
            return False
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        data = text.encode("utf-16-le")  # UTF-16 code units, so emoji etc. survive
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            self.user32.PostMessageW(hwnd_w, self.WM_CHAR, unit, 0)
        return True

    def focus_soon(self):
        """Qt may re-grab focus after activation, so retry a few times."""
        for delay in (30, 150, 400):
            QTimer.singleShot(delay, self.focus_window)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_soon()

    def resize_embedded_window(self):
        if self.hwnd and self.user32.IsWindow(wintypes.HWND(self.hwnd)):
            dpr = self.devicePixelRatioF()
            w = int(self.width() * dpr)
            h = int(self.height() * dpr)
            self.user32.SetWindowPos(
                wintypes.HWND(self.hwnd), None, 0, 0, w, h,
                self.SWP_NOZORDER | self.SWP_FRAMECHANGED | self.SWP_SHOWWINDOW,
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_embedded_window()

    def showEvent(self, event):
        super().showEvent(event)
        if self.hwnd and os.name == "nt":
            self.resize_embedded_window()
            self.focus_soon()


class EmbeddedGitBash(EmbeddedWindow):
    NAME = "Terminal"
    TIMEOUT_MS = 15000
    IDLE_MESSAGE = "GitPortable will appear here when it starts."
    TIMEOUT_MESSAGE = (
        "GitPortable started, but no terminal window was captured.\n"
        "Check the launcher path in Settings."
    )

    def build_launch(self, target=None):
        launcher = find_launcher(self.settings)
        if not launcher.is_file():
            return None, None, f"Error: Launcher not found at:\n{launcher}"
        kwargs = {"cwd": str(launcher.parent), "creationflags": CREATE_NEW_PROCESS_GROUP}
        if target and Path(target).is_dir():
            # Best effort: Git Bash's login profile stays in the current directory
            # when CHERE_INVOKING is set.
            env = os.environ.copy()
            env["CHERE_INVOKING"] = "1"
            kwargs["cwd"] = str(target)
            kwargs["env"] = env
        return [str(launcher)], kwargs, ""

    def accepts(self, hwnd):
        return self.window_class(hwnd) == "mintty"


class EmbeddedVSCode(EmbeddedWindow):
    NAME = "VS Code"
    TIMEOUT_MS = 45000
    IDLE_MESSAGE = "VS Code isn't running."
    TIMEOUT_MESSAGE = (
        "VS Code was launched, but its window wasn't captured.\n"
        "Check the VS Code settings (alias, Git Bash path, or executable path)."
    )

    def build_launch(self, target=None):
        args, kwargs, info = vscode_launch_plan(self.settings, target)
        if args is None:
            return None, None, info
        return args, kwargs, ""

    def accepts(self, hwnd):
        # Electron windows all share one class, so also check the owning executable.
        if self.window_class(hwnd) != "chrome_widgetwin_1" or self.has_owner(hwnd):
            return False
        w, h = self.window_size(hwnd)
        if w < 300 or h < 200:
            return False
        name = self.process_name(hwnd)
        return name.startswith("code") or name.startswith("vscodium")


class EmbeddedTool(EmbeddedWindow):
    """Any program from the Tools page. Its window is recognised by executable name."""

    def __init__(self, settings, name, exe, args_text="", parent=None):
        super().__init__(settings, parent)
        self.exe = str(exe)
        self.args_text = str(args_text or "")
        self.exe_name = Path(self.exe).name.lower()
        self.NAME = name
        self.TIMEOUT_MS = 25000
        self.IDLE_MESSAGE = f"{name} will appear here when it starts."
        self.TIMEOUT_MESSAGE = (
            f"{name} was launched, but its window wasn't captured.\n"
            "Apps that are already running or that reuse an existing window "
            "can't be captured. Close them first and try again."
        )
        self.set_message(self.IDLE_MESSAGE)

    def build_launch(self, target=None):
        p = Path(self.exe)
        if not p.is_file():
            return None, None, f"Program not found:\n{p}"
        args = [str(p)] + split_args(self.args_text)
        kwargs = {"cwd": str(p.parent), "creationflags": CREATE_NEW_PROCESS_GROUP}
        return args, kwargs, ""

    def accepts(self, hwnd):
        if self.has_owner(hwnd):
            return False
        w, h = self.window_size(hwnd)
        if w < 200 or h < 120:
            return False
        return self.process_name(hwnd) == self.exe_name


# ============================================================
# FLOATING DEVTOOLS WINDOW
# ============================================================

class DevToolsWindow(QWidget):
    """Small floating DevTools panel. Drag the title bar to move, the corner to resize."""

    BAR_H = 32

    def __init__(self, parent):
        # Qt.Tool keeps it above the main window and out of the taskbar
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("DevToolsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(320, 220)
        self.resize(560, 380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("DevBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(self.BAR_H)
        bar.setCursor(Qt.SizeAllCursor)
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(12, 0, 6, 0)
        title = QLabel("DevTools  -  PocketAi")
        title.setObjectName("DevTitle")
        bar_l.addWidget(title)
        bar_l.addStretch()
        close = QPushButton("\u00d7")
        close.setObjectName("TabClose")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(22, 22)
        close.clicked.connect(self.hide)
        bar_l.addWidget(close)
        outer.addWidget(bar)

        self.view = QWebEngineView()
        outer.addWidget(self.view, stretch=1)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self))
        outer.addLayout(grip_row)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() <= self.BAR_H:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)


# ============================================================
# TAB HELPERS
# ============================================================

def add_closable_tab(tabs, widget, label):
    """Add a tab with a themed close button that emits tabCloseRequested(index)."""
    idx = tabs.addTab(widget, label)
    btn = QPushButton("\u00d7")
    btn.setObjectName("TabClose")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(18, 18)
    btn.clicked.connect(
        lambda _=False, w=widget: tabs.tabCloseRequested.emit(tabs.indexOf(w))
    )
    tabs.tabBar().setTabButton(idx, QTabBar.ButtonPosition.RightSide, btn)
    return idx


class TerminalTabs(QTabWidget):
    """Several embedded Git Bash sessions in tabs."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("Tabs")
        self.setMovable(True)
        self._counter = 0

        self.tabCloseRequested.connect(self.close_tab)
        self.add_terminal(start=False)

    def add_terminal(self, start=True, target=None, title=None):
        term = EmbeddedGitBash(self.settings)
        self._counter += 1
        idx = add_closable_tab(self, term, title or f"Terminal {self._counter}")
        self.setCurrentIndex(idx)
        if start:
            term.start(target)
        return term

    def terminals(self):
        return [self.widget(i) for i in range(self.count())]

    def current_terminal(self):
        return self.currentWidget()

    def close_tab(self, index):
        term = self.widget(index)
        if term is None:
            return
        if self.count() == 1:
            term.stop("Terminal stopped.")
            return
        term.shutdown()
        self.removeTab(index)
        term.deleteLater()

    def any_running(self):
        return any(t.is_running() for t in self.terminals())

    def shutdown_all(self):
        for t in self.terminals():
            t.shutdown()

    def start_current(self):
        t = self.current_terminal()
        if t:
            t.start()

    def restart_current(self):
        t = self.current_terminal()
        if t:
            t.restart()

    def stop_current(self, message=None):
        t = self.current_terminal()
        if t:
            t.stop(message)


class DonutUsageWidget(QWidget):
    """Compact live usage donut for the dashboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.available = True
        self.setMinimumSize(112, 112)
        self.setMaximumSize(132, 132)

    def set_value(self, value, available=True):
        self.value = max(0.0, min(100.0, float(value or 0.0)))
        self.available = bool(available)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        diameter = side - 18
        rect = QRectF((self.width() - diameter) / 2, (self.height() - diameter) / 2,
                      diameter, diameter)
        track = QColor("#302541")
        accent = QColor("#a879ff")
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(track, 10, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -360 * 16)
        if self.available:
            painter.setPen(QPen(accent, 10, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 90 * 16, -int(self.value * 3.6 * 16))
        painter.setPen(QColor("#f2eaff"))
        font = QFont("Segoe UI", 16, QFont.Bold)
        painter.setFont(font)
        text = f"{self.value:.0f}%" if self.available else "N/A"
        painter.drawText(self.rect(), Qt.AlignCenter, text)
        painter.end()


# ============================================================
# PLUGIN API
# ============================================================

class PocketTermPluginAPI:
    """Small convenience API exposed to local PocketTerm plugins.

    Plugins are ordinary Python code and are NOT sandboxed. They run with the
    same permissions as PocketTerm. Only enable plugins you trust.
    """
    def __init__(self, window, plugin_id):
        self._window = window
        self.plugin_id = plugin_id

    def add_action(self, name, callback, description=""):
        """Add a button to the Plugins page. callback receives no arguments."""
        if not callable(callback):
            raise TypeError("Plugin action callback must be callable")
        self._window._plugin_actions.setdefault(self.plugin_id, []).append({
            "name": str(name)[:80], "callback": callback,
            "description": str(description)[:240],
        })
        self._window._render_plugin_actions()

    def notify(self, message, title="PocketTerm plugin"):
        QMessageBox.information(self._window, str(title)[:100], str(message)[:2000])

    def log(self, message):
        log.info("[plugin:%s] %s", self.plugin_id, str(message)[:2000])


# ============================================================
# MAIN WINDOW
# ============================================================

class MainWindow(QMainWindow):
    # Emitted from background threads, handled on the UI thread.
    git_info_ready = Signal(int, str, object)
    task_done = Signal(str, bool, str)
    drives_ready = Signal(object)
    ports_ready = Signal(object)
    plugins_loaded = Signal(object, str)
    plugin_preview_ready = Signal(str, str)
    plugin_action_done = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.settings = Settings(SETTINGS_FILE)
        if self.settings.get("accent") not in ACCENT_CHOICES:
            self.settings.data["accent"] = "Violet"

        self._vscode_autostarted = False
        self._project_paths = []
        self._script_paths = []
        self.git_cache = {}
        self._git_gen = 0
        self.script_proc = None
        self.tray = None
        self._user32 = None
        self._hotkey_registered = False
        self._was_max = False
        self._edits = {}    # settings key -> QLineEdit (for import refresh)
        self._checks = {}   # settings key -> QCheckBox
        self._delegates = []
        self._nav_base = {}
        self._nav_groups = {}
        self._nav_group_open = {}
        self._nav_page_group = {}
        self._nav_running = {}
        self._drives_busy = False
        self._drives_sig = None
        self._drive_tick_count = 0
        self._low_alerted = set()
        self._ports_busy = False
        self._ports_cache = None
        self._script_path = None
        self._skip_close_confirm = False
        self.web_view = None
        self._devtools = None
        self._web_started = False
        self._ai_checked = False      # has the PocketAi tab been opened yet (for auto-start)
        self.ai_proc = None
        self._ai_deadline = 0.0
        self._ai_last_line = ""
        self.dashboard_labels = {}
        self.dashboard_donuts = {}
        self.background_manager = None
        self.file_model = None
        self.file_tree = None
        self.file_path_edit = None
        self.plugins_tree = None
        self.plugins_preview = None
        self.plugins_status = None
        self._plugin_entries = {}
        self._plugin_preview_path = None
        self._plugins_busy = False
        self._loaded_plugins = {}
        self._plugin_actions = {}
        self.local_plugins_list = None
        self.plugin_actions_layout = None
        self._ai_timer = QTimer(self)
        self._ai_timer.setInterval(1000)
        self._ai_timer.timeout.connect(self._ai_poll)

        self.setWindowTitle("PocketTerm Launcher")
        self.setAcceptDrops(True)
        self._apply_style()
        self._restore_size()

        self.git_info_ready.connect(self._on_git_info)
        self.task_done.connect(self._on_task_done)
        self.drives_ready.connect(self._on_drives)
        self.ports_ready.connect(self._on_ports)
        self.plugins_loaded.connect(self._on_plugins_loaded)
        self.plugin_preview_ready.connect(self._on_plugin_preview_ready)
        self.plugin_action_done.connect(self._on_plugin_action_done)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create the embedded widgets first; pages and sidebar reference them.
        self.terminals = TerminalTabs(self.settings)
        self.vscode = EmbeddedVSCode(self.settings)

        # Keep navigation usable on short displays. The sidebar content can
        # scroll vertically instead of forcing the whole window to grow.
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName("SidebarScroll")
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sidebar_scroll.setFixedWidth(216)
        sidebar_scroll.setWidget(self._build_sidebar())
        main_layout.addWidget(sidebar_scroll)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self._build_terminal_page())   # PAGE_TERMINAL
        self.stacked_widget.addWidget(self._build_vscode_page())     # PAGE_VSCODE
        self.stacked_widget.addWidget(self._build_projects_page())   # PAGE_PROJECTS
        self.stacked_widget.addWidget(self._build_scripts_page())    # PAGE_SCRIPTS
        self.stacked_widget.addWidget(self._build_snippets_page())   # PAGE_SNIPPETS
        self.stacked_widget.addWidget(self._build_tools_page())      # PAGE_TOOLS
        self.stacked_widget.addWidget(self._build_drives_page())     # PAGE_DRIVES
        self.stacked_widget.addWidget(self._build_ports_page())      # PAGE_PORTS
        self.stacked_widget.addWidget(self._build_web_page())        # PAGE_WEB
        self.stacked_widget.addWidget(self._build_settings_page())   # PAGE_SETTINGS
        self.stacked_widget.addWidget(self._build_dashboard_page())  # PAGE_DASHBOARD
        self.stacked_widget.addWidget(self._build_files_page())      # PAGE_FILES
        self.stacked_widget.addWidget(self._build_plugins_page())    # PAGE_PLUGINS
        main_layout.addWidget(self.stacked_widget, stretch=1)

        for index, button in self.nav_buttons.items():
            button.clicked.connect(lambda _=False, i=index: self.show_page(i))

        self.terminals.currentChanged.connect(lambda _i: self._focus_current())
        self.tool_tabs.currentChanged.connect(lambda _i: self._focus_current())

        self._setup_shortcuts()
        self.refresh_projects()
        self.refresh_scripts()
        self._start_background_manager()
        QTimer.singleShot(700, self.load_enabled_plugins)

        # Small status dots in the sidebar for things that are running.
        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(1000)
        self._nav_timer.timeout.connect(self._update_nav_indicators)
        self._nav_timer.start()

        # Re-scan drives every 15 s while the Drives page is open (and about once a
        # minute in the background when low-space alerts are on).
        self._drives_timer = QTimer(self)
        self._drives_timer.setInterval(DRIVE_REFRESH_MS)
        self._drives_timer.timeout.connect(self._drives_tick)
        self._drives_timer.start()
        if self.settings.get("low_space_alerts"):
            QTimer.singleShot(3000, self.refresh_drives)

        # Refresh the Ports list every few seconds while that page is open.
        self._ports_timer = QTimer(self)
        self._ports_timer.setInterval(5000)
        self._ports_timer.timeout.connect(self._ports_tick)
        self._ports_timer.start()

        self._dashboard_timer = QTimer(self)
        self._dashboard_timer.setInterval(3000)
        self._dashboard_timer.timeout.connect(self._refresh_dashboard)
        self._dashboard_timer.start()
        QTimer.singleShot(250, self._refresh_dashboard)

        if self.settings.get("auto_start"):
            QTimer.singleShot(500, self.start_terminal)
        else:
            self.terminals.current_terminal().set_message(
                "Auto-start is off.\nPress Start below, or use Settings."
            )

        last = self.settings.get("last_page")
        if not isinstance(last, int) or not (0 <= last < PAGE_COUNT):
            last = PAGE_TERMINAL
        self.show_page(last)

    # ---------- shared UI helpers ----------

    @staticmethod
    def _make_card(title, description):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        d = QLabel(description)
        d.setObjectName("Description")
        d.setWordWrap(True)
        layout.addWidget(t)
        layout.addWidget(d)
        return card, layout

    @staticmethod
    def _page_header(title, description, buttons=()):
        row = QHBoxLayout()
        left = QVBoxLayout()
        t = QLabel(title)
        t.setObjectName("PageTitle")
        d = QLabel(description)
        d.setObjectName("Description")
        d.setWordWrap(True)
        left.addWidget(t)
        left.addWidget(d)
        row.addLayout(left, stretch=1)
        for b in buttons:
            row.addWidget(b, alignment=Qt.AlignTop)
        return row

    @staticmethod
    def _button(text, handler, object_name=None):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        if object_name:
            b.setObjectName(object_name)
        b.clicked.connect(lambda _=False: handler())
        return b

    @staticmethod
    def _set_status(label, ok, text):
        label.setText(text)
        label.setStyleSheet(f"color: {OK_COLOR};" if ok else f"color: {ERR_COLOR};")

    def _apply_style(self):
        self.setStyleSheet(build_style(self.settings))
        palette = get_palette(self.settings)
        strong = palette[1]
        for d in self._delegates:
            d.set_colors(strong, on_color(strong))
        for name in ("project_list", "script_list", "snippet_list", "port_list"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.viewport().update()

    def _new_row_delegate(self, list_widget):
        d = RowDelegate(list_widget)
        palette = get_palette(self.settings)
        d.set_colors(palette[1], on_color(palette[1]))
        self._delegates.append(d)
        list_widget.setItemDelegate(d)
        list_widget.setMouseTracking(True)
        list_widget.setSpacing(2)
        return d

    # ---------- building the pages ----------

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(196)
        sidebar.setMaximumWidth(216)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 24, 16, 20)
        layout.setSpacing(5)

        logo_label = QLabel("PocketTerm")
        logo_label.setObjectName("Logo")
        subtitle_label = QLabel("DEVELOPER ENVIRONMENT")
        subtitle_label.setObjectName("Subtitle")
        layout.addWidget(logo_label)
        layout.addWidget(subtitle_label)
        layout.addSpacing(24)

        self.nav_buttons = {}
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_groups = {}        # group name -> (header button, body frame)
        self._nav_group_open = {}    # group name -> bool
        self._nav_page_group = {}    # page index -> group name
        self._nav_running = {}       # page index -> bool (updated by the nav timer)

        def add_nav(parent_layout, label, index):
            btn = QPushButton(label)
            btn.setObjectName("Navigation")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"Ctrl+{(index + 1) % 10}")
            self.nav_group.addButton(btn)
            self.nav_buttons[index] = btn
            self._nav_base[index] = label
            parent_layout.addWidget(btn)

        collapsed = set(self.settings.get("nav_collapsed"))
        for group, items in NAV_GROUPS:
            header = QPushButton()
            header.setObjectName("NavGroup")
            header.setCursor(Qt.PointingHandCursor)
            header.clicked.connect(lambda _=False, g=group: self._toggle_group(g))

            body = QFrame()
            body.setObjectName("NavGroupBody")
            body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(8, 0, 0, 4)
            body_layout.setSpacing(4)
            for label, index in items:
                add_nav(body_layout, label, index)
                self._nav_page_group[index] = group

            self._nav_groups[group] = (header, body)
            layout.addWidget(header)
            layout.addWidget(body)
            self._set_group_open(group, group not in collapsed, save=False)

        layout.addStretch()
        add_nav(layout, "Settings Profile", PAGE_SETTINGS)

        layout.addSpacing(10)
        hint = QLabel("CTRL+K  COMMAND PALETTE")
        hint.setObjectName("Subtitle")
        layout.addWidget(hint)

        self.nav_buttons[PAGE_TERMINAL].setChecked(True)
        return sidebar

    def _group_header_text(self, group):
        is_open = self._nav_group_open.get(group, True)
        text = ("\u25be" if is_open else "\u25b8") + "  " + group.upper()
        if not is_open:
            pages = [i for i, g in self._nav_page_group.items() if g == group]
            if any(self._nav_running.get(i) for i in pages):
                text += "   \u25cf"
        return text

    def _set_group_open(self, group, is_open, save=True):
        header, body = self._nav_groups[group]
        self._nav_group_open[group] = is_open
        body.setVisible(is_open)
        header.setText(self._group_header_text(group))
        if save:
            self.settings.set(
                "nav_collapsed",
                [g for g, o in self._nav_group_open.items() if not o],
            )

    def _toggle_group(self, group):
        self._set_group_open(group, not self._nav_group_open.get(group, True))

    def _tools_running(self):
        for i in range(self.tool_tabs.count()):
            w = self.tool_tabs.widget(i)
            if isinstance(w, EmbeddedWindow) and w.is_running():
                return True
        return False

    def _update_nav_indicators(self):
        running = {
            PAGE_TERMINAL: self.terminals.any_running(),
            PAGE_VSCODE: self.vscode.is_running(),
            PAGE_SCRIPTS: self._script_running(),
            PAGE_WEB: self.ai_running(),
            PAGE_TOOLS: self._tools_running(),
        }
        self._nav_running = running
        for index, base in self._nav_base.items():
            text = base + ("   \u25cf" if running.get(index) else "")
            btn = self.nav_buttons[index]
            if btn.text() != text:
                btn.setText(text)
        for group, (header, _body) in self._nav_groups.items():
            text = self._group_header_text(group)
            if header.text() != text:
                header.setText(text)

    def _start_background_manager(self):
        """Start the separate no-console worker used only for extra background activities."""
        if self.background_manager is not None and self.background_manager.poll() is None:
            return
        if not PROCESS_MANAGER_FILE.is_file():
            log.warning("Background process manager not found: %s", PROCESS_MANAGER_FILE)
            return
        python_exe = Path(sys.executable)
        if os.name == "nt":
            pythonw = python_exe.with_name("pythonw.exe")
            if pythonw.is_file():
                python_exe = pythonw
        creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.background_manager = subprocess.Popen(
                [str(python_exe), str(PROCESS_MANAGER_FILE), "--worker"],
                cwd=str(SCRIPT_DIR), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            log.info("Background activity manager started (PID %s).", self.background_manager.pid)
        except Exception:
            self.background_manager = None
            log.exception("Could not start the background activity manager")

    def run_background_activity(self, program, args=None, *, cwd=None, env=None, name=None):
        """Queue an extra non-interactive activity in ProcessManager.py.

        This is intentionally separate from the existing terminal, VS Code, PocketAi,
        scripts and embedded-tool launch paths.
        """
        self._start_background_manager()
        proc = self.background_manager
        if proc is None or proc.poll() is not None or proc.stdin is None:
            log.warning("Background activity manager is not running; activity was not started.")
            return False
        request = {
            "action": "launch", "program": str(program),
            "args": [str(x) for x in (args or [])],
            "cwd": str(cwd) if cwd else None,
            "env": {str(k): str(v) for k, v in (env or {}).items()},
            "name": str(name or Path(str(program)).name),
        }
        try:
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            log.exception("Could not send activity to the background manager")
            return False

    def stop_background_activity(self, name):
        """Ask the helper to stop every activity registered under this name."""
        proc = self.background_manager
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        try:
            request = {"action": "stop_name", "name": str(name)}
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            log.exception("Could not stop background activity")
            return False

    def _stop_background_manager(self):
        proc = self.background_manager
        self.background_manager = None
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b'{"action":"shutdown"}\n')
                proc.stdin.flush()
                proc.stdin.close()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        refresh = self._button("Refresh", self._refresh_dashboard, "CornerButton")
        layout.addLayout(self._page_header(
            "PocketTerm Control Center",
            "A quick overview of this computer, your portable environment and available tools.",
            (refresh,),
        ))
        layout.addSpacing(18)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        cards = [
            ("CPU", "cpu", "Checking processor usage..."),
            ("Memory", "memory", "Checking memory..."),
            ("PocketTerm USB", "disk", "Checking portable drive..."),
            ("Environment", "environment", "Detecting portable tools..."),
        ]
        for i, (title, key, initial) in enumerate(cards):
            card = QFrame()
            card.setObjectName("Card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 16, 18, 16)
            title_label = QLabel(title)
            title_label.setObjectName("SectionTitle")
            card_layout.addWidget(title_label)
            if key in ("cpu", "memory"):
                metric_row = QHBoxLayout()
                donut = DonutUsageWidget()
                self.dashboard_donuts[key] = donut
                metric_row.addWidget(donut, 0, Qt.AlignVCenter)
                value_label = QLabel(initial)
                value_label.setObjectName("Description")
                value_label.setWordWrap(True)
                value_label.setMinimumHeight(52)
                metric_row.addWidget(value_label, 1, Qt.AlignVCenter)
                card_layout.addLayout(metric_row)
            else:
                value_label = QLabel(initial)
                value_label.setObjectName("Description")
                value_label.setWordWrap(True)
                value_label.setMinimumHeight(52)
                card_layout.addWidget(value_label)
            grid.addWidget(card, i // 2, i % 2)
            self.dashboard_labels[key] = value_label
        layout.addLayout(grid)
        layout.addSpacing(10)

        quick_card = QFrame()
        quick_card.setObjectName("Card")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(18, 16, 18, 16)
        quick_title = QLabel("Quick launch")
        quick_title.setObjectName("SectionTitle")
        quick_layout.addWidget(quick_title)
        actions = QHBoxLayout()
        for label, handler in (
            ("Terminal", lambda: self.show_page(PAGE_TERMINAL)),
            ("File Manager", lambda: self.show_page(PAGE_FILES)),
            ("Projects", lambda: self.show_page(PAGE_PROJECTS)),
            ("VS Code", lambda: self.show_page(PAGE_VSCODE)),
            ("Tools", lambda: self.show_page(PAGE_TOOLS)),
        ):
            actions.addWidget(self._button(label, handler))
        quick_layout.addLayout(actions)
        layout.addWidget(quick_card)
        layout.addStretch(1)
        return page

    def _build_files_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addLayout(self._page_header(
            "Portable File Manager",
            "Browse the USB drive and local folders. Double-click a folder to open it or a file to launch it.",
        ))
        layout.addSpacing(14)

        path_row = QHBoxLayout()
        self.file_path_edit = QLineEdit(str(PORTABLE_ROOT))
        self.file_path_edit.setPlaceholderText("Enter a folder path...")
        self.file_path_edit.returnPressed.connect(self._navigate_file_path)
        path_row.addWidget(self.file_path_edit, stretch=1)
        path_row.addWidget(self._button("Go", self._navigate_file_path, "Primary"))
        path_row.addWidget(self._button("Up", self._file_go_up))
        path_row.addWidget(self._button("USB Root", self._file_go_home))
        path_row.addWidget(self._button("Open", self._open_selected_file))
        layout.addLayout(path_row)

        self.file_model = QFileSystemModel(self)
        self.file_model.setReadOnly(True)
        self.file_model.setRootPath(str(PORTABLE_ROOT))
        self.file_tree = QTreeView()
        self.file_tree.setModel(self.file_model)
        self.file_tree.setRootIndex(self.file_model.index(str(PORTABLE_ROOT)))
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.setSortingEnabled(True)
        self.file_tree.sortByColumn(0, Qt.AscendingOrder)
        self.file_tree.setColumnWidth(0, 330)
        self.file_tree.doubleClicked.connect(self._file_item_activated)
        layout.addWidget(self.file_tree, stretch=1)
        hint = QLabel("Tip: double-click a folder to browse it, or double-click a file to open it with its default Windows application.")
        hint.setObjectName("Description")
        layout.addWidget(hint)
        return page


    # ---------- online plugin browser ----------

    def _build_plugins_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)

        refresh = self._button("Refresh online", self.refresh_plugins, "CornerButton")
        download = self._button("Download selected", self.download_selected_plugin, "Primary")
        clone = self._button("Clone Plugins folder", self.clone_plugins_folder, "CornerButton")
        layout.addLayout(self._page_header(
            "Plugin Library",
            "Browse the PocketTerm GitHub Plugins folder, download individual Python files, and manage trusted local plugins.",
            (refresh, download, clone),
        ))

        warning = QLabel("Security: .py plugins execute Python code with your account permissions. Downloaded files are NOT run automatically. Only enable code you have inspected and trust.")
        warning.setObjectName("Description")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        online = QFrame(); online.setObjectName("Card")
        online_layout = QVBoxLayout(online); online_layout.setContentsMargins(12, 12, 12, 12)
        online_layout.addWidget(QLabel("Online repository files"))
        self.plugins_tree = QTreeWidget()
        self.plugins_tree.setHeaderLabels(["Name", "Type"])
        self.plugins_tree.setAlternatingRowColors(True)
        self.plugins_tree.setUniformRowHeights(True)
        self.plugins_tree.setColumnWidth(0, 230)
        self.plugins_tree.itemClicked.connect(self._plugin_item_clicked)
        online_layout.addWidget(self.plugins_tree, 1)
        self.plugins_preview = QPlainTextEdit()
        self.plugins_preview.setReadOnly(True)
        self.plugins_preview.setPlaceholderText("Select a repository file to preview it.")
        self.plugins_preview.setMaximumHeight(170)
        self.plugins_preview.setObjectName("Log")
        online_layout.addWidget(self.plugins_preview)
        splitter.addWidget(online)

        local = QFrame(); local.setObjectName("Card")
        local_layout = QVBoxLayout(local); local_layout.setContentsMargins(12, 12, 12, 12)
        local_layout.addWidget(QLabel("Installed local plugins"))
        self.local_plugins_list = QListWidget()
        self.local_plugins_list.setAlternatingRowColors(True)
        local_layout.addWidget(self.local_plugins_list, 1)
        controls = QHBoxLayout()
        controls.addWidget(self._button("Scan", self.refresh_local_plugins, "CornerButton"))
        controls.addWidget(self._button("Enable", self.enable_selected_plugin, "Primary"))
        controls.addWidget(self._button("Disable", self.disable_selected_plugin, "CornerButton"))
        controls.addWidget(self._button("Reload", self.reload_selected_plugin, "CornerButton"))
        controls.addWidget(self._button("Remove", self.remove_selected_plugin, "Danger"))
        local_layout.addLayout(controls)
        actions_title = QLabel("Plugin actions")
        actions_title.setObjectName("SectionTitle")
        local_layout.addWidget(actions_title)
        self.plugin_actions_layout = QVBoxLayout()
        self.plugin_actions_layout.setSpacing(6)
        local_layout.addLayout(self.plugin_actions_layout)
        splitter.addWidget(local)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, 1)

        self.plugins_status = QLabel("Choose Refresh online to browse GitHub, or scan your local Plugins folder.")
        self.plugins_status.setObjectName("Description")
        self.plugins_status.setWordWrap(True)
        layout.addWidget(self.plugins_status)
        self.refresh_local_plugins()
        return page

    # ---------- local Python plugin manager ----------

    def _plugin_files(self):
        folder = APP_DIR / "Plugins"
        folder.mkdir(parents=True, exist_ok=True)
        return sorted((p for p in folder.rglob("*.py") if p.is_file()
                       and p.name != "__init__.py" and not p.name.startswith("_")),
                      key=lambda p: str(p).lower())

    def refresh_local_plugins(self):
        if not self.local_plugins_list:
            return
        self.local_plugins_list.clear()
        enabled = set(self.settings.get("enabled_plugins"))
        loaded = set(self._loaded_plugins)
        for path in self._plugin_files():
            rel = path.relative_to(APP_DIR / "Plugins").as_posix()
            state = "RUNNING" if rel in loaded else ("ENABLED" if rel in enabled else "DISABLED")
            item = QListWidgetItem(f"{rel}    [{state}]")
            item.setData(Qt.UserRole, rel)
            self.local_plugins_list.addItem(item)
        self._render_plugin_actions()

    def _selected_plugin_relpath(self):
        item = self.local_plugins_list.currentItem() if self.local_plugins_list else None
        return item.data(Qt.UserRole) if item else None

    def _safe_plugin_path(self, rel):
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
            raise ValueError("Invalid plugin path")
        base = (APP_DIR / "Plugins").resolve()
        path = (base / rel).resolve()
        if path != base and base not in path.parents:
            raise ValueError("Plugin path escapes the Plugins folder")
        if path.suffix.lower() != ".py" or not path.is_file():
            raise FileNotFoundError("Select an existing .py plugin file")
        return path

    def _plugin_identifier(self, rel):
        return str(rel).replace("\\", "/")

    def _load_plugin(self, rel):
        rel = self._plugin_identifier(rel)
        if rel in self._loaded_plugins:
            return True, f"{rel} is already loaded."
        path = self._safe_plugin_path(rel)
        module_name = "pocketterm_plugin_" + re.sub(r"[^A-Za-z0-9_]", "_", rel) + "_" + str(abs(hash(str(path))))
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("Could not create a Python module loader")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise ValueError("Plugin must define register(app)")
            api = PocketTermPluginAPI(self, rel)
            register(api)
            self._loaded_plugins[rel] = {"module": module, "module_name": module_name, "api": api}
            return True, f"Loaded {rel}"
        except Exception as exc:
            sys.modules.pop(module_name, None)
            self._plugin_actions.pop(rel, None)
            self._render_plugin_actions()
            log.exception("Plugin load failed: %s", rel)
            return False, f"Could not load {rel}: {exc}"

    def _unload_plugin(self, rel):
        loaded = self._loaded_plugins.get(rel)
        if not loaded:
            return True, f"{rel} is not currently loaded."
        try:
            unregister = getattr(loaded["module"], "unregister", None)
            if callable(unregister):
                unregister(loaded["api"])
        except Exception as exc:
            log.exception("Plugin unload hook failed: %s", rel)
            return False, f"Plugin unload hook failed: {exc}"
        self._loaded_plugins.pop(rel, None)
        sys.modules.pop(loaded["module_name"], None)
        self._plugin_actions.pop(rel, None)
        self._render_plugin_actions()
        return True, f"Disabled {rel}"

    def load_enabled_plugins(self):
        for rel in list(self.settings.get("enabled_plugins")):
            try:
                ok, message = self._load_plugin(rel)
                if not ok:
                    log.warning(message)
            except Exception:
                log.exception("Could not load enabled plugin %s", rel)
        self.refresh_local_plugins()

    def enable_selected_plugin(self):
        rel = self._selected_plugin_relpath()
        if not rel:
            QMessageBox.information(self, "Select a plugin", "Choose a local .py file first.")
            return
        answer = QMessageBox.warning(
            self, "Enable Python plugin",
            f"This will execute {rel} as Python code with PocketTerm's permissions. "
            "It can read or modify files and start processes. Only continue if you trust and have inspected this file.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        ok, message = self._load_plugin(rel)
        if ok:
            enabled = list(self.settings.get("enabled_plugins"))
            if rel not in enabled:
                enabled.append(rel)
            self.settings.set("enabled_plugins", enabled)
        self.refresh_local_plugins()
        if self.plugins_status:
            self.plugins_status.setText(message)
        if not ok:
            QMessageBox.warning(self, "Plugin error", message)

    def disable_selected_plugin(self):
        rel = self._selected_plugin_relpath()
        if not rel:
            return
        ok, message = self._unload_plugin(rel)
        enabled = [x for x in self.settings.get("enabled_plugins") if x != rel]
        self.settings.set("enabled_plugins", enabled)
        self.refresh_local_plugins()
        if self.plugins_status:
            self.plugins_status.setText(message)

    def reload_selected_plugin(self):
        rel = self._selected_plugin_relpath()
        if not rel:
            return
        if rel not in self._loaded_plugins:
            self.enable_selected_plugin()
            return
        ok, message = self._unload_plugin(rel)
        if ok:
            ok, message = self._load_plugin(rel)
        self.refresh_local_plugins()
        if self.plugins_status:
            self.plugins_status.setText(message)
        if not ok:
            QMessageBox.warning(self, "Plugin reload failed", message)

    def remove_selected_plugin(self):
        rel = self._selected_plugin_relpath()
        if not rel:
            return
        answer = QMessageBox.question(self, "Remove plugin", f"Delete {rel} from the local Plugins folder?")
        if answer != QMessageBox.Yes:
            return
        ok, message = self._unload_plugin(rel)
        if not ok:
            QMessageBox.warning(self, "Could not disable plugin", message)
            return
        enabled = [x for x in self.settings.get("enabled_plugins") if x != rel]
        self.settings.set("enabled_plugins", enabled)
        try:
            self._safe_plugin_path(rel).unlink()
            if self.plugins_status:
                self.plugins_status.setText(f"Removed {rel}")
        except Exception as exc:
            QMessageBox.warning(self, "Remove failed", str(exc))
        self.refresh_local_plugins()

    def _render_plugin_actions(self):
        if self.plugin_actions_layout is None:
            return
        while self.plugin_actions_layout.count():
            item = self.plugin_actions_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        count = 0
        for rel, actions in list(self._plugin_actions.items()):
            for action in actions:
                button = QPushButton(f"{action['name']}  ·  {Path(rel).name}")
                button.setToolTip(action["description"] or rel)
                button.setCursor(Qt.PointingHandCursor)
                callback = action["callback"]
                def invoke(cb=callback, plugin=rel):
                    try:
                        cb()
                    except Exception as exc:
                        log.exception("Plugin action failed: %s", plugin)
                        QMessageBox.warning(self, "Plugin action failed", str(exc))
                button.clicked.connect(invoke)
                self.plugin_actions_layout.addWidget(button)
                count += 1
        if count == 0:
            label = QLabel("Enabled plugins can add buttons here with app.add_action(...).")
            label.setObjectName("Description")
            label.setWordWrap(True)
            self.plugin_actions_layout.addWidget(label)

    def refresh_plugins(self):
        if self._plugins_busy:
            return
        self._plugins_busy = True
        if self.plugins_status:
            self.plugins_status.setText("Connecting to GitHub and reading the Plugins folder...")
        if self.plugins_tree:
            self.plugins_tree.clear()
        if self.plugins_preview:
            self.plugins_preview.clear()

        def work():
            try:
                request = urllib.request.Request(
                    PLUGIN_REPO_API,
                    headers={"User-Agent": "PocketTerm-Plugin-Browser", "Accept": "application/vnd.github+json"},
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("truncated"):
                    raise RuntimeError("GitHub returned a truncated repository listing.")
                entries = []
                for item in payload.get("tree", []):
                    path = str(item.get("path", ""))
                    if path.startswith("Plugins/") and item.get("type") == "blob" and path.lower().endswith(".py"):
                        entries.append({
                            "path": path,
                            "name": Path(path).name,
                            "size": int(item.get("size") or 0),
                            "sha": item.get("sha", ""),
                        })
                entries.sort(key=lambda item: item["path"].lower())
                self.plugins_loaded.emit(entries, f"Loaded {len(entries)} file(s) from the online Plugins folder.")
            except Exception as exc:
                self.plugins_loaded.emit([], f"Could not load the plugin repository: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_plugins_loaded(self, entries, message):
        self._plugins_busy = False
        self._plugin_entries = {item["path"]: item for item in entries}
        if self.plugins_tree:
            self.plugins_tree.clear()
            folders = {"": self.plugins_tree.invisibleRootItem()}
            for entry in entries:
                parts = entry["path"].split("/")
                parent_path = ""
                for folder_name in parts[:-1]:
                    folder_path = f"{parent_path}/{folder_name}".strip("/")
                    if folder_path not in folders:
                        folder_item = QTreeWidgetItem([folder_name, "Folder"])
                        folder_item.setData(0, Qt.UserRole, "")
                        folders[parent_path].addChild(folder_item)
                        folders[folder_path] = folder_item
                    parent_path = folder_path
                filename = parts[-1]
                suffix = Path(filename).suffix.lower().lstrip(".") or "file"
                file_item = QTreeWidgetItem([filename, suffix.upper()])
                file_item.setData(0, Qt.UserRole, entry["path"])
                file_item.setToolTip(0, entry["path"])
                file_item.setToolTip(1, f'{entry["size"]:,} bytes')
                folders[parent_path].addChild(file_item)
            self.plugins_tree.expandToDepth(1)
        if self.plugins_status:
            self.plugins_status.setText(message)

    def _plugin_item_clicked(self, item, column=0):
        path = item.data(0, Qt.UserRole)
        if not path or path not in self._plugin_entries:
            return
        self._plugin_preview_path = path
        if self.plugins_preview:
            self.plugins_preview.setPlainText(f"Loading {path} ...")
        def work():
            try:
                url = PLUGIN_RAW_BASE + urllib.parse.quote(path, safe="/")
                request = urllib.request.Request(url, headers={"User-Agent": "PocketTerm-Plugin-Browser"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = response.read(512 * 1024 + 1)
                if len(data) > 512 * 1024:
                    content = "[Preview limited to 512 KB. Download the file to inspect it locally.]"
                else:
                    content = data.decode("utf-8", errors="replace")
                self.plugin_preview_ready.emit(path, content)
            except Exception as exc:
                self.plugin_preview_ready.emit(path, f"Preview unavailable: {exc}")
        threading.Thread(target=work, daemon=True).start()

    def _on_plugin_preview_ready(self, path, content):
        if path == self._plugin_preview_path and self.plugins_preview:
            self.plugins_preview.setPlainText(content)

    def download_selected_plugin(self):
        if self._plugins_busy:
            return
        path = self._plugin_preview_path
        if not path or path not in self._plugin_entries:
            QMessageBox.information(self, "Select a plugin file", "Choose a file in the repository tree first.")
            return
        self._plugins_busy = True
        if self.plugins_status:
            self.plugins_status.setText(f"Downloading {path}...")
        def work():
            try:
                # Only accept files inside the repository's Plugins directory.
                relative = Path(*path.split("/"))
                if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                        or relative.parts[0] != "Plugins" or relative.suffix.lower() != ".py"):
                    raise ValueError("Only .py files inside the repository Plugins folder can be downloaded.")
                url = PLUGIN_RAW_BASE + urllib.parse.quote(path, safe="/")
                request = urllib.request.Request(url, headers={"User-Agent": "PocketTerm-Plugin-Browser"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read()
                destination = APP_DIR / "Plugins" / Path(*relative.parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                self.plugin_action_done.emit(True, f"Downloaded {path} to {destination}. The file was not executed.")
            except Exception as exc:
                self.plugin_action_done.emit(False, f"Download failed: {exc}")
        threading.Thread(target=work, daemon=True).start()

    def clone_plugins_folder(self):
        if self._plugins_busy:
            return
        git = find_git(self.settings)
        if not git:
            QMessageBox.warning(self, "Git not found", "Configure Git in Settings or add git.exe to PATH.")
            return
        target_plugins = APP_DIR / "Plugins"
        if target_plugins.exists() and any(target_plugins.iterdir()):
            answer = QMessageBox.question(
                self, "Merge plugin files",
                f"The destination already contains files:\n{target_plugins}\n\n"
                "The repository files will be merged into this folder. Existing files with the same names may be replaced.",
            )
            if answer != QMessageBox.Yes:
                return
        self._plugins_busy = True
        if self.plugins_status:
            self.plugins_status.setText("Cloning only the Plugins folder with Git sparse checkout...")
        def work():
            temp_repo = APP_DIR / "Temp" / "PocketTermPluginsSparse"
            try:
                if temp_repo.exists():
                    shutil.rmtree(temp_repo, ignore_errors=True)
                temp_repo.parent.mkdir(parents=True, exist_ok=True)
                target_plugins.mkdir(parents=True, exist_ok=True)
                clone_cmd = [
                    str(git), "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                    PLUGIN_REPO_URL, str(temp_repo),
                ]
                result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=180)
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout or "git clone failed").strip())
                sparse = subprocess.run(
                    [str(git), "-C", str(temp_repo), "sparse-checkout", "set", "Plugins"],
                    capture_output=True, text=True, timeout=60,
                )
                if sparse.returncode:
                    raise RuntimeError((sparse.stderr or sparse.stdout or "sparse-checkout failed").strip())
                source_plugins = temp_repo / "Plugins"
                if not source_plugins.is_dir():
                    raise RuntimeError("The repository does not contain a Plugins folder on main.")
                for source in source_plugins.rglob("*"):
                    if source.is_file() and ".git" not in source.parts:
                        relative = source.relative_to(source_plugins)
                        destination = target_plugins / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
                self.plugin_action_done.emit(
                    True, f"Cloned only the repository's Plugins folder into {target_plugins}. "
                    "Git sparse checkout kept the rest of the working tree out."
                )
            except Exception as exc:
                self.plugin_action_done.emit(False, f"Plugin folder clone failed: {exc}")
            finally:
                shutil.rmtree(temp_repo, ignore_errors=True)
        threading.Thread(target=work, daemon=True).start()

    def _on_plugin_action_done(self, ok, message):
        self._plugins_busy = False
        if self.plugins_status:
            self.plugins_status.setText(message)
        if ok:
            log.info(message)
            self.refresh_local_plugins()
        else:
            log.warning(message)

    def _refresh_dashboard(self):
        if not self.dashboard_labels:
            return
        cpu_text = "Live metrics need psutil."
        memory_text = "Live metrics need psutil."
        cpu_value = memory_value = 0.0
        cpu_ok = memory_ok = False
        if psutil is not None:
            try:
                cpu_value = psutil.cpu_percent(interval=None)
                cpu_text = f"{cpu_value:.0f}% in use\n{psutil.cpu_count(logical=True) or 'Unknown'} logical processors"
                cpu_ok = True
            except Exception as exc:
                cpu_text = f"CPU metrics unavailable: {exc}"
            try:
                mem = psutil.virtual_memory()
                memory_value = mem.percent
                memory_text = f"{memory_value:.0f}% used\n{format_size(mem.used)} / {format_size(mem.total)}"
                memory_ok = True
            except Exception as exc:
                memory_text = f"Memory metrics unavailable: {exc}"
        self.dashboard_labels["cpu"].setText(cpu_text)
        self.dashboard_labels["memory"].setText(memory_text)
        if "cpu" in self.dashboard_donuts:
            self.dashboard_donuts["cpu"].set_value(cpu_value, cpu_ok)
        if "memory" in self.dashboard_donuts:
            self.dashboard_donuts["memory"].set_value(memory_value, memory_ok)

        try:
            usage = shutil.disk_usage(PORTABLE_ROOT)
            disk_text = (
                f"{PORTABLE_ROOT}\n{format_size(usage.free)} free of {format_size(usage.total)}"
                f"\n{format_size(usage.used)} used"
            )
        except OSError as exc:
            disk_text = f"Portable drive unavailable: {exc}"
        self.dashboard_labels["disk"].setText(disk_text)

        tools = []
        git = find_git(self.settings)
        bash = find_bash(self.settings)
        tools.append("Git: " + ("Ready" if git else "Not found"))
        tools.append("Git Bash: " + ("Ready" if bash else "Not found"))
        for name, command in (("Python", "python"), ("Node.js", "node"),
                              ("Java", "java"), ("ADB", "adb"),
                              ("Ollama", "ollama")):
            found = shutil.which(command)
            tools.append(name + ": " + ("Ready" if found else "Not detected"))
        self.dashboard_labels["environment"].setText("\n".join(tools))

    def _navigate_file_path(self):
        if self.file_model is None:
            return
        raw = self.file_path_edit.text().strip().strip('"')
        if not raw:
            return
        target = Path(os.path.expandvars(os.path.expanduser(raw)))
        try:
            target = target.resolve()
        except OSError:
            pass
        if not target.exists() or not target.is_dir():
            QMessageBox.warning(self, "Folder not found", f"This folder does not exist:\n{target}")
            return
        self.file_tree.setRootIndex(self.file_model.index(str(target)))
        self.file_path_edit.setText(str(target))

    def _file_go_up(self):
        current = Path(self.file_path_edit.text().strip() or str(PORTABLE_ROOT))
        parent = current.parent
        if parent != current:
            self.file_path_edit.setText(str(parent))
            self._navigate_file_path()

    def _file_go_home(self):
        self.file_path_edit.setText(str(PORTABLE_ROOT))
        self._navigate_file_path()

    def _file_item_activated(self, index):
        path = Path(self.file_model.filePath(index))
        if path.is_dir():
            self.file_tree.setRootIndex(self.file_model.index(str(path)))
            self.file_path_edit.setText(str(path))
        else:
            self._open_path_in_system(path)

    def _open_selected_file(self):
        if self.file_tree is None:
            return
        index = self.file_tree.currentIndex()
        if not index.isValid():
            return
        path = Path(self.file_model.filePath(index))
        if path.is_dir():
            self.file_tree.setRootIndex(self.file_model.index(str(path)))
            self.file_path_edit.setText(str(path))
        else:
            self._open_path_in_system(path)

    def _open_path_in_system(self, path):
        try:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                QMessageBox.warning(self, "Could not open file", f"Windows could not open:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Could not open file", str(exc))

    def _build_terminal_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addLayout(self._page_header(
            "Workspace Terminal",
            "Native standalone sandbox console environment. Use + New Tab for more sessions "
            "(Ctrl+T).",
        ))
        layout.addSpacing(20)

        corner = self._button("+  New Tab", self.new_terminal_tab, "CornerButton")
        self.terminals.setCornerWidget(corner, Qt.TopRightCorner)
        layout.addWidget(self.terminals, stretch=1)
        return page

    def _build_vscode_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)

        buttons = (
            self._button("Open Folder...", self.open_folder_in_vscode),
            self._button("Start / Restart VS Code", self.restart_vscode, "Primary"),
        )
        layout.addLayout(self._page_header(
            "VS Code", "VS Code running inside PocketTerm. Drop a file or folder on the window to open it.",
            buttons
        ))
        layout.addSpacing(20)

        frame = QFrame()
        frame.setObjectName("TerminalFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(3, 3, 3, 3)
        frame_layout.addWidget(self.vscode)
        layout.addWidget(frame, stretch=1)
        return page

    def _build_projects_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)

        buttons = (
            self._button("New Project...", self.new_project),
            self._button("Clone Repo...", self.clone_repo),
        )
        layout.addLayout(self._page_header(
            "Projects",
            "Folders inside your Projects directory. Double-click one to open it in VS Code. "
            "Pinned projects come first, then recently opened ones.",
            buttons,
        ))
        layout.addSpacing(16)

        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText("Search projects...  (Ctrl+F)")
        self.project_search.setClearButtonEnabled(True)
        self.project_search.textChanged.connect(lambda _t: self._render_projects())
        layout.addWidget(self.project_search)
        layout.addSpacing(8)

        self.project_list = QListWidget()
        self._new_row_delegate(self.project_list)
        self.project_list.itemDoubleClicked.connect(lambda _item: self.open_project_in_vscode())
        layout.addWidget(self.project_list, stretch=1)

        self.projects_status = QLabel("")
        self.projects_status.setObjectName("Muted")
        self.projects_status.setWordWrap(True)
        layout.addWidget(self.projects_status)

        row = QHBoxLayout()
        row.addWidget(self._button("Open in VS Code", self.open_project_in_vscode, "Primary"))
        row.addWidget(self._button("Open Terminal Here", self.open_terminal_here))
        row.addWidget(self._button("Pin / Unpin", self.toggle_pin_selected))
        row.addWidget(self._button("Show in Explorer", self.show_project_in_explorer))
        row.addWidget(self._button("Refresh", self.refresh_projects))
        row.addStretch()
        layout.addLayout(row)
        return page

    def _build_scripts_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)

        buttons = (self._button("Open Scripts Folder", lambda: open_path(APP_DIR / "Scripts")),)
        layout.addLayout(self._page_header(
            "Scripts",
            "Scripts in your Scripts folder (.bat, .cmd, .ps1, .py, .sh). Double-click to run one "
            "in its own console, or use Run with Log to see the output below.",
            buttons,
        ))
        layout.addSpacing(16)

        # Top half: search, list, arguments, buttons. Bottom half: the log. Drag the divider to resize.
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.script_search = QLineEdit()
        self.script_search.setPlaceholderText("Search scripts...  (Ctrl+F)")
        self.script_search.setClearButtonEnabled(True)
        self.script_search.textChanged.connect(lambda _t: self._render_scripts())
        top_layout.addWidget(self.script_search)
        top_layout.addSpacing(4)

        self.script_list = QListWidget()
        self._new_row_delegate(self.script_list)
        self.script_list.itemDoubleClicked.connect(lambda _item: self.run_selected_script())
        self.script_list.currentItemChanged.connect(self._script_selected)
        top_layout.addWidget(self.script_list, stretch=1)

        args_row = QHBoxLayout()
        args_label = QLabel("Arguments")
        args_label.setObjectName("Muted")
        args_row.addWidget(args_label)
        self.script_args_edit = QLineEdit()
        self.script_args_edit.setPlaceholderText("Optional arguments for the selected script (remembered per script)")
        self.script_args_edit.editingFinished.connect(self._save_script_args)
        args_row.addWidget(self.script_args_edit, stretch=1)
        top_layout.addLayout(args_row)

        self.scripts_status = QLabel("")
        self.scripts_status.setObjectName("Muted")
        self.scripts_status.setWordWrap(True)
        top_layout.addWidget(self.scripts_status)

        row = QHBoxLayout()
        row.addWidget(self._button("Run in Console", self.run_selected_script, "Primary"))
        self.btn_run_log = self._button("Run with Log", self.run_selected_script_logged)
        row.addWidget(self.btn_run_log)
        self.btn_stop_script = self._button("Stop", self.stop_script, "Danger")
        self.btn_stop_script.setEnabled(False)
        row.addWidget(self.btn_stop_script)
        row.addWidget(self._button("Edit in VS Code", self.edit_selected_script))
        row.addWidget(self._button("Refresh", self.refresh_scripts))
        row.addStretch()
        top_layout.addLayout(row)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        log_header = QHBoxLayout()
        log_title = QLabel("Output")
        log_title.setObjectName("SectionTitle")
        log_header.addWidget(log_title)
        log_header.addStretch()
        log_header.addWidget(self._button("Clear", lambda: self.log_view.clear()))
        bottom_layout.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setPlaceholderText("Output from 'Run with Log' appears here.")
        bottom_layout.addWidget(self.log_view, stretch=1)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)
        return page

    def _build_tools_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addLayout(self._page_header(
            "Tools",
            "Run other programs inside PocketTerm. Pick one and press Launch, or add your own "
            "with Add Tool...",
        ))
        layout.addSpacing(16)

        row = QHBoxLayout()
        self.tool_combo = QComboBox()
        self.tool_combo.setMinimumWidth(220)
        row.addWidget(self.tool_combo)
        row.addWidget(self._button("Launch", self.launch_tool, "Primary"))
        row.addWidget(self._button("Add Tool...", self.add_tool))
        row.addWidget(self._button("Edit...", self.edit_tool))
        row.addWidget(self._button("Remove", self.remove_tool, "Danger"))
        row.addStretch()
        layout.addLayout(row)
        layout.addSpacing(12)

        self.tool_tabs = QTabWidget()
        self.tool_tabs.setObjectName("Tabs")
        self.tool_tabs.setMovable(True)
        self.tool_tabs.tabCloseRequested.connect(self.close_tool_tab)
        layout.addWidget(self.tool_tabs, stretch=1)

        self.tools_empty = QLabel("No tool is running.\nPick one above and press Launch.")
        self.tools_empty.setObjectName("Muted")
        self.tools_empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.tools_empty, stretch=1)

        self._refresh_tool_combo()
        self._update_tools_empty()
        return page

    def _build_snippets_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addLayout(self._page_header(
            "Snippets",
            "Saved commands for the current terminal tab. Run types the command and presses "
            "Enter; Paste only types it so you can edit it first. Snippets also appear in "
            "Ctrl+K.",
            (self._button("Add Snippet...", self.add_snippet, "Primary"),),
        ))
        layout.addSpacing(16)

        self.snippet_list = QListWidget()
        self._new_row_delegate(self.snippet_list)
        self.snippet_list.itemDoubleClicked.connect(lambda _item: self.run_selected_snippet(True))
        layout.addWidget(self.snippet_list, stretch=1)

        self.snippets_status = QLabel("")
        self.snippets_status.setObjectName("Muted")
        self.snippets_status.setWordWrap(True)
        layout.addWidget(self.snippets_status)

        row = QHBoxLayout()
        row.addWidget(self._button("Run in Terminal", lambda: self.run_selected_snippet(True),
                                   "Primary"))
        row.addWidget(self._button("Paste (no Enter)", lambda: self.run_selected_snippet(False)))
        row.addWidget(self._button("Edit...", self.edit_snippet))
        row.addWidget(self._button("Remove", self.remove_snippet, "Danger"))
        row.addStretch()
        layout.addLayout(row)

        self._render_snippets()
        return page

    def _build_ports_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addLayout(self._page_header(
            "Ports",
            "Programs listening on TCP ports on this PC. Handy when a dev server is stuck on "
            "port 3000. Refreshes every few seconds while this page is open.",
            (self._button("Refresh", self.refresh_ports),),
        ))
        layout.addSpacing(16)

        self.port_search = QLineEdit()
        self.port_search.setPlaceholderText("Filter by port, program name or PID...")
        self.port_search.setClearButtonEnabled(True)
        self.port_search.textChanged.connect(lambda _t: self._render_ports())
        layout.addWidget(self.port_search)
        layout.addSpacing(8)

        self.port_list = QListWidget()
        self._new_row_delegate(self.port_list)
        self.port_list.itemDoubleClicked.connect(lambda _item: self.open_port_in_browser())
        layout.addWidget(self.port_list, stretch=1)

        self.ports_status = QLabel("")
        self.ports_status.setObjectName("Muted")
        self.ports_status.setWordWrap(True)
        layout.addWidget(self.ports_status)

        row = QHBoxLayout()
        if WEBVIEW_OK:
            row.addWidget(self._button("Preview in PocketTerm", self.preview_selected_port,
                                       "Primary"))
            row.addWidget(self._button("Open in Browser", self.open_port_in_browser))
        else:
            row.addWidget(self._button("Open in Browser", self.open_port_in_browser, "Primary"))
        row.addWidget(self._button("Kill Process", self.kill_selected_port, "Danger"))
        row.addWidget(self._button("Copy Port", self.copy_selected_port))
        row.addStretch()
        layout.addLayout(row)
        return page

    # ---------- snippets ----------

    def _snippets(self):
        return [
            s for s in self.settings.get("snippets")
            if isinstance(s, dict) and s.get("name") and s.get("command")
        ]

    def _render_snippets(self):
        keep = self.snippet_list.currentRow()
        self.snippet_list.clear()
        for s in self._snippets():
            item = QListWidgetItem(s["name"])
            item.setToolTip(s["command"])
            item.setData(ROLE_TITLE, s["name"])
            item.setData(ROLE_SUB, s["command"].replace("\n", "  ↵  "))
            item.setData(ROLE_BADGE, "")
            self.snippet_list.addItem(item)
        if self.snippet_list.count():
            self.snippet_list.setCurrentRow(max(0, min(keep, self.snippet_list.count() - 1)))
        else:
            self.snippets_status.setText("No snippets yet. Use Add Snippet... to save a command.")

    def _send_command(self, command, enter):
        term = self.terminals.current_terminal()
        if term is None or not term.hwnd:
            self.show_page(PAGE_SNIPPETS)
            self.snippets_status.setText(
                "The current terminal tab isn't running. Start it on the Terminal page first."
            )
            return False
        self.show_page(PAGE_TERMINAL)
        ok = term.send_text(command + ("\r" if enter else ""))
        if ok:
            term.focus_soon()
        return ok

    def run_selected_snippet(self, enter):
        row = self.snippet_list.currentRow()
        snippets = self._snippets()
        if not (0 <= row < len(snippets)):
            self.snippets_status.setText("Select a snippet first.")
            return
        self._send_command(snippets[row]["command"], enter)

    def add_snippet(self):
        name, ok = QInputDialog.getText(self, "Add snippet", "Name:")
        if not ok or not name.strip():
            return
        command, ok = QInputDialog.getText(self, "Add snippet", "Command to type into the terminal:")
        if not ok or not command.strip():
            return
        snippets = self._snippets()
        snippets.append({"name": name.strip(), "command": command.strip()})
        self.settings.set("snippets", snippets)
        self._render_snippets()
        self.snippet_list.setCurrentRow(self.snippet_list.count() - 1)

    def edit_snippet(self):
        row = self.snippet_list.currentRow()
        snippets = self._snippets()
        if not (0 <= row < len(snippets)):
            self.snippets_status.setText("Select a snippet first.")
            return
        s = snippets[row]
        name, ok = QInputDialog.getText(self, "Edit snippet", "Name:", text=s["name"])
        if not ok or not name.strip():
            return
        command, ok = QInputDialog.getText(self, "Edit snippet", "Command:", text=s["command"])
        if not ok or not command.strip():
            return
        s["name"], s["command"] = name.strip(), command.strip()
        self.settings.set("snippets", snippets)
        self._render_snippets()

    def remove_snippet(self):
        row = self.snippet_list.currentRow()
        snippets = self._snippets()
        if not (0 <= row < len(snippets)):
            return
        del snippets[row]
        self.settings.set("snippets", snippets)
        self._render_snippets()

    # ---------- ports ----------

    def _ports_tick(self):
        if self.stacked_widget.currentIndex() == PAGE_PORTS and self.isVisible():
            self.refresh_ports()

    def refresh_ports(self):
        if self._ports_busy:
            return
        if os.name != "nt":
            self.ports_status.setText("The Ports page is only available on Windows.")
            return
        self._ports_busy = True

        def work():
            try:
                ports = list_listening_ports()
            except Exception:
                log.exception("Port scan failed")
                ports = None
            self.ports_ready.emit(ports)

        threading.Thread(target=work, daemon=True).start()

    def _on_ports(self, ports):
        self._ports_busy = False
        if ports is None:
            self.ports_status.setText("Couldn't read the port list (netstat failed).")
            return
        self._ports_cache = ports
        self._render_ports()

    def _render_ports(self):
        if self._ports_cache is None:
            return
        keep = None
        item = self.port_list.currentItem()
        if item is not None:
            keep = (item.data(ROLE_PORT), item.data(ROLE_PATH))

        query = self.port_search.text().strip().lower()
        shown = [
            p for p in self._ports_cache
            if not query or query in str(p["port"]) or query in p["name"].lower()
            or query == str(p["pid"])
        ]

        self.port_list.setUpdatesEnabled(False)
        self.port_list.clear()
        select_row = -1
        for p in shown:
            it = QListWidgetItem(f":{p['port']}")
            local = p["addr"] in ("127.0.0.1", "::1")
            it.setData(ROLE_TITLE, f":{p['port']}   {p['name']}")
            it.setData(ROLE_SUB, f"PID {p['pid']}  ·  {p['addr']}")
            it.setData(ROLE_BADGE, "local only" if local else "all interfaces")
            it.setData(ROLE_KIND, "ok" if local else "warn")
            it.setData(ROLE_PORT, p["port"])
            it.setData(ROLE_PATH, str(p["pid"]))
            self.port_list.addItem(it)
            if keep and keep == (p["port"], str(p["pid"])):
                select_row = self.port_list.count() - 1
        if select_row >= 0:
            self.port_list.setCurrentRow(select_row)
        self.port_list.setUpdatesEnabled(True)

        total = len(self._ports_cache)
        if query:
            self.ports_status.setText(f"{len(shown)} of {total} listening port(s) match.")
        else:
            self.ports_status.setText(f"{total} listening port(s).")

    def _selected_port(self):
        it = self.port_list.currentItem()
        if it is None:
            self.ports_status.setText("Select a port first.")
            return None
        return int(it.data(ROLE_PORT)), int(it.data(ROLE_PATH)), it.data(ROLE_TITLE)

    def open_port_in_browser(self):
        sel = self._selected_port()
        if sel:
            QDesktopServices.openUrl(QUrl(f"http://localhost:{sel[0]}"))

    def copy_selected_port(self):
        sel = self._selected_port()
        if sel:
            QApplication.clipboard().setText(str(sel[0]))
            self.ports_status.setText(f"Copied {sel[0]} to the clipboard.")

    def kill_selected_port(self):
        sel = self._selected_port()
        if not sel:
            return
        port, pid, title = sel
        if pid in (0, 4):
            self.ports_status.setText("That is a Windows system process and can't be stopped here.")
            return
        extra = ""
        if pid == os.getpid():
            extra = "\n\nThis is PocketTerm itself."
        answer = QMessageBox.question(
            self, "Kill process",
            f"Stop {title.split('   ', 1)[-1]} (PID {pid}) that is using port {port}?\n"
            f"Its child processes will be stopped too.{extra}",
        )
        if answer != QMessageBox.Yes:
            return
        if kill_tree(pid):
            self.ports_status.setText(f"Stopped PID {pid}.")
        else:
            self.ports_status.setText(
                f"Couldn't stop PID {pid}. It may need administrator rights, or it already exited."
            )
        QTimer.singleShot(800, self.refresh_ports)

    # ---------- web preview ----------

    def _build_web_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        self.ai_btn = self._button("Start PocketAi", self.ai_toggle, "Primary")
        self.ai_log_btn = self._button("Show log", self.ai_toggle_log)
        layout.addLayout(self._page_header(
            "PocketAi",
            "PocketAi runs hidden in the background through Git Bash, and its web page is "
            "shown here. Type a port (like 3000) or a full address and press Enter to look at "
            "something else.",
            (self.ai_btn, self.ai_log_btn,
             self._button("Open in Browser", self.web_open_external)),
        ))
        layout.addSpacing(8)

        self.ai_status = QLabel("PocketAi isn't running.")
        self.ai_status.setObjectName("Muted")
        self.ai_status.setWordWrap(True)
        layout.addWidget(self.ai_status)

        self.ai_log = QPlainTextEdit()
        self.ai_log.setObjectName("Log")
        self.ai_log.setReadOnly(True)
        self.ai_log.setMaximumBlockCount(2000)
        self.ai_log.setMaximumHeight(170)
        self.ai_log.setPlaceholderText("Output from PocketAi appears here.")
        self.ai_log.hide()
        layout.addWidget(self.ai_log)
        layout.addSpacing(8)

        if not WEBVIEW_OK:
            msg = QLabel(
                "The built-in web view isn't available because QtWebEngine isn't installed.\n\n"
                "Install it with:   pip install PySide6-Addons\n"
                "then restart PocketTerm. Until then, 'Open in Browser' uses your normal browser."
            )
            msg.setObjectName("Muted")
            msg.setWordWrap(True)
            msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(msg)
            layout.addStretch()
            return page

        self.web_view = QWebEngineView()

        bar = QHBoxLayout()
        bar.addWidget(self._button("←", self.web_view.back))
        bar.addWidget(self._button("→", self.web_view.forward))
        bar.addWidget(self._button("↻", self.web_view.reload))
        self.web_url_edit = QLineEdit(str(self.settings.get("web_url")) or DEFAULT_WEB_URL)
        self.web_url_edit.setPlaceholderText("Port or address, e.g. 3000 or http://localhost:8080")
        self.web_url_edit.returnPressed.connect(self.web_go)
        bar.addWidget(self.web_url_edit, stretch=1)
        bar.addWidget(self._button("Go", self.web_go, "Primary"))
        bar.addWidget(self._button("Hard Reload", self.web_hard_reload))
        bar.addWidget(self._button("DevTools", self.web_devtools))
        layout.addLayout(bar)
        layout.addSpacing(8)

        frame = QFrame()
        frame.setObjectName("TerminalFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(3, 3, 3, 3)
        frame_layout.addWidget(self.web_view)
        layout.addWidget(frame, stretch=1)

        self.web_status = QLabel("")
        self.web_status.setObjectName("Muted")
        self.web_status.setWordWrap(True)
        layout.addWidget(self.web_status)

        self.web_view.loadStarted.connect(lambda: self.web_status.setText("Loading..."))
        self.web_view.loadFinished.connect(self._web_loaded)
        self.web_view.urlChanged.connect(self._web_url_changed)
        return page

    def _web_first_show(self):
        """First time the PocketAi tab is shown: start PocketAi (hidden) or load the address."""
        if self._ai_checked:
            return
        self._ai_checked = True
        if self.settings.get("pocketai_auto_start") and not self.ai_running():
            self.ai_start()
        elif self.web_view is not None:
            self.web_go()

    def web_go(self):
        if self.web_view is None:
            return
        url = normalize_url(self.web_url_edit.text())
        self._web_started = True
        self.web_url_edit.setText(url)
        self.web_view.load(QUrl(url))

    def web_hard_reload(self):
        if self.web_view is not None:
            self.web_view.page().triggerAction(QWebEnginePage.ReloadAndBypassCache)

    def web_devtools(self):
        """Toggle the small floating DevTools panel (drag its title bar to move it)."""
        if self.web_view is None:
            return
        if self._devtools is None:
            self._devtools = DevToolsWindow(self)
            self.web_view.page().setDevToolsPage(self._devtools.view.page())
            g = self.frameGeometry()
            self._devtools.move(g.right() - self._devtools.width() - 40, g.top() + 100)
        if self._devtools.isVisible():
            self._devtools.hide()
        else:
            self._devtools.show()
            self._devtools.raise_()

    def web_open_external(self):
        text = self.web_url_edit.text() if self.web_view is not None \
            else str(self.settings.get("web_url"))
        QDesktopServices.openUrl(QUrl(normalize_url(text)))

    def _web_url_changed(self, url):
        if not self.web_url_edit.hasFocus():
            self.web_url_edit.setText(url.toString())

    def _web_loaded(self, ok):
        url = self.web_view.url().toString()
        if ok:
            self.web_status.setText(f"Loaded {url}")
            if url and url != self.settings.get("web_url"):
                self.settings.set("web_url", url)
        else:
            self.web_status.setText(
                f"Couldn't load {url or 'the page'}. Is the server running on that port?"
            )

    def preview_selected_port(self):
        sel = self._selected_port()
        if not sel:
            return
        if self.web_view is None:
            self.open_port_in_browser()
            return
        self._ai_checked = True  # previewing a port shouldn't also auto-start PocketAi
        self.web_url_edit.setText(f"http://localhost:{sel[0]}")
        self.show_page(PAGE_WEB)
        self.web_go()

    # ---------- PocketAi (hidden Git Bash process) ----------

    _ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")

    def ai_running(self):
        return self.ai_proc is not None and self.ai_proc.state() != QProcess.NotRunning

    def _ai_say(self, text, ok=None):
        """Status line under the header. ok=True green, False red, None neutral."""
        if ok is None:
            self.ai_status.setStyleSheet("")
            self.ai_status.setText(text)
        else:
            self._set_status(self.ai_status, ok, text)

    def _ai_refresh_button(self):
        running = self.ai_running()
        self.ai_btn.setText("Stop PocketAi" if running else "Start PocketAi")
        self.ai_btn.setObjectName("Danger" if running else "Primary")
        self.ai_btn.style().unpolish(self.ai_btn)
        self.ai_btn.style().polish(self.ai_btn)

    def ai_toggle(self):
        if self.ai_running():
            self.ai_stop()
        else:
            self.ai_start()

    def ai_toggle_log(self):
        show = not self.ai_log.isVisible()
        self.ai_log.setVisible(show)
        self.ai_log_btn.setText("Hide log" if show else "Show log")

    def ai_start(self):
        if self.ai_running():
            return
        bash = find_bash(self.settings)
        if not bash:
            self._ai_say("Git Bash (bash.exe) not found. Set its path in Settings.", False)
            return
        command = str(self.settings.get("pocketai_command")).strip()
        if not command:
            self._ai_say("No PocketAi command set. Add it in Settings > PocketAi.", False)
            return

        proc = QProcess(self)
        proc.setWorkingDirectory(str(APP_DIR))
        pe = QProcessEnvironment()
        for k, v in git_env(self.settings).items():
            pe.insert(k, v)
        proc.setProcessEnvironment(pe)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._ai_output)
        proc.finished.connect(self._ai_finished)
        proc.errorOccurred.connect(self._ai_error)

        # Make sure no console window appears (best effort; Qt already hides it in most cases).
        if os.name == "nt" and hasattr(proc, "setCreateProcessArgumentsModifier"):
            def no_window(args):
                try:
                    args.flags |= CREATE_NO_WINDOW
                except Exception:
                    pass
            try:
                proc.setCreateProcessArgumentsModifier(no_window)
            except Exception:
                log.exception("Could not set the no-window flag")

        self.ai_proc = proc
        self._ai_last_line = ""
        self.ai_log.clear()
        # Same approach as VS Code: a login + interactive shell so aliases and PATH are loaded.
        proc.start(str(bash), ["-l", "-i", "-c", command])
        self._ai_deadline = time.time() + 180
        self._ai_timer.start()
        self._ai_refresh_button()
        self._ai_say(f"Starting '{command}' in the background...")
        log.info("Started PocketAi: %s", command)

    def ai_stop(self):
        if not self.ai_running():
            return
        self._ai_timer.stop()
        pid = self.ai_proc.processId()
        # Kill the whole tree so the actual program (a child of bash) stops too.
        if not kill_tree(pid):
            self.ai_proc.kill()
        self._ai_say("Stopping PocketAi...")

    def _ai_output(self):
        if self.ai_proc is None:
            return
        data = bytes(self.ai_proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        data = self._ANSI.sub("", data).replace("\r\n", "\n").replace("\r", "\n")
        if not data:
            return
        cursor = self.ai_log.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(data)
        self.ai_log.setTextCursor(cursor)
        self.ai_log.ensureCursorVisible()
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        if lines:
            self._ai_last_line = lines[-1]

    def _ai_finished(self, code, _status):
        self._ai_output()
        self._ai_timer.stop()
        proc = self.ai_proc
        self.ai_proc = None
        if proc is not None:
            proc.deleteLater()
        self._ai_refresh_button()
        if code == 0:
            self._ai_say("PocketAi has stopped.")
        else:
            tail = f" Last output: {self._ai_last_line}" if self._ai_last_line else ""
            self._ai_say(f"PocketAi stopped (exit code {code}).{tail}", False)

    def _ai_error(self, error):
        proc = self.ai_proc
        detail = proc.errorString() if proc is not None else str(error)
        log.warning("PocketAi process error (%s): %s", error, detail)
        if error == QProcess.FailedToStart:
            self._ai_timer.stop()
            self.ai_proc = None
            if proc is not None:
                proc.deleteLater()
            self._ai_refresh_button()
            self._ai_say(f"Couldn't start PocketAi: {detail}", False)

    def _ai_target(self):
        text = self.web_url_edit.text() if self.web_view is not None \
            else str(self.settings.get("web_url"))
        url = QUrl(normalize_url(text))
        return (url.host() or "localhost"), url.port(80)

    def _ai_poll(self):
        """Wait until something is listening on the PocketAi address, then show it."""
        if not self.ai_running():
            self._ai_timer.stop()
            return
        host, port = self._ai_target()
        try:
            socket.create_connection((host, port), timeout=0.3).close()
            ready = True
        except OSError:
            ready = False
        if ready:
            self._ai_timer.stop()
            self._ai_say(f"PocketAi is running at {host}:{port}.", True)
            if self.web_view is not None:
                self.web_go()
            else:
                self._ai_say(
                    f"PocketAi is running at {host}:{port}. The built-in web view isn't "
                    "available, so use Open in Browser.", True)
        elif time.time() > self._ai_deadline:
            self._ai_timer.stop()
            self._ai_say(
                f"PocketAi started, but nothing is listening on port {port} yet. "
                "Press Show log to see what it printed, or change the address above.", False)

    def _build_drives_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addLayout(self._page_header(
            "Drives",
            "Free space on every drive in this PC. Updates automatically while this page is open.",
            (self._button("Refresh", self.refresh_drives),),
        ))
        layout.addSpacing(16)

        self.drives_summary = QLabel("Scanning drives...")
        self.drives_summary.setObjectName("Muted")
        layout.addWidget(self.drives_summary)
        layout.addSpacing(8)

        alert_row = QHBoxLayout()
        self._check_row(alert_row, "low_space_alerts", "Alert me when a drive has less than",
                        after=self._on_alert_setting_changed)
        self.low_space_spin = QSpinBox()
        self.low_space_spin.setRange(1, 100000)
        self.low_space_spin.setSuffix(" GB")
        self.low_space_spin.setButtonSymbols(QSpinBox.NoButtons)
        self.low_space_spin.setKeyboardTracking(False)  # apply on Enter / leaving the box
        self.low_space_spin.setValue(int(self.settings.get("low_space_gb")))
        self.low_space_spin.valueChanged.connect(self._low_space_changed)
        alert_row.addWidget(self.low_space_spin)
        alert_row.addWidget(QLabel("free"))
        alert_row.addStretch()
        layout.addLayout(alert_row)

        self.drives_status = QLabel("")
        self.drives_status.setObjectName("Muted")
        self.drives_status.setWordWrap(True)
        layout.addWidget(self.drives_status)
        layout.addSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self.drives_layout = QVBoxLayout(content)
        self.drives_layout.setContentsMargins(0, 0, 12, 12)
        self.drives_layout.setSpacing(12)
        self.drives_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)
        return page

    # ---------- drives ----------

    def _drives_tick(self):
        self._drive_tick_count += 1
        on_page = self.stacked_widget.currentIndex() == PAGE_DRIVES and self.isVisible()
        background = bool(self.settings.get("low_space_alerts")) and self._drive_tick_count % 4 == 0
        if on_page or background:
            self.refresh_drives()

    def _on_alert_setting_changed(self):
        self._low_alerted.clear()
        self._drives_sig = None
        self.refresh_drives()

    def _low_space_changed(self, value):
        self.settings.set("low_space_gb", int(value))
        self._on_alert_setting_changed()

    def _low_space_bytes(self):
        return int(self.settings.get("low_space_gb")) * 1024 ** 3

    def _notify(self, title, text):
        """Show a tray balloon (briefly showing the tray icon if it is normally hidden)."""
        if not self.tray:
            return
        was_visible = self.tray.isVisible()
        self.tray.setVisible(True)
        QTimer.singleShot(
            500, lambda: self.tray.showMessage(title, text, QSystemTrayIcon.Warning, 10000)
        )
        if not was_visible:
            QTimer.singleShot(16000, self._apply_tray)

    def _check_low_space(self, drives):
        if not self.settings.get("low_space_alerts"):
            self._low_alerted.clear()
            return
        limit = self._low_space_bytes()
        low_now = {d["root"] for d in drives if d["free"] < limit}
        self._low_alerted &= low_now  # forget drives that have recovered
        messages = []
        for d in drives:
            if d["root"] not in low_now:
                continue
            messages.append(f"{d['root']} has only {format_size(d['free'])} free")
            if d["root"] not in self._low_alerted:
                self._low_alerted.add(d["root"])
                self._notify("Low disk space",
                             f"{d['root']} has only {format_size(d['free'])} free.")
        if messages:
            self._set_status(self.drives_status, False, "Low space: " + "; ".join(messages) + ".")
        elif self.drives_status.text().startswith("Low space"):
            self.drives_status.setText("")

    def _stop_things_on(self, drive):
        """Stop anything PocketTerm is running from the given drive so it can be ejected."""
        msg = "Stopped so the drive can be ejected."
        if drive_of(find_launcher(self.settings)) == drive:
            for t in self.terminals.terminals():
                if t.is_running():
                    t.stop(msg)
        bash = find_bash(self.settings)
        configured_vscode = resolve_configured_path(self.settings.get("vscode_exe"))
        vs_paths = [bash, configured_vscode]
        if self.vscode.is_running() and any(p and drive_of(p) == drive for p in vs_paths):
            self.vscode.stop(msg)
        for i in range(self.tool_tabs.count() - 1, -1, -1):
            w = self.tool_tabs.widget(i)
            if isinstance(w, EmbeddedTool) and drive_of(w.exe) == drive:
                self.close_tool_tab(i)
        if self._script_running() and self._script_path and drive_of(self._script_path) == drive:
            self.stop_script()

    def eject_drive(self, root):
        drive = drive_of(root)
        if not drive:
            return

        if drive_of(APP_DIR) == drive:
            answer = QMessageBox.question(
                self, "Eject drive",
                f"PocketTerm is running from {drive}.\n\n"
                f"It will stop everything, close, and then eject {drive}. Continue?",
            )
            if answer != QMessageBox.Yes:
                return
            error = schedule_eject_after_exit(drive)
            if error:
                self._set_status(self.drives_status, False, error)
                return
            self._skip_close_confirm = True
            self.close()
            return

        answer = QMessageBox.question(
            self, "Eject drive",
            f"Eject {drive}?\n\nAnything running from it inside PocketTerm will be stopped first.",
        )
        if answer != QMessageBox.Yes:
            return
        self._stop_things_on(drive)
        self.drives_status.setStyleSheet("")
        self.drives_status.setText(f"Ejecting {drive} ...")

        def work():
            ok, message = eject_drive_now(drive)
            self.task_done.emit("eject", ok, message)

        threading.Thread(target=work, daemon=True).start()

    def refresh_drives(self):
        if self._drives_busy:
            return
        self._drives_busy = True

        def work():
            # Off the UI thread: an unreachable network drive can block for a while.
            try:
                drives = collect_drives()
            except Exception:
                log.exception("Drive scan failed")
                drives = []
            self.drives_ready.emit(drives)

        threading.Thread(target=work, daemon=True).start()

    def _on_drives(self, drives):
        self._drives_busy = False
        self._check_low_space(drives)
        limit = self._low_space_bytes() if self.settings.get("low_space_alerts") else 0
        sig = (limit,) + tuple((d["root"], d["free"], d["total"]) for d in drives)
        if sig == self._drives_sig:
            return  # nothing changed, don't rebuild the cards
        self._drives_sig = sig

        # Remove the old cards (the last layout item is the stretch).
        while self.drives_layout.count() > 1:
            item = self.drives_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not drives:
            self.drives_summary.setText("No drives found.")
            return

        total_free = sum(d["free"] for d in drives)
        self.drives_summary.setText(
            f"{len(drives)} drive(s)  ·  {format_size(total_free)} free in total"
        )
        for i, d in enumerate(drives):
            self.drives_layout.insertWidget(i, self._drive_card(d, limit))

    def _drive_card(self, d, limit=0):
        used = d["total"] - d["free"]
        pct = round(used * 100 / d["total"]) if d["total"] else 0
        low = bool(limit) and d["free"] < limit
        color = ERR_COLOR if (pct >= 90 or low) else WARN_COLOR if pct >= 75 else OK_COLOR

        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel(f"{d['root']}   {d['name']}".rstrip())
        title.setObjectName("SectionTitle")
        top.addWidget(title)
        top.addStretch()
        info = QLabel("  ·  ".join(x for x in (d["kind"], d["fs"]) if x))
        info.setObjectName("Muted")
        top.addWidget(info)
        top.addWidget(self._button("Open", lambda r=d["root"]: open_path(r)))
        letter = drive_of(d["root"])
        system_drive = os.environ.get("SystemDrive", "C:").upper()
        if os.name == "nt" and d["kind"] in ("Removable", "Local disk") \
                and letter and letter != system_drive:
            top.addWidget(self._button("Eject", lambda r=d["root"]: self.eject_drive(r)))
        lay.addLayout(top)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(12)
        bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 5px; }}"
        )
        lay.addWidget(bar)

        bottom = QHBoxLayout()
        free_text = f"{format_size(d['free'])} free of {format_size(d['total'])}"
        if low:
            free_text += "  ·  low space"
        free_label = QLabel(free_text)
        free_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        pct_label = QLabel(f"{pct}% used")
        pct_label.setObjectName("Muted")
        bottom.addWidget(free_label)
        bottom.addStretch()
        bottom.addWidget(pct_label)
        lay.addLayout(bottom)
        return card

    def _build_settings_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(30, 30, 30, 0)
        page_layout.addLayout(self._page_header(
            "Control Settings", "Changes are saved automatically to Config/settings.json."
        ))
        page_layout.addSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 12, 30)
        content_layout.setSpacing(16)

        # ----- Terminal card -----
        card, lay = self._make_card(
            "Terminal", "Choose which Git Bash launcher to use and control the session."
        )
        self.path_edit = self._path_row(
            lay, "launcher_path", "Leave empty to auto-detect GitBashPortable.exe",
            "Select Git Bash launcher",
        )
        self.launcher_status = QLabel()
        self.launcher_status.setObjectName("Muted")
        self.launcher_status.setWordWrap(True)
        lay.addWidget(self.launcher_status)

        self._check_row(lay, "auto_start", "Start the terminal automatically when PocketTerm opens")

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._button("Start / Restart Terminal", self.restart_terminal, "Primary"))
        btn_row.addWidget(self._button("Stop Terminal", lambda: self.terminals.stop_current(
            "Terminal stopped."), "Danger"))
        btn_row.addStretch()
        lay.addLayout(btn_row)
        content_layout.addWidget(card)

        # ----- VS Code card -----
        card, lay = self._make_card(
            "VS Code",
            "By default VS Code is started by running your alias through Git Bash "
            "(bash -l -i -c \"code ...\"). If that doesn't work for your setup, "
            "set the VS Code executable below to launch it directly.",
        )
        lay.addWidget(self._labeled("Alias / command"))
        self.vscode_cmd_edit = self._text_row(lay, "vscode_command", "code")
        lay.addWidget(self._labeled("Extra arguments (e.g. --disable-gpu if it renders badly)"))
        self.vscode_args_edit = self._text_row(lay, "vscode_args", "--new-window")
        lay.addWidget(self._labeled("Git Bash executable (bash.exe) - leave empty to auto-detect"))
        self.bash_edit = self._path_row(
            lay, "bash_path", "Auto-detected from your GitPortable folder",
            "Select bash.exe",
        )
        lay.addWidget(self._labeled("VS Code executable (optional, bypasses the alias)"))
        self.vscode_exe_edit = self._path_row(
            lay, "vscode_exe", "e.g. Tools/VSCode/Code.exe (relative to USB root)", "Select Code.exe",
        )

        self.vscode_status = QLabel()
        self.vscode_status.setObjectName("Muted")
        self.vscode_status.setWordWrap(True)
        lay.addWidget(self.vscode_status)

        self._check_row(lay, "vscode_auto_start",
                        "Start VS Code automatically the first time I open its tab")

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._button("Start / Restart VS Code", self.restart_vscode, "Primary"))
        btn_row.addWidget(self._button("Stop VS Code", lambda: self.vscode.stop(
            "VS Code stopped."), "Danger"))
        btn_row.addStretch()
        lay.addLayout(btn_row)
        content_layout.addWidget(card)

        # ----- Git card -----
        card, lay = self._make_card(
            "Git",
            "Used for the branch info in Projects, Clone Repo and git init. "
            "Leave empty to auto-detect it from your GitPortable folder.",
        )
        self.git_edit = self._path_row(lay, "git_path", "Auto-detected", "Select git.exe")
        self.git_status = QLabel()
        self.git_status.setObjectName("Muted")
        self.git_status.setWordWrap(True)
        lay.addWidget(self.git_status)
        content_layout.addWidget(card)

        # ----- PocketAi card -----
        card, lay = self._make_card(
            "PocketAi",
            "PocketAi is started hidden through Git Bash (bash -l -i -c \"<command>\"), so your "
            "aliases and PATH work. The PocketAi tab shows the address in its address bar "
            "(default http://localhost:8080) as soon as something is listening there.",
        )
        lay.addWidget(self._labeled("Command"))
        self.pocketai_cmd_edit = self._text_row(lay, "pocketai_command", "PocketAi")
        self._check_row(lay, "pocketai_auto_start",
                        "Start PocketAi automatically the first time I open its tab")
        btn_row = QHBoxLayout()
        btn_row.addWidget(self._button("Start / Stop PocketAi", self.ai_toggle, "Primary"))
        btn_row.addStretch()
        lay.addLayout(btn_row)
        content_layout.addWidget(card)

        # ----- Appearance card -----
        card, lay = self._make_card("Appearance", "Pick an accent color and window behavior.")

        accent_row = QHBoxLayout()
        accent_row.addWidget(QLabel("Accent color"))
        self.accent_combo = QComboBox()
        self.accent_combo.addItems(ACCENT_CHOICES)
        self.accent_combo.setCurrentText(self.settings.get("accent"))
        self.accent_combo.currentTextChanged.connect(self._accent_changed)
        accent_row.addWidget(self.accent_combo)
        accent_row.addWidget(self._button("Pick custom color...", self.pick_custom_accent))
        accent_row.addStretch()
        lay.addLayout(accent_row)

        self._check_row(lay, "remember_size", "Remember window size between sessions")

        reset_row = QHBoxLayout()
        reset_row.addWidget(self._button("Reset window size", self._reset_size))
        reset_row.addStretch()
        lay.addLayout(reset_row)

        self.icon_status_label = QLabel(ICON_STATUS)
        self.icon_status_label.setObjectName("Muted")
        self.icon_status_label.setWordWrap(True)
        self.icon_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.icon_status_label)
        content_layout.addWidget(card)

        # ----- Behavior card -----
        card, lay = self._make_card(
            "Behavior", "System tray, global hotkey and close confirmation."
        )
        self._check_row(lay, "minimize_to_tray",
                        "Minimize to the system tray instead of the taskbar",
                        after=self._apply_tray)
        self._check_row(lay, "hotkey_enabled",
                        f"Global hotkey {HOTKEY_TEXT} shows / hides PocketTerm",
                        after=self._register_hotkey)
        self.hotkey_status = QLabel()
        self.hotkey_status.setObjectName("Muted")
        self.hotkey_status.setWordWrap(True)
        lay.addWidget(self.hotkey_status)
        self._check_row(lay, "confirm_close",
                        "Ask for confirmation when closing while the terminal, VS Code or a tool is running")
        content_layout.addWidget(card)

        # ----- Folders card -----
        card, lay = self._make_card("Folders", "Open PocketTerm's working folders in File Explorer.")
        folder_row = QHBoxLayout()
        for name in ("Projects", "Scripts", "Tools", "Config", "Images", "Temp"):
            folder_row.addWidget(self._button(name, lambda n=name: open_path(APP_DIR / n)))
        folder_row.addStretch()
        lay.addLayout(folder_row)
        content_layout.addWidget(card)

        # ----- Backup card -----
        card, lay = self._make_card(
            "Backup", "Export your settings to a file, or import them on another machine or drive."
        )
        backup_row = QHBoxLayout()
        backup_row.addWidget(self._button("Export settings...", self.export_settings))
        backup_row.addWidget(self._button("Import settings...", self.import_settings))
        backup_row.addStretch()
        lay.addLayout(backup_row)
        self.backup_status = QLabel("")
        self.backup_status.setObjectName("Muted")
        self.backup_status.setWordWrap(True)
        lay.addWidget(self.backup_status)
        content_layout.addWidget(card)

        content_layout.addStretch()
        scroll.setWidget(content)
        page_layout.addWidget(scroll, stretch=1)

        self._refresh_statuses()
        return page

    # ---------- settings row helpers ----------

    @staticmethod
    def _labeled(text):
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    def _text_row(self, layout, key, placeholder):
        edit = QLineEdit(str(self.settings.get(key)))
        edit.setPlaceholderText(placeholder)

        def save():
            self.settings.set(key, edit.text().strip())
            self._refresh_statuses()

        edit.editingFinished.connect(save)
        layout.addWidget(edit)
        self._edits[key] = edit
        return edit

    def _path_row(self, layout, key, placeholder, dialog_title):
        row = QHBoxLayout()
        edit = QLineEdit(str(self.settings.get(key)))
        edit.setPlaceholderText(placeholder)

        def save():
            self.settings.set(key, edit.text().strip().strip('"'))
            self._refresh_statuses()

        edit.editingFinished.connect(save)

        def browse():
            current = edit.text().strip().strip('"')
            start_dir = str(Path(current).parent) if current and Path(current).parent.exists() \
                else str(APP_DIR)
            path, _ = QFileDialog.getOpenFileName(
                self, dialog_title, start_dir, "Executables (*.exe);;All files (*)"
            )
            if path:
                edit.setText(os.path.normpath(path))
                save()

        row.addWidget(edit, stretch=1)
        row.addWidget(self._button("Browse...", browse))
        layout.addLayout(row)
        self._edits[key] = edit
        return edit

    def _check_row(self, layout, key, text, after=None):
        chk = QCheckBox(text)
        chk.setChecked(bool(self.settings.get(key)))

        def toggled(value):
            self.settings.set(key, bool(value))
            if after:
                after()

        chk.toggled.connect(toggled)
        layout.addWidget(chk)
        self._checks[key] = chk
        return chk

    def _refresh_statuses(self):
        # Terminal launcher
        launcher = find_launcher(self.settings)
        custom = bool(str(self.settings.get("launcher_path")).strip())
        label = "Using" if custom else "Auto-detected"
        if launcher.is_file():
            self._set_status(self.launcher_status, True, f"{label}: {launcher}")
        else:
            self._set_status(self.launcher_status, False, f"Not found: {launcher}")

        # VS Code
        args, _kwargs, info = vscode_launch_plan(self.settings)
        self._set_status(self.vscode_status, args is not None, info)

        # Git
        git = find_git(self.settings)
        custom_git = bool(str(self.settings.get("git_path")).strip())
        if git:
            self._set_status(
                self.git_status, True,
                f"{'Using' if custom_git else 'Auto-detected'}: {git}"
            )
        else:
            self._set_status(
                self.git_status, False,
                "Git not found. Branch info, Clone Repo and git init are disabled. "
                "Set its path above."
            )

    # ---------- keyboard shortcuts ----------

    def _setup_shortcuts(self):
        # Note: these work while the PocketTerm UI has focus. Once the keyboard focus is
        # inside an embedded program (terminal / VS Code) that program gets the keys.
        # Ctrl+1 .. Ctrl+9 and Ctrl+0 provide quick access to the first ten pages.
        for i in range(min(PAGE_COUNT, 10)):
            sc = QShortcut(QKeySequence(f"Ctrl+{(i + 1) % 10}"), self)
            sc.activated.connect(lambda i=i: self.show_page(i))

        sc = QShortcut(QKeySequence("Ctrl+T"), self)
        sc.activated.connect(self.new_terminal_tab)

        sc = QShortcut(QKeySequence("Ctrl+F"), self)
        sc.activated.connect(self._focus_search)

        sc = QShortcut(QKeySequence("Ctrl+K"), self)
        sc.activated.connect(self.open_palette)

    def _focus_search(self):
        index = self.stacked_widget.currentIndex()
        if index == PAGE_PROJECTS:
            self.project_search.setFocus()
            self.project_search.selectAll()
        elif index == PAGE_SCRIPTS:
            self.script_search.setFocus()
            self.script_search.selectAll()

    # ---------- command palette ----------

    def open_palette(self):
        commands = []
        for index, label in self._nav_base.items():
            commands.append((f"Go to: {label}", lambda i=index: self.show_page(i)))
        commands.append(("Terminal: New tab", self.new_terminal_tab))
        commands.append(("VS Code: Open folder...", self.open_folder_in_vscode))
        commands.append(("VS Code: Start / restart", self.restart_vscode))

        for p in self._list_projects():
            commands.append((f"Project: Open in VS Code - {p.name}",
                             lambda p=p: self._open_project(p)))
            commands.append((f"Project: Terminal here - {p.name}",
                             lambda p=p: self.open_terminal_in(p)))
        for p in self._list_scripts():
            commands.append((f"Script: Run - {p.name}",
                             lambda p=p: self._run_script_path(p)))
            commands.append((f"Script: Edit in VS Code - {p.name}",
                             lambda p=p: self.restart_vscode(p)))

        for s in self._snippets():
            commands.append((f"Snippet: Run - {s['name']}",
                             lambda c=s["command"]: self._send_command(c, True)))

        CommandPalette(self, commands).open_centered()

    # ---------- navigation / focus ----------

    def _embedded_for(self, index):
        if index == PAGE_TERMINAL:
            return self.terminals.current_terminal()
        if index == PAGE_VSCODE:
            return self.vscode
        if index == PAGE_TOOLS:
            w = self.tool_tabs.currentWidget()
            return w if isinstance(w, EmbeddedWindow) else None
        return None

    def _focus_current(self):
        widget = self._embedded_for(self.stacked_widget.currentIndex())
        if widget:
            widget.setFocus()
            widget.focus_soon()

    def show_page(self, index):
        # Expand the sidebar group that holds this page (Ctrl+number, Ctrl+K, etc.)
        group = self._nav_page_group.get(index)
        if group and not self._nav_group_open.get(group, True):
            self._set_group_open(group, True)

        self.stacked_widget.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if self.settings.get("last_page") != index:
            self.settings.set("last_page", index)

        if index == PAGE_PROJECTS:
            self.refresh_projects()
        elif index == PAGE_SCRIPTS:
            self.refresh_scripts()
        elif index == PAGE_SNIPPETS:
            self._render_snippets()
        elif index == PAGE_DRIVES:
            self.refresh_drives()
        elif index == PAGE_PORTS:
            self.refresh_ports()
        elif index == PAGE_WEB:
            self._web_first_show()
        elif index == PAGE_SETTINGS:
            self._refresh_statuses()
        elif index == PAGE_DASHBOARD:
            self._refresh_dashboard()
        elif index == PAGE_PLUGINS:
            if not self._plugin_entries:
                self.refresh_plugins()
        elif (
            index == PAGE_VSCODE
            and self.settings.get("vscode_auto_start")
            and not self._vscode_autostarted
            and not self.vscode.is_running()
        ):
            self._vscode_autostarted = True
            self.start_vscode()

        self._focus_current()

    # ---------- terminal / vscode actions ----------

    def start_terminal(self):
        self.terminals.start_current()

    def restart_terminal(self):
        self.show_page(PAGE_TERMINAL)
        self.terminals.restart_current()

    def new_terminal_tab(self):
        self.show_page(PAGE_TERMINAL)
        self.terminals.add_terminal(start=True)

    def open_terminal_in(self, path):
        """Open a new terminal tab that starts in the given folder."""
        path = Path(path)
        self.show_page(PAGE_TERMINAL)
        self.terminals.add_terminal(start=True, target=path, title=path.name)

    def start_vscode(self, target=None):
        self.vscode.start(target)

    def restart_vscode(self, target=None):
        self._vscode_autostarted = True  # don't double-start via the auto-start setting
        self.show_page(PAGE_VSCODE)
        self.vscode.restart(target)

    def open_folder_in_vscode(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Open folder in VS Code", str(APP_DIR / "Projects")
        )
        if folder:
            self._record_recent(Path(folder))
            self.restart_vscode(Path(folder))

    # ---------- drag and drop ----------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p.exists()]
        if not paths:
            return
        event.acceptProposedAction()
        target = paths[0]
        if target.is_dir():
            self._record_recent(target)
        self.restart_vscode(target)

    # ---------- projects ----------

    @staticmethod
    def _selected_path(list_widget):
        item = list_widget.currentItem()
        return Path(item.data(ROLE_PATH)) if item else None

    def _record_recent(self, path):
        s = str(Path(path))
        recent = [r for r in self.settings.get("recent_projects") if r != s]
        recent.insert(0, s)
        self.settings.set("recent_projects", recent[:MAX_RECENT])
        self._render_projects()

    @staticmethod
    def _list_projects():
        root = APP_DIR / "Projects"
        try:
            return sorted(
                (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            return []

    def _fill_project_item(self, item, p):
        s = str(p)
        info = self.git_cache.get(s)
        item.setText(p.name)
        item.setToolTip(s)
        item.setData(ROLE_PATH, s)
        item.setData(ROLE_TITLE, p.name)
        item.setData(ROLE_PIN, s in self.settings.get("pinned_projects"))
        if info:
            item.setData(ROLE_SUB, info[0])
            item.setData(ROLE_BADGE, info[1])
            item.setData(ROLE_KIND, info[2])
        else:
            item.setData(ROLE_SUB, "Folder")
            item.setData(ROLE_BADGE, "")
            item.setData(ROLE_KIND, "")

    def refresh_projects(self):
        self._project_paths = self._list_projects()
        self._render_projects()
        self._start_git_scan()

    def _render_projects(self):
        keep = self._selected_path(self.project_list)
        query = self.project_search.text().strip().lower()
        pinned = list(self.settings.get("pinned_projects"))
        recent = list(self.settings.get("recent_projects"))

        def sort_key(p):
            s = str(p)
            if s in pinned:
                return (0, 0, p.name.lower())
            if s in recent:
                return (1, recent.index(s), p.name.lower())
            return (2, 0, p.name.lower())

        shown = [p for p in self._project_paths if query in p.name.lower()]
        shown.sort(key=sort_key)

        self.project_list.clear()
        select_row = -1
        for p in shown:
            item = QListWidgetItem()
            self._fill_project_item(item, p)
            self.project_list.addItem(item)
            if keep and p == keep:
                select_row = self.project_list.count() - 1
        if select_row >= 0:
            self.project_list.setCurrentRow(select_row)

        root = APP_DIR / "Projects"
        if not self._project_paths:
            self.projects_status.setText("No projects yet. Use New Project... or Clone Repo... to add one.")
        elif query:
            self.projects_status.setText(
                f"{len(shown)} of {len(self._project_paths)} project(s) match in {root}"
            )
        else:
            self.projects_status.setText(f"{len(self._project_paths)} project(s) in {root}")

    def _start_git_scan(self):
        self._git_gen += 1
        gen = self._git_gen
        git = find_git(self.settings)
        paths = list(self._project_paths)
        current = {str(p) for p in paths}
        # Drop entries for projects that no longer exist.
        self.git_cache = {k: v for k, v in self.git_cache.items() if k in current}
        if not git:
            self.git_cache = {}
            return

        def work():
            for p in paths:
                if gen != self._git_gen:
                    return
                self.git_info_ready.emit(gen, str(p), git_summary(git, p))

        threading.Thread(target=work, daemon=True).start()

    def _on_git_info(self, gen, path, info):
        if gen != self._git_gen:
            return
        self.git_cache[path] = info
        for i in range(self.project_list.count()):
            item = self.project_list.item(i)
            if item.data(ROLE_PATH) == path:
                self._fill_project_item(item, Path(path))
                break

    def _open_project(self, path):
        self._record_recent(path)
        self.restart_vscode(path)

    def open_project_in_vscode(self):
        path = self._selected_path(self.project_list)
        if not path:
            self.projects_status.setText("Select a project first.")
            return
        self._open_project(path)

    def open_terminal_here(self):
        path = self._selected_path(self.project_list)
        if not path:
            self.projects_status.setText("Select a project first.")
            return
        self.open_terminal_in(path)

    def toggle_pin_selected(self):
        path = self._selected_path(self.project_list)
        if not path:
            self.projects_status.setText("Select a project first.")
            return
        s = str(path)
        pinned = list(self.settings.get("pinned_projects"))
        if s in pinned:
            pinned.remove(s)
            msg = f"Unpinned {path.name}."
        else:
            pinned.append(s)
            msg = f"Pinned {path.name}."
        self.settings.set("pinned_projects", pinned)
        self._render_projects()
        self.projects_status.setText(msg)

    def show_project_in_explorer(self):
        path = self._selected_path(self.project_list)
        if not path:
            self.projects_status.setText("Select a project first.")
            return
        open_path(path)

    def new_project(self):
        name, ok = QInputDialog.getText(self, "New project", "Project folder name:")
        if not ok:
            return
        name = name.strip()
        if not name or any(c in name for c in '<>:"/\\|?*') or name in (".", ".."):
            self.projects_status.setText("That isn't a valid folder name.")
            return
        target = APP_DIR / "Projects" / name
        if target.exists():
            self.projects_status.setText(f"'{name}' already exists.")
            return
        try:
            target.mkdir(parents=True)
        except OSError as e:
            self.projects_status.setText(f"Couldn't create folder: {e}")
            return

        note = ""
        git = find_git(self.settings)
        if git:
            answer = QMessageBox.question(
                self, "New project", "Initialize a git repository in this folder?"
            )
            if answer == QMessageBox.Yes:
                try:
                    subprocess.run(
                        [str(git), "init"], cwd=str(target), capture_output=True,
                        timeout=15, creationflags=CREATE_NO_WINDOW,
                    )
                    note = " (git repository initialized)"
                except (OSError, subprocess.SubprocessError):
                    note = " (git init failed)"

        self.project_search.clear()
        self.refresh_projects()
        self._select_project_named(name)
        self.projects_status.setText(f"Created {target}{note}")

    def _select_project_named(self, name):
        for i in range(self.project_list.count()):
            if Path(self.project_list.item(i).data(ROLE_PATH)).name == name:
                self.project_list.setCurrentRow(i)
                break

    def clone_repo(self):
        git = find_git(self.settings)
        if not git:
            self.projects_status.setText("Git not found. Set its path in Settings.")
            return
        url, ok = QInputDialog.getText(self, "Clone repository", "Repository URL:")
        url = url.strip()
        if not ok or not url:
            return
        name = repo_name_from_url(url)
        dest = APP_DIR / "Projects"
        self.projects_status.setText(f"Cloning {url} ...")

        def work():
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"  # fail instead of hanging on a password prompt
            try:
                r = subprocess.run(
                    [str(git), "clone", url], cwd=str(dest), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", env=env,
                    stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                )
            except OSError as e:
                self.task_done.emit("clone", False, f"Clone failed: {e}")
                return
            if r.returncode == 0:
                self.task_done.emit("clone", True, name)
            else:
                lines = r.stderr.strip().splitlines()
                self.task_done.emit("clone", False,
                                    "Clone failed: " + (lines[-1] if lines else "unknown error"))

        threading.Thread(target=work, daemon=True).start()

    def _on_task_done(self, kind, ok, message):
        if kind == "clone":
            if ok:
                self.project_search.clear()
                self.refresh_projects()
                self._select_project_named(message)
                self.projects_status.setText(f"Cloned {message}.")
            else:
                self.projects_status.setText(message)
        elif kind == "eject":
            self._set_status(self.drives_status, ok, message)
            self._drives_sig = None
            self.refresh_drives()

    # ---------- scripts ----------

    @staticmethod
    def _list_scripts():
        root = APP_DIR / "Scripts"
        try:
            return sorted(
                (p for p in root.iterdir()
                 if p.is_file() and p.suffix.lower() in SCRIPT_EXTS),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            return []

    def _fill_script_item(self, item, p):
        saved = self.settings.get("script_args").get(p.name, "")
        ext = p.suffix.lower()
        item.setText(p.name)
        item.setToolTip(str(p))
        item.setData(ROLE_PATH, str(p))
        item.setData(ROLE_TITLE, p.name)
        item.setData(ROLE_SUB, f"args: {saved}" if saved else SCRIPT_TYPES.get(ext, "Script"))
        item.setData(ROLE_BADGE, ext.lstrip(".").upper())
        item.setData(ROLE_KIND, "info")

    def refresh_scripts(self):
        self._script_paths = self._list_scripts()
        self._render_scripts()

    def _render_scripts(self):
        keep = self._selected_path(self.script_list)
        query = self.script_search.text().strip().lower()
        shown = [p for p in self._script_paths if query in p.name.lower()]

        self.script_list.clear()
        select_row = -1
        for p in shown:
            item = QListWidgetItem()
            self._fill_script_item(item, p)
            self.script_list.addItem(item)
            if keep and p == keep:
                select_row = self.script_list.count() - 1
        if select_row >= 0:
            self.script_list.setCurrentRow(select_row)

        root = APP_DIR / "Scripts"
        if not self._script_paths:
            self.scripts_status.setText(
                f"No scripts found. Put .bat, .cmd, .ps1, .py or .sh files in {root}"
            )
        elif query:
            self.scripts_status.setText(
                f"{len(shown)} of {len(self._script_paths)} script(s) match in {root}"
            )
        else:
            self.scripts_status.setText(f"{len(self._script_paths)} script(s) in {root}")

    def _script_selected(self, current, _previous):
        saved = ""
        if current is not None:
            saved = self.settings.get("script_args").get(Path(current.data(ROLE_PATH)).name, "")
        self.script_args_edit.setText(saved)

    def _save_script_args(self):
        path = self._selected_path(self.script_list)
        if not path:
            return
        args = dict(self.settings.get("script_args"))
        text = self.script_args_edit.text().strip()
        if text:
            args[path.name] = text
        else:
            args.pop(path.name, None)
        self.settings.set("script_args", args)
        item = self.script_list.currentItem()
        if item is not None:
            self._fill_script_item(item, path)

    def _run_script_path(self, path, args_text=None):
        """Run a script in its own console. Uses the remembered arguments unless given."""
        if args_text is None:
            args_text = self.settings.get("script_args").get(path.name, "")
        error = run_script(path, self.settings, args_text)
        self.scripts_status.setText(error if error else f"Started {path.name} in a new console window.")

    def run_selected_script(self):
        path = self._selected_path(self.script_list)
        if not path:
            self.scripts_status.setText("Select a script first.")
            return
        self._save_script_args()
        self._run_script_path(path, self.script_args_edit.text())

    def _script_running(self):
        return self.script_proc is not None and self.script_proc.state() != QProcess.NotRunning

    def _update_script_buttons(self):
        running = self._script_running()
        self.btn_run_log.setEnabled(not running)
        self.btn_stop_script.setEnabled(running)

    def _log(self, text, color=None):
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color or LOG_COLOR))
        cursor.insertText(text, fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def run_selected_script_logged(self):
        path = self._selected_path(self.script_list)
        if not path:
            self.scripts_status.setText("Select a script first.")
            return
        if self._script_running():
            self.scripts_status.setText("A script is already running. Stop it first.")
            return

        self._save_script_args()
        args = split_args(self.script_args_edit.text())
        program, argv, env, error = script_command(path, self.settings, args)
        if error:
            self.scripts_status.setText(error)
            return

        proc = QProcess(self)
        proc.setWorkingDirectory(str(path.parent))
        proc.setProcessChannelMode(QProcess.MergedChannels)
        if env:
            pe = QProcessEnvironment()
            for k, v in env.items():
                pe.insert(k, v)
            proc.setProcessEnvironment(pe)
        proc.readyReadStandardOutput.connect(self._script_output)
        proc.finished.connect(self._script_finished)
        proc.errorOccurred.connect(self._script_error)

        shown_args = " ".join(args)
        palette = get_palette(self.settings)
        self._log(f"\n$ {path.name} {shown_args}".rstrip() + "\n", palette[2])
        self.script_proc = proc
        self._script_path = path
        proc.start(program, argv)
        self._update_script_buttons()
        self.scripts_status.setText(f"Running {path.name} ...")

    _ERROR_LINE = re.compile(r"\b(error|traceback|exception|failed|fatal)\b", re.IGNORECASE)

    def _script_output(self):
        if self.script_proc is None:
            return
        data = bytes(self.script_proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines(keepends=True):
            self._log(line, ERR_COLOR if self._ERROR_LINE.search(line) else None)

    def _script_finished(self, code, _status):
        self._script_output()
        self._log(f"[finished with exit code {code}]\n", OK_COLOR if code == 0 else ERR_COLOR)
        proc = self.script_proc
        self.script_proc = None
        if proc is not None:
            proc.deleteLater()
        self._update_script_buttons()
        self.scripts_status.setText(f"Script finished with exit code {code}.")

    def _script_error(self, error):
        proc = self.script_proc
        detail = proc.errorString() if proc is not None else str(error)
        log.warning("Script process error (%s): %s", error, detail)
        if error == QProcess.FailedToStart:
            self._log(f"[failed to start: {detail}]\n", ERR_COLOR)
            self.script_proc = None
            if proc is not None:
                proc.deleteLater()
            self._update_script_buttons()
            self.scripts_status.setText("Failed to start the script.")

    def stop_script(self):
        if not self._script_running():
            return
        pid = self.script_proc.processId()
        # Kill the whole process tree, not just the first process.
        if not kill_tree(pid):
            self.script_proc.kill()
        self._log("[stopped]\n", WARN_COLOR)

    def edit_selected_script(self):
        path = self._selected_path(self.script_list)
        if not path:
            self.scripts_status.setText("Select a script first.")
            return
        self.restart_vscode(path)

    # ---------- tools ----------

    def _tools(self):
        """Return tool entries with relative USB paths resolved for this computer."""
        tools = []
        for item in self.settings.get("tools"):
            if not (isinstance(item, dict) and item.get("name") and item.get("path")):
                continue
            tool = dict(item)
            resolved = resolve_configured_path(tool["path"])
            tool["path"] = str(resolved) if resolved else str(tool["path"])
            tools.append(tool)
        return tools

    def _refresh_tool_combo(self, select=None):
        tools = self._tools()
        self.tool_combo.blockSignals(True)
        self.tool_combo.clear()
        for t in tools:
            self.tool_combo.addItem(str(t["name"]))
        if select is not None and 0 <= select < len(tools):
            self.tool_combo.setCurrentIndex(select)
        self.tool_combo.blockSignals(False)

    def _update_tools_empty(self):
        has = self.tool_tabs.count() > 0
        self.tool_tabs.setVisible(has)
        self.tools_empty.setVisible(not has)

    def launch_tool(self):
        tools = self._tools()
        i = self.tool_combo.currentIndex()
        if not (0 <= i < len(tools)):
            self.tools_empty.setText("No tools yet. Use Add Tool... to add a program.")
            return
        t = tools[i]
        widget = EmbeddedTool(self.settings, str(t["name"]), t["path"], t.get("args", ""))
        idx = add_closable_tab(self.tool_tabs, widget, str(t["name"]))
        self.tool_tabs.setCurrentIndex(idx)
        self._update_tools_empty()
        widget.start()

    def close_tool_tab(self, index):
        widget = self.tool_tabs.widget(index)
        if widget is None:
            return
        widget.shutdown()
        self.tool_tabs.removeTab(index)
        widget.deleteLater()
        self._update_tools_empty()

    def add_tool(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a program", str(APP_DIR / "Tools"),
            "Programs (*.exe);;All files (*)"
        )
        if not path:
            return
        name, ok = QInputDialog.getText(
            self, "Add tool", "Display name:", text=Path(path).stem
        )
        if not ok or not name.strip():
            return
        args, ok = QInputDialog.getText(self, "Add tool", "Arguments (optional):")
        if not ok:
            args = ""
        tools = self._tools()
        tools.append({"name": name.strip(), "path": os.path.normpath(path), "args": args.strip()})
        self.settings.set("tools", tools)
        self._refresh_tool_combo(select=len(tools) - 1)

    def edit_tool(self):
        tools = self._tools()
        i = self.tool_combo.currentIndex()
        if not (0 <= i < len(tools)):
            return
        t = tools[i]
        name, ok = QInputDialog.getText(
            self, "Edit tool", "Display name:", text=str(t["name"])
        )
        if not ok or not name.strip():
            return
        args, ok = QInputDialog.getText(
            self, "Edit tool", "Arguments (optional):", text=str(t.get("args", ""))
        )
        if not ok:
            args = str(t.get("args", ""))
        t["name"] = name.strip()
        t["args"] = args.strip()
        self.settings.set("tools", tools)
        self._refresh_tool_combo(select=i)

    def remove_tool(self):
        tools = self._tools()
        i = self.tool_combo.currentIndex()
        if not (0 <= i < len(tools)):
            return
        del tools[i]
        self.settings.set("tools", tools)
        self._refresh_tool_combo(select=min(i, len(tools) - 1))

    # ---------- appearance / window ----------

    def _accent_changed(self, name):
        if name in ACCENT_CHOICES:
            self.settings.set("accent", name)
            self._apply_style()
            self._refresh_statuses()  # re-applies the inline status colors

    def pick_custom_accent(self):
        current = str(self.settings.get("custom_accent"))
        if not HEX_RE.fullmatch(current):
            current = ACCENTS["Violet"][0]
        color = QColorDialog.getColor(QColor(current), self, "Pick an accent color")
        if not color.isValid():
            return
        self.settings.set("custom_accent", color.name())
        self.settings.set("accent", "Custom")
        self.accent_combo.blockSignals(True)
        self.accent_combo.setCurrentText("Custom")
        self.accent_combo.blockSignals(False)
        self._apply_style()
        self._refresh_statuses()

    def _available_screen_geometry(self):
        """Return the usable screen area, excluding the taskbar/docks."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            return screen.availableGeometry()
        # Fallback for unusual/offscreen environments where Qt has no screen.
        return QRect(0, 0, 1280, 800)

    def _fit_window_size(self, width, height):
        """Clamp the requested client size so the entire window fits on screen."""
        area = self._available_screen_geometry()
        margin = 24
        max_width = max(1, area.width() - margin)
        max_height = max(1, area.height() - margin)

        # Keep a useful minimum, but never demand more space than the display has.
        min_width = min(760, max_width)
        min_height = min(460, max_height)
        self.setMinimumSize(min_width, min_height)

        width = max(min_width, min(int(width), max_width))
        height = max(min_height, min(int(height), max_height))
        return width, height

    def _place_window_on_screen(self, width, height):
        """Size and center the normal window inside the current screen area."""
        area = self._available_screen_geometry()
        width, height = self._fit_window_size(width, height)
        x = area.x() + max(0, (area.width() - width) // 2)
        y = area.y() + max(0, (area.height() - height) // 2)
        self.setGeometry(x, y, width, height)

    def _reset_size(self):
        self.showNormal()
        self._place_window_on_screen(1100, 700)
        self.settings.set("window_size", [self.width(), self.height()])

    def _restore_size(self):
        width, height = 1100, 700
        if self.settings.get("remember_size"):
            size = self.settings.get("window_size")
            try:
                width, height = int(size[0]), int(size[1])
            except (TypeError, ValueError, IndexError):
                pass
        # Older settings could request a 700px-tall window even on a shorter
        # laptop display. Clamp saved dimensions instead of forcing that size.
        self._place_window_on_screen(width, height)

    def showEvent(self, event):
        super().showEvent(event)
        # Re-check after Qt assigns the real screen/native window, especially
        # when Windows has multiple monitors or a changed display resolution.
        QTimer.singleShot(0, self._ensure_window_fits_screen)

    def _ensure_window_fits_screen(self):
        if self.isMaximized() or self.isMinimized():
            return
        area = self._available_screen_geometry()
        frame = self.frameGeometry()
        width, height = self._fit_window_size(self.width(), self.height())
        needs_resize = width != self.width() or height != self.height()
        outside = (
            frame.right() > area.right() or frame.bottom() > area.bottom()
            or frame.left() < area.left() or frame.top() < area.top()
        )
        if needs_resize or outside:
            self._place_window_on_screen(width, height)

    # ---------- backup ----------

    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export settings", str(APP_DIR / "Config" / "pocketterm-settings.json"),
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        error = self.settings.export_to(path)
        if error:
            self._set_status(self.backup_status, False, error)
        else:
            self._set_status(self.backup_status, True, f"Exported to {path}")

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import settings", str(APP_DIR / "Config"),
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        error = self.settings.import_from(path)
        if error:
            self._set_status(self.backup_status, False, error)
            return
        self._sync_settings_widgets()
        self._set_status(self.backup_status, True, f"Imported settings from {path}")

    def _sync_settings_widgets(self):
        """Refresh every settings control and dependent feature after an import."""
        for key, edit in self._edits.items():
            edit.setText(str(self.settings.get(key)))
        for key, chk in self._checks.items():
            chk.blockSignals(True)
            chk.setChecked(bool(self.settings.get(key)))
            chk.blockSignals(False)
        if self.settings.get("accent") not in ACCENT_CHOICES:
            self.settings.data["accent"] = "Violet"
        self.accent_combo.blockSignals(True)
        self.accent_combo.setCurrentText(self.settings.get("accent"))
        self.accent_combo.blockSignals(False)

        self._apply_style()
        self._refresh_statuses()
        self._refresh_tool_combo()
        self.low_space_spin.blockSignals(True)
        self.low_space_spin.setValue(int(self.settings.get("low_space_gb")))
        self.low_space_spin.blockSignals(False)
        self._render_snippets()
        self._drives_sig = None
        self._apply_tray()
        self._register_hotkey()
        self.refresh_projects()
        self.refresh_scripts()

    # ---------- system tray and global hotkey ----------

    def finish_setup(self):
        """Call once after the window has been shown (needs a native window handle)."""
        self._setup_tray()
        self._register_hotkey()

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("PocketTerm")

        menu = QMenu(self)

        def add_action(text, handler):
            act = QAction(text, self)
            act.triggered.connect(lambda _=False: handler())
            menu.addAction(act)

        add_action("Show PocketTerm", self._show_window)
        add_action("Terminal", lambda: (self._show_window(), self.show_page(PAGE_TERMINAL)))
        add_action("VS Code", lambda: (self._show_window(), self.show_page(PAGE_VSCODE)))
        menu.addSeparator()
        add_action("Quit", self._quit_from_tray)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self._tray_menu = menu  # keep a reference
        self._apply_tray()

    def _apply_tray(self):
        if self.tray:
            self.tray.setVisible(bool(self.settings.get("minimize_to_tray")))

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_visibility()

    def _show_window(self):
        if self._was_max:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_visibility(self):
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            if self.tray and self.tray.isVisible():
                self.hide()
            else:
                self.showMinimized()
        else:
            self._show_window()

    def _quit_from_tray(self):
        self._show_window()
        self.close()

    def _set_hotkey_label(self, state, text):
        """state: 'on', 'off' or 'error'."""
        self.hotkey_status.setText(text)
        if state == "on":
            self.hotkey_status.setStyleSheet(f"color: {OK_COLOR};")
        elif state == "error":
            self.hotkey_status.setStyleSheet(f"color: {ERR_COLOR};")
        else:
            self.hotkey_status.setStyleSheet("")

    def _register_hotkey(self):
        if os.name != "nt":
            self._set_hotkey_label("off", "The global hotkey is only available on Windows.")
            return
        try:
            if self._user32 is None:
                u = ctypes.WinDLL("user32", use_last_error=True)
                u.RegisterHotKey.argtypes = [
                    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
                ]
                u.RegisterHotKey.restype = wintypes.BOOL
                u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
                u.UnregisterHotKey.restype = wintypes.BOOL
                self._user32 = u

            hwnd = wintypes.HWND(int(self.winId()))
            if self._hotkey_registered:
                self._user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                self._hotkey_registered = False

            if not self.settings.get("hotkey_enabled"):
                self._set_hotkey_label("off", "The global hotkey is off.")
                return

            ok = self._user32.RegisterHotKey(
                hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_P
            )
            self._hotkey_registered = bool(ok)
            if ok:
                self._set_hotkey_label("on", f"{HOTKEY_TEXT} is active.")
            else:
                self._set_hotkey_label(
                    "error",
                    f"Couldn't register {HOTKEY_TEXT}. Another program is probably using it."
                )
        except Exception as e:
            log.exception("Hotkey registration failed")
            self._set_hotkey_label("error", f"Hotkey error: {e}")

    def _unregister_hotkey(self):
        if os.name == "nt" and self._hotkey_registered and self._user32 is not None:
            try:
                self._user32.UnregisterHotKey(wintypes.HWND(int(self.winId())), HOTKEY_ID)
            except Exception:
                log.exception("Could not unregister hotkey")
        self._hotkey_registered = False

    def nativeEvent(self, eventType, message):
        if os.name == "nt":
            try:
                if bytes(eventType) == b"windows_generic_MSG":
                    msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        QTimer.singleShot(0, self.toggle_visibility)
                        return True, 0
            except Exception:
                log.exception("nativeEvent failed")
        return super().nativeEvent(eventType, message)

    # ---------- events ----------

    def changeEvent(self, event):
        super().changeEvent(event)

        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                if self.settings.get("minimize_to_tray") and self.tray and self.tray.isVisible():
                    QTimer.singleShot(0, self.hide)
            else:
                self._was_max = self.isMaximized()

        # Window re-activated (alt-tab, clicking back in): restore focus to the embedded app
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._focus_current()

    def _anything_running(self):
        if self.terminals.any_running() or self.vscode.is_running():
            return True
        if self._tools_running() or self.ai_running():
            return True
        return self._script_running()

    def closeEvent(self, event):
        if (self.settings.get("confirm_close") and not self._skip_close_confirm
                and self._anything_running()):
            answer = QMessageBox.question(
                self, "Close PocketTerm",
                "The terminal, VS Code, PocketAi, a tool or a script is still running.\n"
                "Closing PocketTerm will stop them. Close anyway?",
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return

        if self.settings.get("remember_size") and not self.isMaximized():
            self.settings.data["window_size"] = [self.width(), self.height()]
        self.settings.save()

        self._nav_timer.stop()
        self._drives_timer.stop()
        self._ports_timer.stop()
        self._dashboard_timer.stop()
        for plugin_rel in list(self._loaded_plugins):
            self._unload_plugin(plugin_rel)
        self._stop_background_manager()
        self._unregister_hotkey()
        # Close QtWebEngine cleanly so it doesn't print "profile still in use" warnings.
        if self._devtools is not None:
            self._devtools.close()
            self._devtools.deleteLater()
            self._devtools = None
        if self.web_view is not None:
            self.web_view.stop()
            self.web_view.deleteLater()
            self.web_view = None
        if self.tray:
            self.tray.hide()
        if self._script_running():
            self.stop_script()
        if self.ai_running():
            self._ai_timer.stop()
            if not kill_tree(self.ai_proc.processId()):
                self.ai_proc.kill()

        self.terminals.shutdown_all()
        self.vscode.shutdown()
        for i in range(self.tool_tabs.count()):
            w = self.tool_tabs.widget(i)
            if isinstance(w, EmbeddedWindow):
                w.shutdown()

        super().closeEvent(event)
        if event.isAccepted():
            QApplication.quit()


if __name__ == "__main__":
    # Without this, Windows groups the app under python.exe and the taskbar
    # shows the Python icon. Must run before any window is created.
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PocketTerm.Launcher")
        except Exception:
            log.exception("Could not set the AppUserModelID")

    app = QApplication(sys.argv)
    # The window may be hidden in the tray; closeEvent quits the app explicitly.
    app.setQuitOnLastWindowClosed(False)

    app_icon = None
    icon_path, ICON_STATUS = find_icon()
    if icon_path:
        candidate = QIcon(str(icon_path))
        if candidate.isNull():
            ICON_STATUS = f"Found {icon_path}, but Qt could not read it. Is it a valid PNG?"
        else:
            app_icon = candidate
            app.setWindowIcon(app_icon)
    print(ICON_STATUS)
    log.info("Starting PocketTerm. %s", ICON_STATUS)

    window = MainWindow()
    if app_icon:
        window.setWindowIcon(app_icon)
    window.show()
    window.finish_setup()
    sys.exit(app.exec())
