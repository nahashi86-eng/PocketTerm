import sys, os, re, copy, csv, json, shlex, shutil, logging, platform, threading, time, socket
import subprocess, ctypes, importlib.util, urllib.request, urllib.parse, hmac, hashlib
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

from PySide6.QtCore import (Qt, QTimer, QEvent, QProcess, QProcessEnvironment, Signal, QRect, QSize,
                            QPointF, QStorageInfo, QUrl, QRectF)
from PySide6.QtGui import (QIcon, QColor, QKeySequence, QShortcut, QTextCursor, QAction, QPixmap,
                           QPainter, QPen, QFont, QFontMetrics, QTextCharFormat, QDesktopServices)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QButtonGroup, QLineEdit, QCheckBox, QComboBox, QFileDialog, QScrollArea,
    QListWidget, QListWidgetItem, QInputDialog, QTabWidget, QTabBar, QPlainTextEdit, QMessageBox,
    QColorDialog, QSystemTrayIcon, QMenu, QStyle, QStyledItemDelegate, QProgressBar, QSpinBox,
    QDialog, QSplitter, QSizeGrip, QTreeView, QFileSystemModel, QTreeWidget, QTreeWidgetItem,
)

# The built-in web view needs QtWebEngine (part of a normal `pip install PySide6`).
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage
    WEBVIEW_OK = True
except ImportError:
    QWebEngineView = QWebEnginePage = None
    WEBVIEW_OK = False

# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent


def _discover_portable_root():
    """Find the USB root by walking upward from this script; never assume E:, F:, etc."""
    def is_root(f):
        return any((f / r).is_file() for r in
                   ("GitBashPortable.exe", "App/Git/bin/bash.exe", "App/Git/cmd/git.exe"))
    for folder in (SCRIPT_DIR, *SCRIPT_DIR.parents):
        if is_root(folder):
            return folder
    for folder in (SCRIPT_DIR, *SCRIPT_DIR.parents):  # GitPortable stored as a child folder
        c = folder / "GitPortable"
        if any((c / r).is_file() for r in ("GitBashPortable.exe", "App/Git/bin/bash.exe")):
            return c
    return APP_DIR


PORTABLE_ROOT = _discover_portable_root()


def resolve_configured_path(value, *, relative_to_root=True):
    """Resolve saved paths, including old absolute paths from a moved USB drive."""
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if path.is_absolute():
        if path.exists():
            return path
        # Migrate old settings such as E:\Assets\GitPortable\Tools\x.exe
        parts = list(path.parts)
        for i, part in enumerate(parts):
            if part.lower() == PORTABLE_ROOT.name.lower() and i < len(parts) - 1:
                rebased = PORTABLE_ROOT.joinpath(*parts[i + 1:])
                if rebased.exists():
                    return rebased
        return path
    roots = (PORTABLE_ROOT, APP_DIR) if relative_to_root else (APP_DIR, PORTABLE_ROOT)
    for root in roots:
        if (root / path).exists():
            return root / path
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


for _f in ("Tools", "Scripts", "Config", "Temp", "Projects", "GitPortable", "Images", "Plugins"):
    (APP_DIR / _f).mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = APP_DIR / "Config" / "settings.json"
LOG_FILE = APP_DIR / "Config" / "pocketterm.log"
UI_ICON_DIR = APP_DIR / "Temp" / "ui"
PROCESS_MANAGER_FILE = SCRIPT_DIR / "ProcessManager.py"
AUTOCLICKER_FILE = SCRIPT_DIR / "Autoclicker.py"
POCKETLAUNCHER_FILE = SCRIPT_DIR / "PocketLauncher.py"
POCKETVM_FILE = SCRIPT_DIR / "PocketVM.py"
CPP_COMPILER_FILE = SCRIPT_DIR / "CppRunner.py"
POCKETAI_FILE = SCRIPT_DIR / "PocketAi.py"


def setup_logging():
    logger = logging.getLogger("pocketterm")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            h = RotatingFileHandler(LOG_FILE, maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(h)
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


log = setup_logging()

ICON_NAME = "icon.png"
ICON_DIRS = [APP_DIR / "Images", SCRIPT_DIR / "Images", SCRIPT_DIR.parent / "Images", Path.cwd() / "Images"]
ICON_STATUS = "Icon: not checked yet."

LAUNCHER_CANDIDATES = [
    PORTABLE_ROOT / "GitBashPortable.exe",
    PORTABLE_ROOT / "GitPortable" / "GitBashPortable.exe",
    APP_DIR / "GitPortable" / "GitBashPortable.exe",
]

SCRIPT_EXTS = (".bat", ".cmd", ".ps1", ".py", ".sh")
SCRIPT_TYPES = {".py": "Python script", ".bat": "Batch script", ".cmd": "Batch script",
                ".ps1": "PowerShell script", ".sh": "Shell script"}

# Page indices (must match the order pages are added to the stack)
(PAGE_TERMINAL, PAGE_VSCODE, PAGE_PROJECTS, PAGE_SCRIPTS, PAGE_SNIPPETS,
 PAGE_TOOLS, PAGE_DRIVES, PAGE_PORTS, PAGE_WEB, PAGE_SETTINGS,
 PAGE_DASHBOARD, PAGE_FILES, PAGE_PLUGINS, PAGE_AUTOCLICKER,
 PAGE_DIAGNOSTICS, PAGE_JAR_LAUNCHER, PAGE_POCKETLAUNCHER, PAGE_POCKETVM) = range(18)
PAGE_REMOTE_ACCESS = 18
PAGE_CPP = 19
PAGE_COUNT = 20

PLUGIN_REPO_API = "https://api.github.com/repos/nahashi86-eng/PocketTerm/git/trees/main?recursive=1"
PLUGIN_RAW_BASE = "https://raw.githubusercontent.com/nahashi86-eng/PocketTerm/main/"
PLUGIN_REPO_URL = "https://github.com/nahashi86-eng/PocketTerm.git"

# Auto update: the running script is replaced by this file from GitHub (a backup is kept first).
UPDATE_URL = PLUGIN_RAW_BASE + "client/pocketterm.py"
UPDATE_CHECK_DELAY_MS = 8000                  # first check shortly after start-up
UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000       # then every 6 hours while PocketTerm is open
UPDATE_MIN_BYTES = 50_000                     # sanity limits on the downloaded file
UPDATE_MAX_BYTES = 5_000_000
UPDATE_BACKUPS_KEEP = 5

NAV_GROUPS = [
    ("Workspace", [("Dashboard", PAGE_DASHBOARD), ("Terminal View", PAGE_TERMINAL), ("VS Code", PAGE_VSCODE),
                   ("Projects", PAGE_PROJECTS), ("Files", PAGE_FILES), ("Snippets", PAGE_SNIPPETS)]),
    ("Automation", [("Scripts", PAGE_SCRIPTS), ("Tools", PAGE_TOOLS), ("Autoclicker", PAGE_AUTOCLICKER),
                    ("PocketLauncher", PAGE_POCKETLAUNCHER), ("PocketVM", PAGE_POCKETVM),
                    ("C++ Compiler", PAGE_CPP)]),
    ("Extensions", [("Plugins", PAGE_PLUGINS)]),
    ("Network", [("Remote Access", PAGE_REMOTE_ACCESS)]),
    ("System", [("Drives", PAGE_DRIVES), ("Ports", PAGE_PORTS), ("Diagnostics", PAGE_DIAGNOSTICS),
                ("JAR Launcher", PAGE_JAR_LAUNCHER)]),
    ("AI & Web", [("PocketAi", PAGE_WEB)]),
]

DEFAULT_WEB_URL = "http://localhost:8080"
DRIVE_REFRESH_MS = 15000
MAX_RECENT = 10

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

# Global hotkey (Ctrl+Alt+P) to show/hide the window
WM_HOTKEY, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_P = 0x0312, 0x0001, 0x0002, 0x4000, 0x50
HOTKEY_ID = 0xB001
HOTKEY_TEXT = "Ctrl+Alt+P"

# Custom item-data roles used by the list rows
ROLE_PATH, ROLE_TITLE, ROLE_SUB, ROLE_BADGE, ROLE_KIND, ROLE_PIN, ROLE_PORT = range(256, 263)


def find_icon():
    """Return (path or None, human-readable status)."""
    seen = []
    for folder in ICON_DIRS:
        if folder in seen:
            continue
        seen.append(folder)
        if not folder.is_dir():
            continue
        if (folder / ICON_NAME).is_file():
            return folder / ICON_NAME, f"Icon loaded from: {folder / ICON_NAME}"
        pngs = sorted(folder.glob("*.png")) + sorted(folder.glob("*.PNG"))
        if pngs:
            return pngs[0], f"Icon loaded from: {pngs[0]} (no {ICON_NAME} found, used this instead)"
    return None, f"No icon found. Put {ICON_NAME} in one of:\n" + "\n".join(map(str, seen))


# ============================================================
# SETTINGS STORAGE
# ============================================================

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class Settings:
    DEFAULTS = {
        "launcher_path": "", "auto_start": True, "accent": "Violet", "custom_accent": "#8b5cf6",
        "remember_size": True, "window_size": [1100, 650], "last_page": 0, "nav_collapsed": [],
        "terminal_direct_launch": False,  # skip the PortableApps launcher, start Git Bash directly
        "vscode_command": "code", "vscode_args": "--new-window", "vscode_exe": "", "bash_path": "",
        "vscode_auto_start": False, "git_path": "",
        "pinned_projects": [], "recent_projects": [],
        "script_args": {},                                  # remembered arguments per script file name
        "tools": [{"name": "Notepad", "path": "notepad.exe", "args": ""}],
        "minimize_to_tray": False, "hotkey_enabled": True, "confirm_close": True,
        "low_space_alerts": True, "low_space_gb": 10,
        "web_url": "http://localhost:8080", "pocketai_command": "PocketAi", "pocketai_auto_start": True,
        "firefox_path": "",                                 # optional override; empty = auto-detect Firefox Portable
        "snippets": [{"name": "Git status", "command": "git status"}],
        "enabled_plugins": [],                              # only these plugins are executed
        "auto_update": True, "update_hash": "",  # GitHub auto update
    }
    STR_LISTS = ("pinned_projects", "recent_projects", "nav_collapsed", "enabled_plugins")
    PATH_KEYS = ("launcher_path", "bash_path", "git_path", "vscode_exe", "firefox_path")

    def __init__(self, path):
        self.path = path
        self.backup_path = path.with_name(path.name + ".bak")
        self.data = copy.deepcopy(self.DEFAULTS)
        self.load()

    @classmethod
    def _clean(cls, key, value, default):
        """Return a sanitised copy of value, or None if it can't be used."""
        if isinstance(default, bool):
            return value if isinstance(value, bool) else None
        if isinstance(default, int):
            return value if _is_int(value) else None
        if isinstance(default, str):
            return value if isinstance(value, str) else None
        if key == "window_size":
            ok = isinstance(value, list) and len(value) == 2 and all(_is_int(v) for v in value)
            return list(value) if ok else None
        if key in cls.STR_LISTS:
            return [v for v in value if isinstance(v, str)] if isinstance(value, list) else None
        if key == "script_args":
            if not isinstance(value, dict):
                return None
            return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
        if key in ("snippets", "tools"):
            if not isinstance(value, list):
                return None
            fields = ("name", "command") if key == "snippets" else ("name", "path")
            out = []
            for t in value:
                if isinstance(t, dict) and all(isinstance(t.get(f), str) for f in fields):
                    entry = {f: t[f] for f in fields}
                    if key == "tools":
                        entry["args"] = t["args"] if isinstance(t.get("args"), str) else ""
                    out.append(entry)
            return out
        return None

    def _merge(self, raw):
        """Copy known keys (with valid values) from raw. Returns how many were applied."""
        count = 0
        for key, default in self.DEFAULTS.items():
            if key in raw:
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
            out = copy.deepcopy(self.data)
            for key in self.PATH_KEYS:
                out[key] = portable_setting_path(out.get(key, ""))
            for tool in out.get("tools", []):
                tool["path"] = portable_setting_path(tool.get("path", ""))
            tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
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


# ============================================================
# GIT BASH / GIT / VS CODE / SCRIPT HELPERS
# ============================================================

def _custom_file(settings, key):
    c = str(settings.get(key) or "").strip().strip('"')
    p = resolve_configured_path(c) if c else None
    return p if p and p.is_file() else None


def find_launcher(settings):
    p = _custom_file(settings, "launcher_path")
    if p:
        return p
    return next((c for c in LAUNCHER_CANDIDATES if c.is_file()), LAUNCHER_CANDIDATES[0])


def _search(settings, rels, extra=()):
    """First existing `rel` below the portable root, the launcher folders, or `extra` bases."""
    launcher = find_launcher(settings)
    seen = set()
    for base in (PORTABLE_ROOT, launcher.parent, launcher.parent.parent, *extra):
        key = str(base).lower()
        if key in seen:
            continue
        seen.add(key)
        for rel in rels:
            if (base / rel).is_file():
                return base / rel
    return None


def _bash_bases(settings):
    bash = find_bash(settings)
    return (bash.parent.parent, bash.parent.parent.parent) if bash else ()


def find_bash(settings):
    """bash.exe: custom path, the portable bundle, or Windows PATH."""
    found = (_custom_file(settings, "bash_path")
             or _search(settings, ("App/Git/bin/bash.exe", "App/Git/usr/bin/bash.exe",
                                   "App/git/bin/bash.exe", "bin/bash.exe")))
    if found:
        return found
    w = shutil.which("bash.exe") or shutil.which("bash")
    return Path(w) if w else None


def find_git(settings):
    """git.exe: custom setting, next to the portable Git Bash, then PATH."""
    found = (_custom_file(settings, "git_path")
             or _search(settings, ("App/Git/cmd/git.exe", "App/Git/bin/git.exe", "App/Git/mingw64/bin/git.exe",
                                   "App/git/cmd/git.exe", "cmd/git.exe", "bin/git.exe", "mingw64/bin/git.exe"),
                        _bash_bases(settings)))
    if found:
        return found
    w = shutil.which("git")
    return Path(w) if w else None


def find_git_bash_exe(settings):
    """git-bash.exe inside the portable Git. Used to start a terminal WITHOUT the PortableApps
    launcher (which refuses to start while any other Git process is running)."""
    found = _search(settings, ("App/Git/git-bash.exe", "App/git/git-bash.exe", "git-bash.exe"),
                    _bash_bases(settings))
    if found:
        return found
    w = shutil.which("git-bash.exe")
    return Path(w) if w else None


def find_firefox(settings):
    """FirefoxPortable.exe: the custom setting, else searched for by walking upward from this script
    and from the portable root, so it works on any drive letter."""
    p = _custom_file(settings, "firefox_path")
    if p:
        return p
    rels = ("PocketLauncher/FirefoxPortable/FirefoxPortable.exe",
            "FirefoxPortable/FirefoxPortable.exe",
            "Assets/PocketLauncher/FirefoxPortable/FirefoxPortable.exe",
            "Assets/FirefoxPortable/FirefoxPortable.exe")
    seen = set()
    for start in (SCRIPT_DIR, PORTABLE_ROOT):
        for base in (start, *start.parents):
            key = str(base).lower()
            if key in seen:
                continue
            seen.add(key)
            for rel in rels:
                if (base / rel).is_file():
                    return base / rel
    return None


def open_in_firefox(settings, url):
    """Open url in Firefox Portable. Returns False if it isn't found or fails to start."""
    exe = find_firefox(settings)
    if exe is None:
        return False
    try:
        subprocess.Popen([str(exe), url], cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=CREATE_NEW_PROCESS_GROUP)
        return True
    except OSError as e:
        log.warning("Could not start Firefox Portable: %s", e)
        return False


# Process names that make the PortableApps Git launcher say "Another instance of Git is already running".
GIT_PROCESS_NAMES = {"git.exe", "git-bash.exe", "bash.exe", "mintty.exe", "sh.exe",
                     "gitbashportable.exe", "gitportable.exe", "git-cmd.exe"}


def _run_hidden(args, timeout=20, **kw):
    return subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout,
                          creationflags=CREATE_NO_WINDOW, **kw)


def git_processes_running():
    """True if any Git / Git Bash process is currently running."""
    if os.name != "nt":
        return False
    try:
        if psutil is not None:
            return any((p.info.get("name") or "").lower() in GIT_PROCESS_NAMES
                       for p in psutil.process_iter(["name"]))
        r = _run_hidden(["tasklist", "/FO", "CSV", "/NH"], 10)
        return any(row and row[0].lower() in GIT_PROCESS_NAMES for row in csv.reader(r.stdout.splitlines()))
    except Exception as e:
        log.warning("Could not check for running Git processes: %s", e)
    return False


def git_env(settings):
    """Environment for bash: point HOME at the portable home so your aliases load."""
    env = os.environ.copy()
    home = PORTABLE_ROOT / "Data" / "home"
    if home.is_dir():
        env["HOME"] = str(home)
    return env


def git_summary(git_exe, path):
    """(branch_text, status_text, kind) for a project folder, e.g. ("main ↑1", "● 3 changed", "warn"),
    or None if it isn't a repo."""
    if not (Path(path) / ".git").exists():
        return None
    try:
        r = subprocess.run(
            [str(git_exe), "-c", "safe.directory=*", "-C", str(path), "status", "--porcelain=v1", "-b"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.splitlines()
    if r.returncode != 0 or not lines or not lines[0].startswith("## "):
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
        for word, arrow in (("ahead", "\u2191"), ("behind", "\u2193")):
            n = re.search(word + r" (\d+)", m.group(1))
            if n:
                left.append(arrow + n.group(1))
    changes = len(lines) - 1
    if changes:
        return " ".join(left), f"\u25cf {changes} changed", "warn"
    return " ".join(left), "clean", "ok"


def repo_name_from_url(url):
    u = url.strip().replace("\\", "/").rstrip("/").split("/")[-1].split(":")[-1]
    return u[:-4] if u.lower().endswith(".git") else u


def split_args(text):
    """Split an argument string into a list, keeping quoted groups together."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        tokens = shlex.split(text, posix=False)
    except ValueError:
        return text.split()
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]


def python_exe():
    """python.exe (not pythonw.exe) so scripts get working stdout/stderr."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return str(exe.with_name("python.exe"))
    return str(exe)


def pythonw_exe():
    """pythonw.exe (no console window) when available, so a script's own GUI window can be embedded."""
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").is_file():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def kill_tree(pid):
    """Kill a process and all its children (Windows). Returns True on success."""
    if not pid or os.name != "nt":
        return False
    try:
        return subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                              creationflags=CREATE_NO_WINDOW).returncode == 0
    except OSError as e:
        log.warning("taskkill failed for pid %s: %s", pid, e)
        return False


def vscode_launch_plan(settings, target=None):
    """Return (args, popen_kwargs, description). args is None on error (description = reason)."""
    extra = str(settings.get("vscode_args")).strip()
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    exe = str(settings.get("vscode_exe") or "").strip().strip('"')
    if exe:
        p = resolve_configured_path(exe)
        if not p or not p.is_file():
            return None, None, f"VS Code executable not found: {p or exe}"
        args = [str(p)] + split_args(extra) + ([str(target)] if target else [])
        return args, dict(quiet, cwd=str(p.parent)), f"Runs directly: {p}"

    bash = find_bash(settings)
    if not bash:
        return None, None, "Git Bash (bash.exe) not found. Set its path below, or set the VS Code executable instead."
    cmd = str(settings.get("vscode_command")).strip() or "code"
    line = cmd + (f" {extra}" if extra else "")
    if target:
        line += " " + shlex.quote(Path(target).as_posix())
    kwargs = dict(quiet, cwd=str(APP_DIR / "Projects"), env=git_env(settings), creationflags=CREATE_NO_WINDOW)
    return [str(bash), "-l", "-i", "-c", line], kwargs, f"Runs '{cmd}' through Git Bash: {bash}"


def run_script(path, settings, args_text=""):
    """Run a script in its own console window. Returns an error string or None."""
    ext, env = path.suffix.lower(), None
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
        subprocess.Popen(cmd, cwd=str(path.parent), env=env,
                         creationflags=CREATE_NEW_CONSOLE if os.name == "nt" else 0)
    except OSError as e:
        return f"Failed to start: {e}"


def script_command(path, settings, args):
    """Build (program, argv, env_dict_or_None, error) for running a script with captured output."""
    ext = path.suffix.lower()
    if ext in (".bat", ".cmd"):
        return "cmd.exe", ["/c", str(path)] + args, None, None
    if ext == ".py":
        return python_exe(), ["-u", str(path)] + args, dict(os.environ, PYTHONIOENCODING="utf-8"), None
    if ext == ".ps1":
        return "powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)] + args, None, None
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
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
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
    """List of dicts (root, name, fs, kind, total, free) for every ready drive."""
    drives, seen = [], set()
    for v in QStorageInfo.mountedVolumes():
        if not v.isValid() or not v.isReady() or int(v.bytesTotal()) <= 0:
            continue
        root = os.path.normpath(v.rootPath())
        if root in seen:
            continue
        seen.add(root)
        drives.append({"root": root, "name": v.name() or "",
                       "fs": bytes(v.fileSystemType()).decode("utf-8", errors="replace"),
                       "kind": drive_kind(root), "total": int(v.bytesTotal()), "free": int(v.bytesAvailable())})
    return sorted(drives, key=lambda d: d["root"].lower())


def normalize_url(text):
    """'3000', ':3000', 'localhost:3000' and full URLs all become proper URLs."""
    t = (text or "").strip()
    if not t:
        return DEFAULT_WEB_URL
    if re.fullmatch(r"\d{2,5}", t):
        return f"http://localhost:{t}"
    if t.startswith(":"):
        return "http://localhost" + t
    return t if "://" in t else "http://" + t


def drive_of(path):
    """'E:' for a path on drive E, or '' if the path has no drive letter."""
    return os.path.splitdrive(str(path))[0].upper()


EJECT_SCRIPT = ("$i = (New-Object -comObject Shell.Application).Namespace(17).ParseName('{drive}'); "
                "if ($i -eq $null) {{ exit 2 }}; $i.InvokeVerb('Eject')")


def eject_drive_now(drive):
    """Ask Windows to eject a drive like 'E:'. Returns (ok, message)."""
    try:
        r = _run_hidden(["powershell.exe", "-NoProfile", "-Command", EJECT_SCRIPT.format(drive=drive)], 30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Couldn't run the eject command: {e}"
    if r.returncode == 2:
        return False, f"Windows doesn't list {drive} as a drive that can be ejected."
    time.sleep(2.5)  # the eject verb returns immediately; give Windows a moment, then check
    if os.path.exists(drive + "\\"):
        return False, (f"{drive} is still there. Something may be using it: close programs that "
                       "have files open on it and try again.")
    return True, f"Ejected {drive}. It's safe to unplug it."


def schedule_eject_after_exit(drive):
    """Start a detached PowerShell that waits for PocketTerm to exit, then ejects the drive."""
    script = "Start-Sleep -Seconds 4; " + EJECT_SCRIPT.format(drive=drive)
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            cwd=os.environ.get("SystemRoot") or os.environ.get("WINDIR") or os.getcwd(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
            creationflags=0x00000008 | CREATE_NEW_PROCESS_GROUP)  # DETACHED_PROCESS
    except OSError as e:
        return f"Couldn't start the eject helper: {e}"


def parse_listening(netstat_text, names):
    """Parse `netstat -ano` output into listening TCP ports. Language independent."""
    ports, seen = [], set()
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[2] not in ("0.0.0.0:0", "[::]:0", "*:*"):
            continue
        addr, _, port = parts[1].rpartition(":")
        if not (port.isdigit() and parts[4].isdigit()) or (int(port), int(parts[4])) in seen:
            continue
        key = (int(port), int(parts[4]))
        seen.add(key)
        ports.append({"port": key[0], "pid": key[1], "name": names.get(key[1], "unknown"),
                      "addr": addr.strip("[]")})
    return sorted(ports, key=lambda p: (p["port"], p["pid"]))


def list_listening_ports():
    """Listening TCP ports with the owning program (Windows only)."""
    if os.name != "nt":
        return []
    net = _run_hidden(["netstat", "-ano"])
    names = {}
    try:
        for row in csv.reader(_run_hidden(["tasklist", "/FO", "CSV", "/NH"]).stdout.splitlines()):
            if len(row) >= 2 and row[1].isdigit():
                names[int(row[1])] = row[0]
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("tasklist failed: %s", e)
    return parse_listening(net.stdout, names)


# ============================================================
# THEME
# ============================================================

TEXT, TEXT_BODY, TEXT_MUTED, TEXT_FAINT, TEXT_PLACEHOLDER = "#f3f0ff", "#d9d3f2", "#a69fc4", "#857ca8", "#7d739c"
OK_COLOR, ERR_COLOR, WARN_COLOR, LOG_COLOR = "#6ee7b7", "#fda4af", "#fcd34d", "#e4dffa"

ACCENTS = {  # accent, strong, soft, light, lighter, border, hover-border
    "Violet":  ("#8b5cf6", "#7c3aed", "#a78bfa", "#c4b5fd", "#ddd6fe", "#3b2a66", "#5b3fb5"),
    "Magenta": ("#d946ef", "#c026d3", "#e879f9", "#f0abfc", "#f5d0fe", "#5b2a66", "#a03fb5"),
    "Indigo":  ("#6366f1", "#4f46e5", "#818cf8", "#a5b4fc", "#c7d2fe", "#2a2f66", "#3f4fb5"),
    "Emerald": ("#10b981", "#059669", "#34d399", "#6ee7b7", "#a7f3d0", "#1f4a3c", "#2f8f6f"),
}
ACCENT_CHOICES = list(ACCENTS) + ["Custom"]
BACKGROUND = "#08070d"
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def mix(c1, c2, t):
    """Blend two #rrggbb colors. t=0 gives c1, t=1 gives c2."""
    a, b = c1.lstrip("#"), c2.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i+2], 16) * (1 - t) + int(b[i:i+2], 16) * t):02x}" for i in (0, 2, 4))


def luminance(color):
    c = color.lstrip("#")
    ch = lambda v: (v / 255) / 12.92 if v / 255 <= 0.03928 else ((v / 255 + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(int(c[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(c1, c2):
    la, lb = luminance(c1), luminance(c2)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def on_color(bg):
    """Readable text color (white or near-black) for text drawn on top of bg."""
    return "#ffffff" if contrast(bg, "#ffffff") >= 3.5 else "#0b0816"


def custom_palette(base):
    return (base, mix(base, "#000000", 0.15), mix(base, "#ffffff", 0.20), mix(base, "#ffffff", 0.45),
            mix(base, "#ffffff", 0.65), mix(base, BACKGROUND, 0.72), mix(base, BACKGROUND, 0.40))


def get_palette(settings):
    name = settings.get("accent")
    if name == "Custom":
        base = str(settings.get("custom_accent"))
        return custom_palette(base if HEX_RE.fullmatch(base) else ACCENTS["Violet"][0])
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
QMainWindow, QWidget { background-color: #08070d; color: @TEXT_BODY@; font-family: 'Segoe UI'; }
QToolTip { background-color: #1e1535; color: @TEXT@; border: 1px solid @HOVERBORDER@; padding: 4px 8px; }
QLabel { background: transparent; }
QFrame#Sidebar { background-color: #0e0a18; border: none; border-right: 1px solid #231a3a; }
QLabel#Logo { font-family: 'Consolas'; font-size: 26px; font-weight: 700; color: @TEXT@; }
QLabel#Subtitle { color: @TEXT_FAINT@; font-size: 9px; font-weight: 600; }
QLabel#PageTitle { font-size: 24px; font-weight: 700; color: @TEXT@; }
QLabel#Description, QLabel#Muted { color: @TEXT_MUTED@; font-size: 12px; }
QLabel#SectionTitle { font-size: 15px; font-weight: 700; color: @LIGHTER@; }
QLabel#Status { color: @SOFT@; font-weight: bold; }
QFrame#Card { background-color: #130e21; border: 1px solid #2a1f45; border-radius: 12px; }
QFrame#TerminalFrame { background-color: #000000; border: 1px solid @BORDER@; border-radius: 8px; }
QDialog#Palette { background-color: #130e21; border: 1px solid @HOVERBORDER@; }

QPushButton { background-color: #1a1230; color: #e4dffa; border: 1px solid #2a1f45; border-radius: 8px;
    padding: 10px 15px; font-size: 12px; }
QPushButton:hover { background-color: #2a1d4d; border: 1px solid @HOVERBORDER@; }
QPushButton:disabled { background-color: #120d20; color: #6f6590; border: 1px solid #1f1736; }
QPushButton#Navigation { background-color: transparent; border: 1px solid transparent; border-radius: 12px;
    color: #b5acd4; text-align: left; padding: 9px 16px; font-size: 13px; }
QPushButton#Navigation:hover { background-color: #181129; border: 1px solid transparent; color: @TEXT@; }
QPushButton#Navigation:checked { background-color: #1e1535; border: 1px solid @HOVERBORDER@;
    border-left: 3px solid @ACCENT@; color: @LIGHTER@; font-weight: 600; }
QPushButton#NavGroup { background-color: transparent; border: none; border-radius: 8px; color: @TEXT_FAINT@;
    text-align: left; padding: 8px 10px; font-size: 10px; font-weight: 700; }
QPushButton#NavGroup:hover { background-color: #181129; color: @TEXT@; }
QFrame#NavGroupBody { background: transparent; border: none; }
QWidget#DevToolsPanel { background-color: #130e21; border: 1px solid @ACCENT@; }
QWidget#DevBar { background-color: #1e1535; }
QLabel#DevTitle { color: @LIGHTER@; font-weight: 700; font-size: 11px; }
QPushButton#Primary { background-color: @STRONG@; color: @ON_STRONG@; border: none; font-weight: bold; }
QPushButton#Primary:hover { background-color: @ACCENT@; color: @ON_ACCENT@; }
QPushButton#CornerButton { background-color: @STRONG@; color: @ON_STRONG@; border: none; border-radius: 6px;
    padding: 5px 12px; margin: 0px 0px 6px 6px; font-weight: bold; }
QPushButton#CornerButton:hover { background-color: @ACCENT@; color: @ON_ACCENT@; }
QPushButton#Danger { background-color: #2a1220; border: 1px solid #5b1f3a; color: @ERR@; }
QPushButton#Danger:hover { background-color: #3d1730; border: 1px solid #be3a68; }
QPushButton#TabClose { background-color: transparent; border: none; border-radius: 4px; padding: 0px;
    font-size: 15px; color: @TEXT_MUTED@; }
QPushButton#TabClose:hover { background-color: #3d1730; color: @ERR@; }

QLineEdit, QComboBox, QSpinBox { background-color: #0e0a18; color: @TEXT@; border: 1px solid #2a1f45;
    border-radius: 8px; font-size: 12px; selection-background-color: @STRONG@; selection-color: @ON_STRONG@;
    placeholder-text-color: @TEXT_PLACEHOLDER@; }
QLineEdit { padding: 9px 12px; }
QComboBox { padding: 8px 12px; min-width: 140px; }
QSpinBox { padding: 7px 10px; min-width: 80px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:hover, QComboBox:focus { border: 1px solid @ACCENT@; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; border: none; width: 26px; }
QComboBox::down-arrow { image: @ARROW@; width: 10px; height: 10px; }
QComboBox QAbstractItemView { background-color: #130e21; border: 1px solid #2a1f45;
    selection-background-color: @STRONG@; selection-color: @ON_STRONG@; color: @TEXT@; outline: 0; }
QPlainTextEdit#Log { background-color: #0e0a18; border: 1px solid #2a1f45; border-radius: 8px; padding: 6px;
    font-family: 'Consolas'; font-size: 12px; color: @LOG@; selection-background-color: @STRONG@;
    selection-color: @ON_STRONG@; }

QListWidget { background-color: #0e0a18; border: 1px solid #2a1f45; border-radius: 8px; padding: 6px;
    font-size: 13px; outline: 0; }
QListWidget::item { padding: 9px 10px; border-radius: 6px; }
QTreeWidget { background-color: #0e0a18; alternate-background-color: #130e21;
    border: 1px solid #2a1f45; border-radius: 8px; padding: 5px; outline: 0; }
QTreeWidget::item { padding: 5px 4px; border-radius: 4px; }
QListWidget::item:hover, QTreeWidget::item:hover { background-color: #181129; }
QListWidget::item:selected, QTreeWidget::item:selected { background-color: @STRONG@; color: @ON_STRONG@; }
QHeaderView::section { background-color: #171126; color: @TEXT_MUTED@; border: none;
    border-bottom: 1px solid #2a1f45; padding: 6px 8px; font-weight: bold; }

QTabWidget#Tabs::pane { border: 1px solid @BORDER@; border-radius: 8px; background: #000000; top: 0px; padding: 4px; }
QTabBar { background: transparent; }
QTabBar::tab { background-color: #130e21; color: @TEXT_MUTED@; border: 1px solid #2a1f45; border-bottom: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 7px 12px; margin-right: 3px;
    margin-bottom: 6px; font-size: 12px; }
QTabBar::tab:selected { background-color: #1e1535; color: @LIGHTER@; border-color: @HOVERBORDER@; }
QTabBar::tab:hover:!selected { background-color: #181129; color: @TEXT@; }

QMenu { background-color: #130e21; border: 1px solid #2a1f45; padding: 4px; }
QMenu::item { padding: 7px 22px; border-radius: 6px; }
QMenu::item:selected { background-color: @STRONG@; color: @ON_STRONG@; }
QMenu::separator { height: 1px; background: #2a1f45; margin: 4px 8px; }

QCheckBox { background: transparent; spacing: 10px; font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid @BORDER@; border-radius: 4px; background: #0e0a18; }
QCheckBox::indicator:hover { border: 1px solid @ACCENT@; }
QCheckBox::indicator:checked { background: @ACCENT@; border: 1px solid @ACCENT@; image: @CHECK@; }

QProgressBar { background-color: #0e0a18; border: 1px solid #2a1f45; border-radius: 6px; }
QProgressBar::chunk { background-color: @ACCENT@; border-radius: 5px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:vertical { height: 8px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #08070d; width: 10px; }
QScrollBar:horizontal { background: #08070d; height: 10px; }
QScrollBar::handle:vertical { background: @BORDER@; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:horizontal { background: @BORDER@; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: @HOVERBORDER@; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
"""


def build_style(settings):
    accent, strong, soft, light, lighter, border, hover_border = get_palette(settings)
    check = ui_icon("check", on_color(accent), 16, [(3.5, 8.5), (6.5, 11.5), (12.0, 5.0)], 2.2)
    arrow = ui_icon("arrow", TEXT_MUTED, 10, [(2.0, 3.5), (5.0, 6.5), (8.0, 3.5)], 1.6)
    style = STYLE_TEMPLATE
    for token, value in (
        ("@ACCENT@", accent), ("@STRONG@", strong), ("@SOFT@", soft), ("@LIGHT@", light),
        ("@LIGHTER@", lighter), ("@BORDER@", border), ("@HOVERBORDER@", hover_border),
        ("@ON_STRONG@", on_color(strong)), ("@ON_ACCENT@", on_color(accent)),
        ("@TEXT_BODY@", TEXT_BODY), ("@TEXT_MUTED@", TEXT_MUTED), ("@TEXT_FAINT@", TEXT_FAINT),
        ("@TEXT_PLACEHOLDER@", TEXT_PLACEHOLDER), ("@TEXT@", TEXT), ("@ERR@", ERR_COLOR),
        ("@LOG@", LOG_COLOR), ("@CHECK@", check), ("@ARROW@", arrow),
    ):
        style = style.replace(token, value)
    return style


# ============================================================
# SMALL WIDGETS: LIST ROW DELEGATE, COMMAND PALETTE, DONUT
# ============================================================

class RowDelegate(QStyledItemDelegate):
    """Paints a row as: title + muted subtitle on the left, a colored badge on the right."""
    ROW_H = 54
    BADGES = {"ok": ("#12352a", OK_COLOR), "warn": ("#3a2d0f", WARN_COLOR),
              "info": ("#231a3a", "#c4b5fd"), "muted": ("#1a1230", TEXT_MUTED)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strong, self.on_strong = "#7c3aed", "#ffffff"

    def set_colors(self, strong, on_strong):
        self.strong, self.on_strong = strong, on_strong

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_H)

    @staticmethod
    def _font(base, size, bold=False):
        f = QFont(base)
        f.setPointSizeF(size)
        f.setBold(bold)
        return f

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.strong if selected else "#181129" if hover else "#130e21"))
        painter.drawRoundedRect(rect, 8, 8)

        title = str(index.data(ROLE_TITLE) or "")
        sub = str(index.data(ROLE_SUB) or "")
        badge = str(index.data(ROLE_BADGE) or "")
        kind = str(index.data(ROLE_KIND) or "muted")
        if index.data(ROLE_PIN):
            title = "\u2605 " + title
        title_font = self._font(option.font, 10.5, True)
        sub_font = self._font(option.font, 8.5)
        badge_font = self._font(option.font, 8.5, True)

        right_edge = rect.right() - 12
        if badge:
            bw = QFontMetrics(badge_font).horizontalAdvance(badge) + 18
            brect = QRect(right_edge - bw, rect.center().y() - 11, bw, 22)
            if selected:
                badge_bg, badge_fg = QColor(0, 0, 0, 70), QColor(self.on_strong)
            else:
                bg_hex, fg_hex = self.BADGES.get(kind, self.BADGES["muted"])
                badge_bg, badge_fg = QColor(bg_hex), QColor(fg_hex)
            painter.setBrush(badge_bg)
            painter.drawRoundedRect(brect, 11, 11)
            painter.setFont(badge_font)
            painter.setPen(badge_fg)
            painter.drawText(brect, Qt.AlignCenter, badge)
            right_edge = brect.left() - 12

        left = rect.left() + 14
        text_w = max(40, right_edge - left)
        for text, font, color, top, h in ((title, title_font, self.on_strong if selected else TEXT, 7, 20),
                                          (sub, sub_font, self.on_strong if selected else TEXT_MUTED, 28, 16)):
            if text:
                painter.setFont(font)
                painter.setPen(QColor(color))
                painter.drawText(QRect(left, rect.top() + top, text_w, h), Qt.AlignLeft | Qt.AlignVCenter,
                                 QFontMetrics(font).elidedText(text, Qt.ElideRight, text_w))
        painter.restore()


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
        self.list.itemClicked.connect(lambda _i: self._run())
        layout.addWidget(self.list, stretch=1)
        self._filter("")

    def _filter(self, text):
        tokens = text.lower().split()
        self.list.clear()
        for i, (label, _cb) in enumerate(self.commands):
            if all(t in label.lower() for t in tokens):
                item = QListWidgetItem(label)
                item.setData(ROLE_PATH, i)
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Down, Qt.Key_Up):
            step = 1 if event.key() == Qt.Key_Down else -1
            if self.list.count():
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + step)))
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


class DonutUsageWidget(QWidget):
    """Compact live usage donut for the dashboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value, self.available = 0.0, True
        self.setMinimumSize(112, 112)
        self.setMaximumSize(132, 132)

    def set_value(self, value, available=True):
        self.value = max(0.0, min(100.0, float(value or 0.0)))
        self.available = bool(available)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        d = min(self.width(), self.height()) - 18
        rect = QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#302541"), 10, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -360 * 16)
        if self.available:
            painter.setPen(QPen(QColor("#a879ff"), 10, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 90 * 16, -int(self.value * 3.6 * 16))
        painter.setPen(QColor("#f2eaff"))
        painter.setFont(QFont("Segoe UI", 16, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignCenter, f"{self.value:.0f}%" if self.available else "N/A")
        painter.end()


# ============================================================
# EMBEDDED EXTERNAL WINDOWS (BASE CLASS + TERMINAL + VS CODE + TOOLS)
# ============================================================

class EmbeddedWindow(QWidget):
    """Launches an external program and embeds its top-level window into this widget.
    Subclasses define how to launch it and how to recognise its window."""

    NAME = "Application"
    TIMEOUT_MS = 15000
    TIMEOUT_MESSAGE = "Started, but no window was captured."
    IDLE_MESSAGE = "Nothing is running here yet."

    GWL_STYLE = -16
    WS_POPUP, WS_CHILD, WS_VISIBLE = 0x80000000, 0x40000000, 0x10000000
    WS_CAPTION, WS_SYSMENU, WS_THICKFRAME = 0x00C00000, 0x00080000, 0x00040000
    WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
    SWP_NOZORDER, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0004, 0x0020, 0x0040
    WM_CLOSE, WM_CHAR = 0x0010, 0x0102

    _claimed = set()  # windows already embedded by any EmbeddedWindow, so two tabs never grab the same one

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

        def timer(ms, slot, single=False):
            t = QTimer(self)
            t.setInterval(ms)
            t.setSingleShot(single)
            t.timeout.connect(slot)
            return t
        self.poll_timer = timer(250, self.find_window)
        self.timeout_timer = timer(0, self.embedding_timeout, True)
        self.watch_timer = timer(1000, self._check_session)  # detects the program being closed

        self.status_label = QLabel(self.IDLE_MESSAGE)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; padding: 12px 20px; font-size: 14px; background: transparent;")
        self.start_button = QPushButton(f"Start {self.NAME}")
        self.start_button.setObjectName("Primary")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.setMinimumWidth(180)
        self.start_button.clicked.connect(lambda _=False: self.start())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        lay.addWidget(self.status_label)
        lay.addWidget(self.start_button, alignment=Qt.AlignHCenter)
        lay.addStretch(1)

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

    def on_poll(self):
        """Called on every poll tick while waiting. Return True to skip this tick."""
        return False

    # ---------- win32 plumbing ----------

    def _setup_win32_signatures(self):
        u, k = self.user32, self.kernel32
        W, T = wintypes, ctypes
        enum_proc = T.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        for lib, name, args, res in (
            (u, "EnumWindows", [enum_proc, W.LPARAM], W.BOOL),
            (u, "IsWindowVisible", [W.HWND], W.BOOL),
            (u, "IsWindow", [W.HWND], W.BOOL),
            (u, "GetClassNameW", [W.HWND, W.LPWSTR, T.c_int], T.c_int),
            (u, "GetWindowTextW", [W.HWND, W.LPWSTR, T.c_int], T.c_int),
            (u, "GetWindow", [W.HWND, W.UINT], W.HWND),
            (u, "GetWindowRect", [W.HWND, T.POINTER(W.RECT)], W.BOOL),
            (u, "SetParent", [W.HWND, W.HWND], W.HWND),
            (u, "GetWindowLongPtrW", [W.HWND, T.c_int], T.c_ssize_t),
            (u, "SetWindowLongPtrW", [W.HWND, T.c_int, T.c_ssize_t], T.c_ssize_t),
            (u, "SetWindowPos", [W.HWND, W.HWND, T.c_int, T.c_int, T.c_int, T.c_int, W.UINT], W.BOOL),
            (u, "PostMessageW", [W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            (u, "SetFocus", [W.HWND], W.HWND),
            (u, "GetWindowThreadProcessId", [W.HWND, T.POINTER(W.DWORD)], W.DWORD),
            (u, "AttachThreadInput", [W.DWORD, W.DWORD, W.BOOL], W.BOOL),
            (k, "GetCurrentThreadId", None, W.DWORD),
            (k, "OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            (k, "QueryFullProcessImageNameW", [W.HANDLE, W.DWORD, W.LPWSTR, T.POINTER(W.DWORD)], W.BOOL),
            (k, "CloseHandle", [W.HANDLE], W.BOOL),
        ):
            fn = getattr(lib, name)
            if args is not None:
                fn.argtypes = args
            fn.restype = res

    def _text(self, fn, hwnd, size):
        buf = ctypes.create_unicode_buffer(size)
        fn(hwnd, buf, len(buf))
        return buf.value

    def window_class(self, hwnd):
        return self._text(self.user32.GetClassNameW, hwnd, 256).lower()

    def window_title(self, hwnd):
        return self._text(self.user32.GetWindowTextW, hwnd, 512)

    def window_size(self, hwnd):
        rect = wintypes.RECT()
        if self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.right - rect.left, rect.bottom - rect.top
        return 0, 0

    def has_owner(self, hwnd):
        return bool(self.user32.GetWindow(hwnd, 4))  # GW_OWNER

    def process_name(self, hwnd):
        """Executable file name (lowercase) of the process that owns the window."""
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
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

    def _enum_windows(self, predicate):
        found = []
        cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, lparam):
            try:
                if self.user32.IsWindowVisible(hwnd) and predicate(hwnd):
                    found.append(int(hwnd))
            except Exception:
                pass
            return True

        self.user32.EnumWindows(cb_type(callback), 0)
        return found

    def get_candidate_windows(self):
        return set(self._enum_windows(self.accepts)) if os.name == "nt" else set()

    def _alive(self):
        return bool(self.hwnd) and bool(self.user32.IsWindow(wintypes.HWND(self.hwnd)))

    # ---------- lifecycle ----------

    def set_message(self, text, offer_start=True):
        self.status_label.setText(text)
        self.status_label.show()
        self.start_button.setText(f"Start {self.NAME}")
        self.start_button.setVisible(offer_start)

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

    def _release(self):
        """Forget the embedded window (shared by stop and 'closed by user')."""
        self._detach_input()
        EmbeddedWindow._claimed.discard(self.hwnd)
        self.hwnd = None

    def stop(self, message=None):
        for t in (self.poll_timer, self.timeout_timer, self.watch_timer):
            t.stop()
        if self._alive():
            self.user32.PostMessageW(wintypes.HWND(self.hwnd), self.WM_CLOSE, 0, 0)
        self._release()
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
        if self.hwnd and not self._alive():
            self.watch_timer.stop()
            self._release()
            self.process = None
            self.launcher_started = False
            self.set_message(f"{self.NAME} was closed.")

    def find_window(self):
        if self.hwnd:
            self.poll_timer.stop()
            return
        if self.on_poll():
            return
        new = self.get_candidate_windows() - self.previous_windows - EmbeddedWindow._claimed
        if new:
            self.hwnd = next(iter(new))
            EmbeddedWindow._claimed.add(self.hwnd)
            self.poll_timer.stop()
            self.timeout_timer.stop()
            self.status_label.hide()
            self.start_button.hide()
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
        self.user32.SetParent(hwnd_w, wintypes.HWND(int(self.winId())))
        style = self.user32.GetWindowLongPtrW(hwnd_w, self.GWL_STYLE)
        style &= ~(self.WS_POPUP | self.WS_CAPTION | self.WS_SYSMENU | self.WS_THICKFRAME |
                   self.WS_MINIMIZEBOX | self.WS_MAXIMIZEBOX)
        style |= self.WS_CHILD | self.WS_VISIBLE
        self.user32.SetWindowLongPtrW(hwnd_w, self.GWL_STYLE, style)
        self.resize_embedded_window()
        self.focus_window()

    def _attach_input(self):
        """Share the input queue with the child's thread and keep it that way."""
        if self._attached or os.name != "nt" or not self.hwnd:
            return
        ours = self.kernel32.GetCurrentThreadId()
        theirs = self.user32.GetWindowThreadProcessId(wintypes.HWND(self.hwnd), None)
        if theirs and theirs != ours and self.user32.AttachThreadInput(ours, theirs, True):
            self._attached = (ours, theirs)

    def _detach_input(self):
        if self._attached and os.name == "nt":
            self.user32.AttachThreadInput(self._attached[0], self._attached[1], False)
        self._attached = None

    def focus_window(self):
        """Give keyboard focus to the embedded window."""
        if os.name == "nt" and self._alive():
            self._attach_input()
            self.user32.SetFocus(wintypes.HWND(self.hwnd))

    def send_text(self, text):
        """Type text into the embedded window (best effort, Windows only)."""
        if os.name != "nt" or not self._alive():
            return False
        data = text.replace("\r\n", "\r").replace("\n", "\r").encode("utf-16-le")  # UTF-16 units: emoji survive
        for i in range(0, len(data), 2):
            self.user32.PostMessageW(wintypes.HWND(self.hwnd), self.WM_CHAR,
                                     int.from_bytes(data[i:i + 2], "little"), 0)
        return True

    def focus_soon(self):
        """Qt may re-grab focus after activation, so retry a few times."""
        for delay in (30, 150, 400):
            QTimer.singleShot(delay, self.focus_window)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_soon()

    def resize_embedded_window(self):
        if self._alive():
            dpr = self.devicePixelRatioF()
            self.user32.SetWindowPos(wintypes.HWND(self.hwnd), None, 0, 0, int(self.width() * dpr),
                                     int(self.height() * dpr),
                                     self.SWP_NOZORDER | self.SWP_FRAMECHANGED | self.SWP_SHOWWINDOW)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_embedded_window()

    def showEvent(self, event):
        super().showEvent(event)
        if self.hwnd and os.name == "nt":
            self.resize_embedded_window()
            self.focus_soon()


class EmbeddedGitBash(EmbeddedWindow):
    """Git Bash terminal.

    The PortableApps launcher (GitBashPortable.exe) refuses to start when any other Git process is
    running. To avoid that: if Git processes are already running, or "terminal_direct_launch" is on,
    git-bash.exe is started directly; if the launcher still shows its error box, the box is closed
    and the terminal is started directly instead."""

    NAME = "Terminal"
    IDLE_MESSAGE = "GitPortable will appear here when it starts."
    TIMEOUT_MESSAGE = ("GitPortable started, but no terminal window was captured.\n"
                       "Check the launcher path in Settings.")
    LAUNCHER_DIALOG_CLASS = "#32770"

    def __init__(self, settings, parent=None):
        super().__init__(settings, parent)
        self._force_direct = False     # set after the launcher showed its "already running" error
        self._using_launcher = False   # True while the current start went through the launcher
        self._last_target = None

    def build_launch(self, target=None):
        self._last_target = target
        has_target = bool(target and Path(target).is_dir())
        direct = find_git_bash_exe(self.settings)
        launcher = find_launcher(self.settings)
        want_direct = (self._force_direct or bool(self.settings.get("terminal_direct_launch"))
                       or git_processes_running())

        if direct is not None and (want_direct or not launcher.is_file()):
            env = git_env(self.settings)
            home = PORTABLE_ROOT / "Data" / "home"
            if has_target:
                env["CHERE_INVOKING"] = "1"
                cwd, args = str(target), [str(direct)]
            else:
                cwd = str(home) if home.is_dir() else str(direct.parent)
                args = [str(direct), "--cd-to-home"]
            self._using_launcher = False
            return args, {"cwd": cwd, "env": env, "creationflags": CREATE_NEW_PROCESS_GROUP}, ""

        if not launcher.is_file():
            return None, None, f"Error: Launcher not found at:\n{launcher}"
        kwargs = {"cwd": str(launcher.parent), "creationflags": CREATE_NEW_PROCESS_GROUP}
        if has_target:  # best effort: the login profile stays in the current directory
            kwargs["cwd"] = str(target)
            kwargs["env"] = dict(os.environ, CHERE_INVOKING="1")
        self._using_launcher = True
        return [str(launcher)], kwargs, ""

    def accepts(self, hwnd):
        return self.window_class(hwnd) == "mintty"

    def _is_launcher_dialog(self, hwnd):
        if self.window_class(hwnd) != self.LAUNCHER_DIALOG_CLASS:
            return False
        title = self.window_title(hwnd).lower()
        return "portableapps.com launcher" in title and "git" in title

    def on_poll(self):
        """Automatic recovery from "Another instance of Git is already running"."""
        if os.name != "nt" or not self._using_launcher or self._force_direct:
            return False
        dialogs = self._enum_windows(self._is_launcher_dialog)
        if not dialogs:
            return False
        for h in dialogs:
            self.user32.PostMessageW(wintypes.HWND(h), self.WM_CLOSE, 0, 0)
        log.info("Git launcher reported another running instance; starting Git Bash directly.")
        self.poll_timer.stop()
        self.timeout_timer.stop()
        proc, self.process = self.process, None
        if proc is not None and proc.poll() is None and not kill_tree(proc.pid):
            proc.terminate()
        self.launcher_started = False
        self._force_direct = True
        self.set_message("Git is already running, starting Git Bash directly...", False)
        target = self._last_target
        QTimer.singleShot(500, lambda: self.start(target))
        return True


class EmbeddedVSCode(EmbeddedWindow):
    NAME = "VS Code"
    TIMEOUT_MS = 45000
    IDLE_MESSAGE = "VS Code isn't running."
    TIMEOUT_MESSAGE = ("VS Code was launched, but its window wasn't captured.\n"
                       "Check the VS Code settings (alias, Git Bash path, or executable path).")

    def build_launch(self, target=None):
        args, kwargs, info = vscode_launch_plan(self.settings, target)
        return (args, kwargs, "") if args is not None else (None, None, info)

    def accepts(self, hwnd):
        # Electron windows all share one class, so also check the owning executable.
        if self.window_class(hwnd) != "chrome_widgetwin_1" or self.has_owner(hwnd):
            return False
        w, h = self.window_size(hwnd)
        name = self.process_name(hwnd)
        return w >= 300 and h >= 200 and name.startswith(("code", "vscodium"))


class EmbeddedTool(EmbeddedWindow):
    """Any program from the Tools page. Its window is recognised by executable name."""

    def __init__(self, settings, name, exe, args_text="", parent=None):
        super().__init__(settings, parent)
        self.exe, self.args_text = str(exe), str(args_text or "")
        self.exe_name = Path(self.exe).name.lower()
        self.NAME = name
        self.TIMEOUT_MS = 25000
        self.IDLE_MESSAGE = f"{name} will appear here when it starts."
        self.TIMEOUT_MESSAGE = (f"{name} was launched, but its window wasn't captured.\n"
                                "Apps that are already running or that reuse an existing window "
                                "can't be captured. Close them first and try again.")
        self.set_message(self.IDLE_MESSAGE)

    def build_launch(self, target=None):
        p = Path(self.exe)
        if not p.is_file():
            return None, None, f"Program not found:\n{p}"
        return ([str(p)] + split_args(self.args_text),
                {"cwd": str(p.parent), "creationflags": CREATE_NEW_PROCESS_GROUP}, "")

    def accepts(self, hwnd):
        if self.has_owner(hwnd):
            return False
        w, h = self.window_size(hwnd)
        return w >= 200 and h >= 120 and self.process_name(hwnd) == self.exe_name


class EmbeddedScript(EmbeddedWindow):
    """Runs one of PocketTerm's own .py files and embeds the window that script opens.
    The window is recognised by process id (the script's process or any of its children)."""

    def __init__(self, settings, name, script_file, parent=None):
        super().__init__(settings, parent)
        self.script = Path(script_file)
        self.NAME = name
        self.TIMEOUT_MS = 30000
        self.IDLE_MESSAGE = f"Press Start to run {self.script.name}.\nIts window will appear here."
        self.TIMEOUT_MESSAGE = (f"{self.script.name} was started, but it didn't open a window to embed.\n"
                                "Scripts without a GUI window (or that exited right away) can't be shown here.")
        self.toggle_btn = None
        self.set_message(self.IDLE_MESSAGE)

    running = EmbeddedWindow.is_running

    def build_launch(self, target=None):
        if not self.script.is_file():
            return None, None, f"{self.script.name} not found. Put it here:\n{self.script}"
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        return ([pythonw_exe(), str(self.script)],
                {"cwd": str(SCRIPT_DIR), "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                 "stderr": subprocess.DEVNULL, "creationflags": CREATE_NEW_PROCESS_GROUP}, "")

    def _family_pids(self):
        proc = self.process
        if proc is None:
            return set()
        pids = {proc.pid}
        if psutil is not None:
            try:
                pids |= {c.pid for c in psutil.Process(proc.pid).children(recursive=True)}
            except Exception:
                pass
        return pids

    def window_pid(self, hwnd):
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def accepts(self, hwnd):
        if self.has_owner(hwnd) or self.window_class(hwnd) in ("consolewindowclass", "ime", "msctfime ui"):
            return False
        w, h = self.window_size(hwnd)
        return w >= 120 and h >= 80 and self.window_pid(hwnd) in self._family_pids()


# ============================================================
# FLOATING DEVTOOLS, TABS, PLUGIN API, MANAGED PROCESS
# ============================================================

class DevToolsWindow(QWidget):
    """Small floating DevTools panel. Drag the title bar to move, the corner to resize."""
    BAR_H = 32

    def __init__(self, parent):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)  # Tool: above main window, no taskbar
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


def add_closable_tab(tabs, widget, label):
    """Add a tab with a themed close button that emits tabCloseRequested(index)."""
    idx = tabs.addTab(widget, label)
    btn = QPushButton("\u00d7")
    btn.setObjectName("TabClose")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(18, 18)
    btn.clicked.connect(lambda _=False, w=widget: tabs.tabCloseRequested.emit(tabs.indexOf(w)))
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
        self.setCurrentIndex(add_closable_tab(self, term, title or f"Terminal {self._counter}"))
        if start:
            term.start(target)
        return term

    def terminals(self):
        return [self.widget(i) for i in range(self.count())]

    current_terminal = QTabWidget.currentWidget

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

    def any_launching(self):
        """True while a terminal has been launched but its window isn't captured yet."""
        return any(t.launcher_started and not t.hwnd for t in self.terminals())

    def shutdown_all(self):
        for t in self.terminals():
            t.shutdown()

    def start_current(self):
        t = self.currentWidget()
        if t:
            t.start()

    def restart_current(self):
        t = self.currentWidget()
        if t:
            t.restart()

    def stop_current(self, message=None):
        t = self.currentWidget()
        if t:
            t.stop(message)


class PocketTermPluginAPI:
    """Small convenience API exposed to local PocketTerm plugins.

    Plugins are ordinary Python code and are NOT sandboxed. They run with the same permissions
    as PocketTerm. Only enable plugins you trust."""

    def __init__(self, window, plugin_id):
        self._window = window
        self.plugin_id = plugin_id

    def add_action(self, name, callback, description=""):
        """Add a button to the Plugins page. callback receives no arguments."""
        if not callable(callback):
            raise TypeError("Plugin action callback must be callable")
        self._window._plugin_actions.setdefault(self.plugin_id, []).append(
            {"name": str(name)[:80], "callback": callback, "description": str(description)[:240]})
        self._window._render_plugin_actions()

    def notify(self, message, title="PocketTerm plugin"):
        QMessageBox.information(self._window, str(title)[:100], str(message)[:2000])

    def log(self, message):
        log.info("[plugin:%s] %s", self.plugin_id, str(message)[:2000])


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def new_process(parent, on_output, on_finished, on_error, cwd=None, env=None):
    """A QProcess with merged output, no console window, and the usual signals connected."""
    proc = QProcess(parent)
    if cwd:
        proc.setWorkingDirectory(str(cwd))
    if env is not None:
        pe = QProcessEnvironment()
        for k, v in env.items():
            pe.insert(k, v)
        proc.setProcessEnvironment(pe)
    proc.setProcessChannelMode(QProcess.MergedChannels)
    proc.readyReadStandardOutput.connect(on_output)
    proc.finished.connect(on_finished)
    proc.errorOccurred.connect(on_error)
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
    return proc


def append_text(widget, text, color=None):
    cursor = widget.textCursor()
    cursor.movePosition(QTextCursor.End)
    if color:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(text, fmt)
    else:
        cursor.insertText(text)
    widget.setTextCursor(cursor)
    widget.ensureCursorVisible()


def proc_running(proc):
    return proc is not None and proc.state() != QProcess.NotRunning


class ManagedProcess:
    """A hidden background process with a log box, a status line and a Start/Stop button.
    Used by PocketAi, the Autoclicker, PocketLauncher, PocketVM and the C++ Compiler tab."""

    def __init__(self, window, name, log_widget, status_label, button, on_done=None):
        self.window, self.name = window, name
        self.log_widget, self.status, self.button = log_widget, status_label, button
        self.on_done = on_done
        self.proc = None
        self.last_line = ""

    def running(self):
        return proc_running(self.proc)

    def say(self, text, ok=None):
        """ok=True green, False red, None neutral."""
        if ok is None:
            self.status.setStyleSheet("")
            self.status.setText(text)
        else:
            self.window._set_status(self.status, ok, text)

    def refresh_button(self):
        running = self.running()
        self.button.setText(f"{'Stop' if running else 'Start'} {self.name}")
        self.button.setObjectName("Danger" if running else "Primary")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)

    def start(self, program, args, cwd, env=None, message=None):
        if self.running():
            return
        self.proc = new_process(self.window, self._output, self._finished, self._error, cwd, env)
        self.last_line = ""
        self.log_widget.clear()
        self.proc.start(program, args)
        self.refresh_button()
        self.say(message or f"{self.name} is running.", None if message else True)

    def stop(self):
        if not self.running():
            return
        # Kill the whole tree so the actual program (a child of bash) stops too.
        if not kill_tree(self.proc.processId()):
            self.proc.kill()
        self.say(f"Stopping {self.name}...")

    def shutdown(self):
        """Called when PocketTerm closes: kill the whole tree without touching the UI."""
        if self.running():
            if not kill_tree(self.proc.processId()):
                self.proc.kill()

    def _output(self):
        if self.proc is None:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        data = _ANSI.sub("", data).replace("\r\n", "\n").replace("\r", "\n")
        if not data:
            return
        append_text(self.log_widget, data)
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        if lines:
            self.last_line = lines[-1]

    def _drop(self):
        proc, self.proc = self.proc, None
        if proc is not None:
            proc.deleteLater()
        self.refresh_button()
        if self.on_done:
            self.on_done()

    def _finished(self, code, _status):
        self._output()
        self._drop()
        if code == 0:
            self.say(f"{self.name} has stopped.")
        else:
            tail = f" Last output: {self.last_line}" if self.last_line else ""
            self.say(f"{self.name} stopped (exit code {code}).{tail}", False)

    def _error(self, error):
        detail = self.proc.errorString() if self.proc is not None else str(error)
        log.warning("%s process error (%s): %s", self.name, error, detail)
        if error == QProcess.FailedToStart:
            self._drop()
            self.say(f"Couldn't start {self.name}: {detail}", False)


# ============================================================
# MAIN WINDOW
# ============================================================

def source_hash(data):
    """SHA-256 of a source file, ignoring Windows/Unix line-ending differences."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def download_update(timeout=20):
    """Download the latest source from GitHub (raw bytes). Raises on any failure."""
    url = f"{UPDATE_URL}?nocache={int(time.time())}"
    request = urllib.request.Request(url, headers={"User-Agent": "PocketTerm-Updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(UPDATE_MAX_BYTES + 1)
    if len(data) > UPDATE_MAX_BYTES:
        raise ValueError("The update file is unexpectedly large.")
    return data


def validate_update(data):
    """Refuse anything that isn't a complete, syntactically valid PocketTerm script."""
    if len(data) < UPDATE_MIN_BYTES:
        raise ValueError("The downloaded file is too small to be PocketTerm.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("The downloaded file is not valid UTF-8 text.")
    if "class MainWindow" not in text or "__main__" not in text:
        raise ValueError("The downloaded file doesn't look like PocketTerm.")
    try:
        compile(text, "pocketterm_update.py", "exec")
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"The downloaded file has a syntax error: {exc}")


def install_update(data):
    """Back up the running script, then atomically replace it. Returns the backup path."""
    script = Path(__file__).resolve()
    backup_dir = APP_DIR / "Config" / "update_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{script.stem}_{time.strftime('%Y%m%d_%H%M%S')}{script.suffix}"
    shutil.copy2(script, backup)
    for old in sorted(backup_dir.glob(f"{script.stem}_*{script.suffix}"))[:-UPDATE_BACKUPS_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    tmp = script.with_name(script.name + ".update")
    tmp.write_bytes(data)
    os.replace(tmp, script)
    return backup


class MainWindow(QMainWindow):
    # Emitted from background threads, handled on the UI thread.
    remote_devices_ready = Signal(object)
    remote_result_ready = Signal(str)
    git_info_ready = Signal(int, str, object)
    task_done = Signal(str, bool, str)
    drives_ready = Signal(object)
    ports_ready = Signal(object)
    plugins_loaded = Signal(object, str)
    plugin_preview_ready = Signal(str, str)
    plugin_action_done = Signal(bool, str)
    update_checked = Signal(object, str, bool)

    def __init__(self):
        super().__init__()
        self.settings = Settings(SETTINGS_FILE)
        if self.settings.get("accent") not in ACCENT_CHOICES:
            self.settings.data["accent"] = "Violet"

        self._vscode_autostarted = False
        self._project_paths, self._script_paths = [], []
        self.git_cache = {}
        self._git_gen = 0
        # Don't run git.exe while the terminal is starting: the PortableApps launcher refuses to
        # start if another Git process is running at that moment.
        self._git_hold_until = time.time() + (8.0 if self.settings.get("auto_start") else 0.0)
        self._git_retry_pending = False
        self.script_proc = self.jar_proc = self.tray = self._user32 = None
        self._hotkey_registered = self._was_max = False
        self._edits, self._checks, self._delegates = {}, {}, []   # settings widgets / row delegates
        self._nav_base, self._nav_groups, self._nav_group_open = {}, {}, {}
        self._nav_page_group, self._nav_running = {}, {}
        self._drives_busy = self._ports_busy = False
        self._drives_sig = self._ports_cache = self._script_path = None
        self._drive_tick_count = 0
        self._low_alerted = set()
        self._skip_close_confirm = False
        self._update_busy = False
        self.web_view = self._devtools = None
        self._web_started = self._ai_checked = False
        self._ai_deadline = 0.0
        self.dashboard_labels, self.dashboard_donuts = {}, {}
        self.diag_tree = self.diag_summary = None
        self.diag_report = ""
        self.background_manager = None
        self.file_model = self.file_tree = self.file_path_edit = None
        self.plugins_tree = self.plugins_preview = self.plugins_status = None
        self.local_plugins_list = self.plugin_actions_layout = None
        self._plugin_entries, self._plugin_preview_path = {}, None
        self._plugins_busy = False
        self._loaded_plugins, self._plugin_actions = {}, {}
        self.remote_devices = {}
        self.remote_selected = None
        self.remote_token_edit = None
        self.remote_command_edit = None
        self.remote_device_combo = None
        self.remote_output = None
        self.remote_status = None
        self._script_widgets = []   # embedded .py tabs (Autoclicker, PocketLauncher, PocketVM, C++, PocketAi)
        self.ai_tabs = None
        self._ai_timer = QTimer(self)
        self._ai_timer.setInterval(1000)
        self._ai_timer.timeout.connect(self._ai_poll)

        self.setWindowTitle("PocketTerm Launcher")
        self.setAcceptDrops(True)
        self._apply_style()
        self._restore_size()

        for sig, slot in ((self.git_info_ready, self._on_git_info), (self.task_done, self._on_task_done),
                          (self.drives_ready, self._on_drives), (self.ports_ready, self._on_ports),
                          (self.plugins_loaded, self._on_plugins_loaded),
                          (self.plugin_preview_ready, self._on_plugin_preview_ready),
                          (self.plugin_action_done, self._on_plugin_action_done),
                          (self.update_checked, self._on_update_checked),
                          (self.remote_devices_ready, self._on_remote_devices_ready),
                          (self.remote_result_ready, self._on_remote_result_ready)):
            sig.connect(slot)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create the embedded widgets first; pages and sidebar reference them.
        self.terminals = TerminalTabs(self.settings)
        self.vscode = EmbeddedVSCode(self.settings)

        # The sidebar scrolls vertically so short displays don't force the window to grow.
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName("SidebarScroll")
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setFixedWidth(216)
        sidebar_scroll.setWidget(self._build_sidebar())
        main_layout.addWidget(sidebar_scroll)

        self.stacked_widget = QStackedWidget()
        builders = {  # page index -> builder (added in index order)
            PAGE_TERMINAL: self._build_terminal_page, PAGE_VSCODE: self._build_vscode_page,
            PAGE_PROJECTS: self._build_projects_page, PAGE_SCRIPTS: self._build_scripts_page,
            PAGE_SNIPPETS: self._build_snippets_page, PAGE_TOOLS: self._build_tools_page,
            PAGE_DRIVES: self._build_drives_page, PAGE_PORTS: self._build_ports_page,
            PAGE_WEB: self._build_web_page, PAGE_SETTINGS: self._build_settings_page,
            PAGE_DASHBOARD: self._build_dashboard_page, PAGE_FILES: self._build_files_page,
            PAGE_PLUGINS: self._build_plugins_page, PAGE_AUTOCLICKER: self._build_autoclicker_page,
            PAGE_DIAGNOSTICS: self._build_diagnostics_page, PAGE_JAR_LAUNCHER: self._build_jar_launcher_page,
            PAGE_POCKETLAUNCHER: self._build_pocketlauncher_page, PAGE_POCKETVM: self._build_pocketvm_page,
            PAGE_REMOTE_ACCESS: self._build_remote_access_page,
            PAGE_CPP: self._build_cpp_page,
        }
        for i in range(PAGE_COUNT):
            self.stacked_widget.addWidget(builders[i]())
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

        def every(ms, slot):
            t = QTimer(self)
            t.setInterval(ms)
            t.timeout.connect(slot)
            t.start()
            return t
        self._nav_timer = every(1000, self._update_nav_indicators)      # sidebar status dots
        self._drives_timer = every(DRIVE_REFRESH_MS, self._drives_tick)   # also ~1/min for low-space alerts
        self._ports_timer = every(5000, self._ports_tick)
        self._dashboard_timer = every(3000, self._refresh_dashboard)
        self._update_timer = every(UPDATE_INTERVAL_MS, self._auto_update_check)
        QTimer.singleShot(UPDATE_CHECK_DELAY_MS, self._auto_update_check)
        QTimer.singleShot(250, self._refresh_dashboard)
        if self.settings.get("low_space_alerts"):
            QTimer.singleShot(3000, self.refresh_drives)

        if self.settings.get("auto_start"):
            QTimer.singleShot(500, self.start_terminal)
        else:
            self.terminals.current_terminal().set_message("Auto-start is off.\nPress Start below, or use Settings.")

        last = self.settings.get("last_page")
        self.show_page(last if isinstance(last, int) and 0 <= last < PAGE_COUNT else PAGE_TERMINAL)

    # ---------- shared UI helpers ----------

    @staticmethod
    def _label(text, name=None, wrap=False):
        lab = QLabel(text)
        if name:
            lab.setObjectName(name)
        lab.setWordWrap(wrap)
        return lab

    @staticmethod
    def _make_card(title, description):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        layout.addWidget(MainWindow._label(title, "SectionTitle"))
        layout.addWidget(MainWindow._label(description, "Description", True))
        return card, layout

    @staticmethod
    def _page_header(title, description, buttons=()):
        row, left = QHBoxLayout(), QVBoxLayout()
        left.addWidget(MainWindow._label(title, "PageTitle"))
        left.addWidget(MainWindow._label(description, "Description", True))
        row.addLayout(left, stretch=1)
        for b in buttons:
            row.addWidget(b, alignment=Qt.AlignTop)
        return row

    def _page(self, title, description, buttons=(), margins=(30, 30, 30, 30), gap=0):
        """New page widget with a standard header. Returns (page, layout)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(*margins)
        layout.addLayout(self._page_header(title, description, buttons))
        if gap:
            layout.addSpacing(gap)
        return page, layout

    @staticmethod
    def _button(text, handler, object_name=None):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        if object_name:
            b.setObjectName(object_name)
        b.clicked.connect(lambda _=False: handler())
        return b

    def _button_row(self, layout, *specs):
        """Add a row of buttons: each spec is (text, handler[, object_name])."""
        row = QHBoxLayout()
        for spec in specs:
            row.addWidget(self._button(*spec))
        row.addStretch()
        layout.addLayout(row)
        return row

    def _status_label(self, layout, text="", name="Muted"):
        lab = self._label(text, name, True)
        layout.addWidget(lab)
        return lab

    @staticmethod
    def _set_status(label, ok, text):
        label.setText(text)
        label.setStyleSheet(f"color: {OK_COLOR};" if ok else f"color: {ERR_COLOR};")

    def _apply_style(self):
        self.setStyleSheet(build_style(self.settings))
        strong = get_palette(self.settings)[1]
        for d in self._delegates:
            d.set_colors(strong, on_color(strong))
        for name in ("project_list", "script_list", "snippet_list", "port_list"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.viewport().update()

    def _new_row_delegate(self, list_widget):
        d = RowDelegate(list_widget)
        strong = get_palette(self.settings)[1]
        d.set_colors(strong, on_color(strong))
        self._delegates.append(d)
        list_widget.setItemDelegate(d)
        list_widget.setMouseTracking(True)
        list_widget.setSpacing(2)

    def _list_widget(self, on_double_click):
        lw = QListWidget()
        self._new_row_delegate(lw)
        lw.itemDoubleClicked.connect(lambda _i: on_double_click())
        return lw

    # ---------- sidebar ----------

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(196)
        sidebar.setMaximumWidth(216)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 24, 16, 20)
        layout.setSpacing(5)
        layout.addWidget(self._label("PocketTerm", "Logo"))
        layout.addWidget(self._label("DEVELOPER ENVIRONMENT", "Subtitle"))
        layout.addSpacing(24)

        self.nav_buttons = {}
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        def add_nav(parent_layout, label, index):
            btn = QPushButton(label)
            btn.setObjectName("Navigation")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            if index < 10:  # only the first ten pages have Ctrl+digit shortcuts
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
        layout.addWidget(self._label("CTRL+K  COMMAND PALETTE", "Subtitle"))
        self.nav_buttons[PAGE_TERMINAL].setChecked(True)
        return sidebar

    def _group_header_text(self, group):
        is_open = self._nav_group_open.get(group, True)
        text = ("\u25be" if is_open else "\u25b8") + "  " + group.upper()
        if not is_open and any(self._nav_running.get(i) for i, g in self._nav_page_group.items() if g == group):
            text += "   \u25cf"
        return text

    def _set_group_open(self, group, is_open, save=True):
        header, body = self._nav_groups[group]
        self._nav_group_open[group] = is_open
        body.setVisible(is_open)
        header.setText(self._group_header_text(group))
        if save:
            self.settings.set("nav_collapsed", [g for g, o in self._nav_group_open.items() if not o])

    def _toggle_group(self, group):
        self._set_group_open(group, not self._nav_group_open.get(group, True))

    def _tools_running(self):
        return any(isinstance(w, EmbeddedWindow) and w.is_running()
                   for w in (self.tool_tabs.widget(i) for i in range(self.tool_tabs.count())))

    def _update_nav_indicators(self):
        self._nav_running = {
            PAGE_TERMINAL: self.terminals.any_running(), PAGE_VSCODE: self.vscode.is_running(),
            PAGE_SCRIPTS: self._script_running(), PAGE_WEB: self.ai.running(),
            PAGE_TOOLS: self._tools_running(), PAGE_AUTOCLICKER: self.ac.running(),
            PAGE_POCKETLAUNCHER: self.pl.running(), PAGE_POCKETVM: self.vm.running(),
            PAGE_CPP: self.cpp.running(),
        }
        for index, base in self._nav_base.items():
            text = base + ("   \u25cf" if self._nav_running.get(index) else "")
            if self.nav_buttons[index].text() != text:
                self.nav_buttons[index].setText(text)
        for group, (header, _body) in self._nav_groups.items():
            text = self._group_header_text(group)
            if header.text() != text:
                header.setText(text)
        self._refresh_script_buttons()

    # ---------- background worker (ProcessManager.py) ----------

    def _start_background_manager(self):
        """Start the separate no-console worker used only for extra background activities."""
        if self.background_manager is not None and self.background_manager.poll() is None:
            return
        if not PROCESS_MANAGER_FILE.is_file():
            log.warning("Background process manager not found: %s", PROCESS_MANAGER_FILE)
            return
        exe = Path(sys.executable)
        if os.name == "nt" and exe.with_name("pythonw.exe").is_file():
            exe = exe.with_name("pythonw.exe")
        try:
            self.background_manager = subprocess.Popen(
                [str(exe), str(PROCESS_MANAGER_FILE), "--worker"], cwd=str(SCRIPT_DIR),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
            log.info("Background activity manager started (PID %s).", self.background_manager.pid)
        except Exception:
            self.background_manager = None
            log.exception("Could not start the background activity manager")

    def _send_to_manager(self, request):
        proc = self.background_manager
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            log.exception("Could not send a request to the background manager")
            return False

    def run_background_activity(self, program, args=None, *, cwd=None, env=None, name=None):
        """Queue an extra non-interactive activity in ProcessManager.py (separate from the
        terminal, VS Code, PocketAi, scripts and embedded-tool launch paths)."""
        self._start_background_manager()
        ok = self._send_to_manager({
            "action": "launch", "program": str(program), "args": [str(x) for x in (args or [])],
            "cwd": str(cwd) if cwd else None, "env": {str(k): str(v) for k, v in (env or {}).items()},
            "name": str(name or Path(str(program)).name)})
        if not ok:
            log.warning("Background activity manager is not running; activity was not started.")
        return ok

    def stop_background_activity(self, name):
        """Ask the helper to stop every activity registered under this name."""
        return self._send_to_manager({"action": "stop_name", "name": str(name)})

    def _stop_background_manager(self):
        proc, self.background_manager = self.background_manager, None
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

    # ---------- dashboard ----------

    def _build_dashboard_page(self):
        """Compact, scrollable control-center layout for short and high-DPI displays."""
        scroll = QScrollArea()
        scroll.setObjectName("DashboardScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        page = QWidget()
        page.setObjectName("DashboardPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 24)
        layout.setSpacing(14)

        refresh = self._button("Refresh", self._refresh_dashboard, "CornerButton")
        layout.addLayout(self._page_header(
            "PocketTerm Control Center",
            "Your portable development workspace at a glance.",
            (refresh,)
        ))

        # System overview cards stay compact; the page itself scrolls when the
        # available window height is smaller than the complete dashboard.
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        card_specs = [
            ("CPU", "cpu", "Checking processor usage..."),
            ("Memory", "memory", "Checking memory..."),
            ("PocketTerm USB", "disk", "Checking portable drive..."),
            ("Environment", "environment", "Detecting portable tools..."),
        ]
        for i, (title, key, initial) in enumerate(card_specs):
            card = QFrame()
            card.setObjectName("Card")
            card.setMinimumHeight(118)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 13, 16, 13)
            card_layout.setSpacing(7)
            card_layout.addWidget(self._label(title, "SectionTitle"))

            value_label = self._label(initial, "Description", True)
            value_label.setMinimumHeight(0)
            value_label.setWordWrap(True)
            if key in ("cpu", "memory"):
                row = QHBoxLayout()
                row.setSpacing(12)
                self.dashboard_donuts[key] = DonutUsageWidget()
                self.dashboard_donuts[key].setFixedSize(66, 66)
                row.addWidget(self.dashboard_donuts[key], 0, Qt.AlignVCenter)
                row.addWidget(value_label, 1, Qt.AlignVCenter)
                card_layout.addLayout(row)
            else:
                card_layout.addWidget(value_label, 1)
            grid.addWidget(card, i // 2, i % 2)
            self.dashboard_labels[key] = value_label

        layout.addLayout(grid)

        quick = QFrame()
        quick.setObjectName("Card")
        quick_layout = QVBoxLayout(quick)
        quick_layout.setContentsMargins(16, 14, 16, 15)
        quick_layout.setSpacing(10)
        quick_layout.addWidget(self._label("Quick launch", "SectionTitle"))

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        actions.setColumnStretch(2, 1)
        actions.setColumnStretch(3, 1)
        quick_items = (
            ("Terminal", PAGE_TERMINAL),
            ("File Manager", PAGE_FILES),
            ("Projects", PAGE_PROJECTS),
            ("VS Code", PAGE_VSCODE),
            ("Tools", PAGE_TOOLS),
            ("Scripts", PAGE_SCRIPTS),
            ("Remote Access", PAGE_REMOTE_ACCESS),
            ("PocketVM", PAGE_POCKETVM),
        )
        for i, (label, page_index) in enumerate(quick_items):
            button = self._button(label, lambda idx=page_index: self.show_page(idx))
            button.setMinimumHeight(38)
            actions.addWidget(button, i // 4, i % 4)
        quick_layout.addLayout(actions)
        layout.addWidget(quick)

        footer = self._label(
            "Tip: use the sidebar to open any workspace, automation, network or system tool.",
            "Muted", True
        )
        layout.addWidget(footer)
        layout.addStretch(1)

        scroll.setWidget(page)
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        outer_layout.addWidget(scroll)
        return outer

    def _refresh_dashboard(self):
        if not self.dashboard_labels:
            return
        cpu_text = memory_text = "Live metrics need psutil."
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
        for key, value, ok in (("cpu", cpu_value, cpu_ok), ("memory", memory_value, memory_ok)):
            if key in self.dashboard_donuts:
                self.dashboard_donuts[key].set_value(value, ok)
        try:
            u = shutil.disk_usage(PORTABLE_ROOT)
            disk_text = (f"{PORTABLE_ROOT}\n{format_size(u.free)} free of {format_size(u.total)}"
                         f"\n{format_size(u.used)} used")
        except OSError as exc:
            disk_text = f"Portable drive unavailable: {exc}"
        self.dashboard_labels["disk"].setText(disk_text)
        tools = [("Git", find_git(self.settings), "Not found"), ("Git Bash", find_bash(self.settings), "Not found")]
        tools += [(n, shutil.which(c), "Not detected") for n, c in
                  (("Python", "python"), ("Node.js", "node"), ("Java", "java"), ("ADB", "adb"), ("Ollama", "ollama"))]
        self.dashboard_labels["environment"].setText(
            "\n".join(f"{n}: " + ("Ready" if found else missing) for n, found, missing in tools))

    # ---------- file manager ----------

    def _build_files_page(self):
        page, layout = self._page(
            "Portable File Manager",
            "Browse the USB drive and local folders. Double-click a folder to open it or a file to launch it.",
            gap=14)
        row = QHBoxLayout()
        self.file_path_edit = QLineEdit(str(PORTABLE_ROOT))
        self.file_path_edit.setPlaceholderText("Enter a folder path...")
        self.file_path_edit.returnPressed.connect(self._navigate_file_path)
        row.addWidget(self.file_path_edit, stretch=1)
        for spec in (("Go", self._navigate_file_path, "Primary"), ("Up", self._file_go_up),
                     ("USB Root", self._file_go_home), ("Open", self._open_selected_file)):
            row.addWidget(self._button(*spec))
        layout.addLayout(row)

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
        layout.addWidget(self._label("Tip: double-click a folder to browse it, or double-click a file to open it "
                                     "with its default Windows application.", "Description"))
        return page

    def _show_folder(self, path):
        self.file_tree.setRootIndex(self.file_model.index(str(path)))
        self.file_path_edit.setText(str(path))

    def _navigate_file_path(self):
        raw = self.file_path_edit.text().strip().strip('"')
        if self.file_model is None or not raw:
            return
        target = Path(os.path.expandvars(os.path.expanduser(raw)))
        try:
            target = target.resolve()
        except OSError:
            pass
        if not target.is_dir():
            QMessageBox.warning(self, "Folder not found", f"This folder does not exist:\n{target}")
            return
        self._show_folder(target)

    def _file_go_up(self):
        current = Path(self.file_path_edit.text().strip() or str(PORTABLE_ROOT))
        if current.parent != current:
            self.file_path_edit.setText(str(current.parent))
            self._navigate_file_path()

    def _file_go_home(self):
        self.file_path_edit.setText(str(PORTABLE_ROOT))
        self._navigate_file_path()

    def _file_item_activated(self, index):
        path = Path(self.file_model.filePath(index))
        self._show_folder(path) if path.is_dir() else self._open_path_in_system(path)

    def _open_selected_file(self):
        index = self.file_tree.currentIndex() if self.file_tree is not None else None
        if index is not None and index.isValid():
            self._file_item_activated(index)

    def _open_path_in_system(self, path):
        try:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                QMessageBox.warning(self, "Could not open file", f"Windows could not open:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Could not open file", str(exc))

    # ---------- plugins: online browser ----------

    def _build_plugins_page(self):
        buttons = (self._button("Refresh online", self.refresh_plugins, "CornerButton"),
                   self._button("Download selected", self.download_selected_plugin, "Primary"),
                   self._button("Clone Plugins folder", self.clone_plugins_folder, "CornerButton"))
        page, layout = self._page(
            "Plugin Library",
            "Browse the PocketTerm GitHub Plugins folder, download individual Python files, and manage "
            "trusted local plugins.", buttons, margins=(26, 24, 26, 24))
        layout.setSpacing(12)
        layout.addWidget(self._label(
            "Security: .py plugins execute Python code with your account permissions. Downloaded files are NOT "
            "run automatically. Only enable code you have inspected and trust.", "Description", True))

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        def card(title):
            frame = QFrame()
            frame.setObjectName("Card")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(12, 12, 12, 12)
            lay.addWidget(QLabel(title))
            splitter.addWidget(frame)
            return lay

        online = card("Online repository files")
        self.plugins_tree = QTreeWidget()
        self.plugins_tree.setHeaderLabels(["Name", "Type"])
        self.plugins_tree.setAlternatingRowColors(True)
        self.plugins_tree.setUniformRowHeights(True)
        self.plugins_tree.setColumnWidth(0, 230)
        self.plugins_tree.itemClicked.connect(self._plugin_item_clicked)
        online.addWidget(self.plugins_tree, 1)
        self.plugins_preview = QPlainTextEdit()
        self.plugins_preview.setReadOnly(True)
        self.plugins_preview.setPlaceholderText("Select a repository file to preview it.")
        self.plugins_preview.setMaximumHeight(170)
        self.plugins_preview.setObjectName("Log")
        online.addWidget(self.plugins_preview)

        local = card("Installed local plugins")
        self.local_plugins_list = QListWidget()
        self.local_plugins_list.setAlternatingRowColors(True)
        local.addWidget(self.local_plugins_list, 1)
        self._button_row(local, ("Scan", self.refresh_local_plugins, "CornerButton"),
                         ("Enable", self.enable_selected_plugin, "Primary"),
                         ("Disable", self.disable_selected_plugin, "CornerButton"),
                         ("Reload", self.reload_selected_plugin, "CornerButton"),
                         ("Remove", self.remove_selected_plugin, "Danger"))
        local.addWidget(self._label("Plugin actions", "SectionTitle"))
        self.plugin_actions_layout = QVBoxLayout()
        self.plugin_actions_layout.setSpacing(6)
        local.addLayout(self.plugin_actions_layout)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, 1)

        self.plugins_status = self._status_label(
            layout, "Choose Refresh online to browse GitHub, or scan your local Plugins folder.", "Description")
        self.refresh_local_plugins()
        return page

    def _plugin_say(self, text):
        if self.plugins_status:
            self.plugins_status.setText(text)

    def _http_get(self, url, timeout=20, limit=None):
        req = urllib.request.Request(url, headers={"User-Agent": "PocketTerm-Plugin-Browser",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(limit) if limit else response.read()

    def refresh_plugins(self):
        if self._plugins_busy:
            return
        self._plugins_busy = True
        self._plugin_say("Connecting to GitHub and reading the Plugins folder...")
        if self.plugins_tree:
            self.plugins_tree.clear()
        if self.plugins_preview:
            self.plugins_preview.clear()

        def work():
            try:
                payload = json.loads(self._http_get(PLUGIN_REPO_API).decode("utf-8"))
                if payload.get("truncated"):
                    raise RuntimeError("GitHub returned a truncated repository listing.")
                entries = [{"path": str(i.get("path", "")), "name": Path(str(i.get("path", ""))).name,
                            "size": int(i.get("size") or 0), "sha": i.get("sha", "")}
                           for i in payload.get("tree", [])
                           if str(i.get("path", "")).startswith("Plugins/") and i.get("type") == "blob"
                           and str(i.get("path", "")).lower().endswith(".py")]
                entries.sort(key=lambda e: e["path"].lower())
                self.plugins_loaded.emit(entries, f"Loaded {len(entries)} file(s) from the online Plugins folder.")
            except Exception as exc:
                self.plugins_loaded.emit([], f"Could not load the plugin repository: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_plugins_loaded(self, entries, message):
        self._plugins_busy = False
        self._plugin_entries = {e["path"]: e for e in entries}
        if self.plugins_tree:
            self.plugins_tree.clear()
            folders = {"": self.plugins_tree.invisibleRootItem()}
            for entry in entries:
                parts = entry["path"].split("/")
                parent_path = ""
                for folder_name in parts[:-1]:
                    folder_path = f"{parent_path}/{folder_name}".strip("/")
                    if folder_path not in folders:
                        item = QTreeWidgetItem([folder_name, "Folder"])
                        item.setData(0, Qt.UserRole, "")
                        folders[parent_path].addChild(item)
                        folders[folder_path] = item
                    parent_path = folder_path
                item = QTreeWidgetItem([parts[-1], (Path(parts[-1]).suffix.lower().lstrip(".") or "file").upper()])
                item.setData(0, Qt.UserRole, entry["path"])
                item.setToolTip(0, entry["path"])
                item.setToolTip(1, f'{entry["size"]:,} bytes')
                folders[parent_path].addChild(item)
            self.plugins_tree.expandToDepth(1)
        self._plugin_say(message)

    def _plugin_item_clicked(self, item, column=0):
        path = item.data(0, Qt.UserRole)
        if not path or path not in self._plugin_entries:
            return
        self._plugin_preview_path = path
        if self.plugins_preview:
            self.plugins_preview.setPlainText(f"Loading {path} ...")

        def work():
            try:
                data = self._http_get(PLUGIN_RAW_BASE + urllib.parse.quote(path, safe="/"), 20, 512 * 1024 + 1)
                content = ("[Preview limited to 512 KB. Download the file to inspect it locally.]"
                           if len(data) > 512 * 1024 else data.decode("utf-8", errors="replace"))
            except Exception as exc:
                content = f"Preview unavailable: {exc}"
            self.plugin_preview_ready.emit(path, content)

        threading.Thread(target=work, daemon=True).start()

    def _on_plugin_preview_ready(self, path, content):
        if path == self._plugin_preview_path and self.plugins_preview:
            self.plugins_preview.setPlainText(content)

    def download_selected_plugin(self):
        path = self._plugin_preview_path
        if self._plugins_busy:
            return
        if not path or path not in self._plugin_entries:
            QMessageBox.information(self, "Select a plugin file", "Choose a file in the repository tree first.")
            return
        self._plugins_busy = True
        self._plugin_say(f"Downloading {path}...")

        def work():
            try:
                relative = Path(*path.split("/"))  # only .py files inside the repository's Plugins folder
                if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                        or relative.parts[0] != "Plugins" or relative.suffix.lower() != ".py"):
                    raise ValueError("Only .py files inside the repository Plugins folder can be downloaded.")
                data = self._http_get(PLUGIN_RAW_BASE + urllib.parse.quote(path, safe="/"), 30)
                dest = APP_DIR / "Plugins" / Path(*relative.parts[1:])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                self.plugin_action_done.emit(True, f"Downloaded {path} to {dest}. The file was not executed.")
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
        target = APP_DIR / "Plugins"
        if target.exists() and any(target.iterdir()):
            answer = QMessageBox.question(
                self, "Merge plugin files",
                f"The destination already contains files:\n{target}\n\nThe repository files will be merged into "
                "this folder. Existing files with the same names may be replaced.")
            if answer != QMessageBox.Yes:
                return
        self._plugins_busy = True
        self._plugin_say("Cloning only the Plugins folder with Git sparse checkout...")

        def work():
            temp_repo = APP_DIR / "Temp" / "PocketTermPluginsSparse"
            try:
                shutil.rmtree(temp_repo, ignore_errors=True)
                temp_repo.parent.mkdir(parents=True, exist_ok=True)
                target.mkdir(parents=True, exist_ok=True)
                for cmd, timeout, what in (
                        ([str(git), "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                          PLUGIN_REPO_URL, str(temp_repo)], 180, "git clone"),
                        ([str(git), "-C", str(temp_repo), "sparse-checkout", "set", "Plugins"], 60,
                         "sparse-checkout")):
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                    if r.returncode:
                        raise RuntimeError((r.stderr or r.stdout or f"{what} failed").strip())
                source = temp_repo / "Plugins"
                if not source.is_dir():
                    raise RuntimeError("The repository does not contain a Plugins folder on main.")
                for src in source.rglob("*"):
                    if src.is_file() and ".git" not in src.parts:
                        dest = target / src.relative_to(source)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dest)
                self.plugin_action_done.emit(True, f"Cloned only the repository's Plugins folder into {target}. "
                                                   "Git sparse checkout kept the rest of the working tree out.")
            except Exception as exc:
                self.plugin_action_done.emit(False, f"Plugin folder clone failed: {exc}")
            finally:
                shutil.rmtree(temp_repo, ignore_errors=True)

        threading.Thread(target=work, daemon=True).start()

    def _on_plugin_action_done(self, ok, message):
        self._plugins_busy = False
        self._plugin_say(message)
        (log.info if ok else log.warning)(message)
        if ok:
            self.refresh_local_plugins()

    # ---------- auto update (GitHub) ----------

    def _auto_update_check(self):
        if self.settings.get("auto_update"):
            self.check_for_updates(manual=False)

    def check_updates_now(self):
        self.check_for_updates(manual=True)

    def check_for_updates(self, manual=False):
        if self._update_busy:
            if manual:
                QMessageBox.information(self, "Update", "An update check is already running.")
            return
        self._update_busy = True

        def work():
            try:
                data = download_update()
            except Exception as exc:
                self.update_checked.emit(None, str(exc), manual)
                return
            self.update_checked.emit(data, "", manual)

        threading.Thread(target=work, daemon=True).start()

    def _on_update_checked(self, data, error, manual):
        self._update_busy = False
        if error:
            log.warning("Update check failed: %s", error)
            if manual:
                QMessageBox.warning(self, "Update check failed", f"Could not reach GitHub:\n{error}")
            return
        try:
            validate_update(data)
            local_hash = source_hash(Path(__file__).resolve().read_bytes())
        except (ValueError, OSError) as exc:
            log.warning("Update rejected: %s", exc)
            if manual:
                QMessageBox.warning(self, "Update rejected", str(exc))
            return

        remote_hash = source_hash(data)
        if remote_hash == local_hash:
            self.settings.set("update_hash", remote_hash)
            if manual:
                QMessageBox.information(self, "Up to date", "You already have the latest version from GitHub.")
            return

        stored = self.settings.get("update_hash")
        if manual:
            answer = QMessageBox.question(
                self, "Update available",
                "A different version of PocketTerm is on GitHub.\n\nInstall it? Your current file is backed up "
                "first (Config/update_backups) and PocketTerm can restart afterwards.")
            if answer != QMessageBox.Yes:
                return
        elif not stored:
            # First run with updates enabled: remember what GitHub has now, but never overwrite local code.
            self.settings.set("update_hash", remote_hash)
            log.info("Auto update baseline recorded; waiting for the next change on GitHub.")
            return
        elif remote_hash == stored:
            return  # GitHub hasn't changed since the last update; leave any local edits alone

        self._install_update(data, remote_hash)

    def _install_update(self, data, remote_hash):
        try:
            backup = install_update(data)
        except OSError as exc:
            log.exception("Could not install the update")
            QMessageBox.warning(self, "Update failed", f"Could not install the update:\n{exc}")
            return
        self.settings.set("update_hash", remote_hash)
        log.info("Updated from GitHub. Backup: %s", backup)
        answer = QMessageBox.question(
            self, "PocketTerm updated",
            f"PocketTerm was updated from GitHub.\nThe previous version is saved at:\n{backup}\n\n"
            "Restart now to use the new version?")
        if answer == QMessageBox.Yes:
            self._restart_app()

    def _restart_app(self):
        script = Path(__file__).resolve()
        args = [pythonw_exe(), str(script)] + sys.argv[1:]
        code = f"import subprocess, time; time.sleep(4); subprocess.Popen({args!r}, cwd={str(script.parent)!r})"
        try:
            subprocess.Popen([python_exe(), "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0)
        except OSError as exc:
            QMessageBox.warning(self, "Restart failed", f"Could not restart PocketTerm:\n{exc}\nRestart it manually.")
            return
        self._skip_close_confirm = True
        self.close()

    # ---------- plugins: local manager ----------

    def _plugin_files(self):
        folder = APP_DIR / "Plugins"
        folder.mkdir(parents=True, exist_ok=True)
        return sorted((p for p in folder.rglob("*.py")
                       if p.is_file() and p.name != "__init__.py" and not p.name.startswith("_")),
                      key=lambda p: str(p).lower())

    def refresh_local_plugins(self):
        if not self.local_plugins_list:
            return
        self.local_plugins_list.clear()
        enabled = set(self.settings.get("enabled_plugins"))
        for path in self._plugin_files():
            rel = path.relative_to(APP_DIR / "Plugins").as_posix()
            state = "RUNNING" if rel in self._loaded_plugins else ("ENABLED" if rel in enabled else "DISABLED")
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

    def _set_plugin_enabled(self, rel, enabled):
        current = [x for x in self.settings.get("enabled_plugins") if x != rel]
        self.settings.set("enabled_plugins", current + [rel] if enabled else current)

    def _load_plugin(self, rel):
        rel = str(rel).replace("\\", "/")
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
            if not callable(getattr(module, "register", None)):
                raise ValueError("Plugin must define register(app)")
            api = PocketTermPluginAPI(self, rel)
            module.register(api)
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
            f"This will execute {rel} as Python code with PocketTerm's permissions. It can read or modify "
            "files and start processes. Only continue if you trust and have inspected this file.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        ok, message = self._load_plugin(rel)
        if ok:
            self._set_plugin_enabled(rel, True)
        self.refresh_local_plugins()
        self._plugin_say(message)
        if not ok:
            QMessageBox.warning(self, "Plugin error", message)

    def disable_selected_plugin(self):
        rel = self._selected_plugin_relpath()
        if not rel:
            return
        _ok, message = self._unload_plugin(rel)
        self._set_plugin_enabled(rel, False)
        self.refresh_local_plugins()
        self._plugin_say(message)

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
        self._plugin_say(message)
        if not ok:
            QMessageBox.warning(self, "Plugin reload failed", message)

    def remove_selected_plugin(self):
        rel = self._selected_plugin_relpath()
        if not rel or QMessageBox.question(
                self, "Remove plugin", f"Delete {rel} from the local Plugins folder?") != QMessageBox.Yes:
            return
        ok, message = self._unload_plugin(rel)
        if not ok:
            QMessageBox.warning(self, "Could not disable plugin", message)
            return
        self._set_plugin_enabled(rel, False)
        try:
            self._safe_plugin_path(rel).unlink()
            self._plugin_say(f"Removed {rel}")
        except Exception as exc:
            QMessageBox.warning(self, "Remove failed", str(exc))
        self.refresh_local_plugins()

    def _render_plugin_actions(self):
        lay = self.plugin_actions_layout
        if lay is None:
            return
        while lay.count():
            widget = lay.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        count = 0
        for rel, actions in list(self._plugin_actions.items()):
            for action in actions:
                button = QPushButton(f"{action['name']}  ·  {Path(rel).name}")
                button.setToolTip(action["description"] or rel)
                button.setCursor(Qt.PointingHandCursor)

                def invoke(_checked=False, cb=action["callback"], plugin=rel):
                    try:
                        cb()
                    except Exception as exc:
                        log.exception("Plugin action failed: %s", plugin)
                        QMessageBox.warning(self, "Plugin action failed", str(exc))

                button.clicked.connect(invoke)
                lay.addWidget(button)
                count += 1
        if count == 0:
            lay.addWidget(self._label("Enabled plugins can add buttons here with app.add_action(...).",
                                      "Description", True))

    # ---------- terminal / VS Code pages ----------

    def _build_terminal_page(self):
        page, layout = self._page(
            "Workspace Terminal",
            "Native standalone sandbox console environment. Use + New Tab for more sessions (Ctrl+T).", gap=20)
        self.terminals.setCornerWidget(self._button("+  New Tab", self.new_terminal_tab, "CornerButton"),
                                       Qt.TopRightCorner)
        layout.addWidget(self.terminals, stretch=1)
        return page

    def _build_vscode_page(self):
        page, layout = self._page(
            "VS Code", "VS Code running inside PocketTerm. Drop a file or folder on the window to open it.",
            (self._button("Open Folder...", self.open_folder_in_vscode),
             self._button("Start / Restart VS Code", self.restart_vscode, "Primary")), gap=20)
        frame = QFrame()
        frame.setObjectName("TerminalFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(3, 3, 3, 3)
        frame_layout.addWidget(self.vscode)
        layout.addWidget(frame, stretch=1)
        return page

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
        folder = QFileDialog.getExistingDirectory(self, "Open folder in VS Code", str(APP_DIR / "Projects"))
        if folder:
            self._record_recent(Path(folder))
            self.restart_vscode(Path(folder))

    # ---------- drag and drop ----------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [p for p in (Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()) if p.exists()]
        if not paths:
            return
        event.acceptProposedAction()
        if paths[0].is_dir():
            self._record_recent(paths[0])
        self.restart_vscode(paths[0])

    # ---------- projects ----------

    def _build_projects_page(self):
        page, layout = self._page(
            "Projects",
            "Folders inside your Projects directory. Double-click one to open it in VS Code. "
            "Pinned projects come first, then recently opened ones.",
            (self._button("New Project...", self.new_project), self._button("Clone Repo...", self.clone_repo)),
            gap=16)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText("Search projects...  (Ctrl+F)")
        self.project_search.setClearButtonEnabled(True)
        self.project_search.textChanged.connect(lambda _t: self._render_projects())
        layout.addWidget(self.project_search)
        layout.addSpacing(8)
        self.project_list = self._list_widget(self.open_project_in_vscode)
        layout.addWidget(self.project_list, stretch=1)
        self.projects_status = self._status_label(layout)
        self._button_row(layout, ("Open in VS Code", self.open_project_in_vscode, "Primary"),
                         ("Open Terminal Here", self.open_terminal_here), ("Pin / Unpin", self.toggle_pin_selected),
                         ("Show in Explorer", self.show_project_in_explorer), ("Refresh", self.refresh_projects))
        return page

    @staticmethod
    def _selected_path(list_widget):
        item = list_widget.currentItem()
        return Path(item.data(ROLE_PATH)) if item else None

    def _selected_project(self):
        path = self._selected_path(self.project_list)
        if not path:
            self.projects_status.setText("Select a project first.")
        return path

    def _record_recent(self, path):
        s = str(Path(path))
        recent = [r for r in self.settings.get("recent_projects") if r != s]
        self.settings.set("recent_projects", ([s] + recent)[:MAX_RECENT])
        self._render_projects()

    @staticmethod
    def _list_projects():
        try:
            return sorted((p for p in (APP_DIR / "Projects").iterdir() if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: p.name.lower())
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
        sub, badge, kind = info if info else ("Folder", "", "")
        item.setData(ROLE_SUB, sub)
        item.setData(ROLE_BADGE, badge)
        item.setData(ROLE_KIND, kind)

    def refresh_projects(self):
        self._project_paths = self._list_projects()
        self._render_projects()
        self._start_git_scan()

    def _render_projects(self):
        keep = self._selected_path(self.project_list)
        query = self.project_search.text().strip().lower()
        pinned, recent = list(self.settings.get("pinned_projects")), list(self.settings.get("recent_projects"))

        def sort_key(p):
            s = str(p)
            if s in pinned:
                return (0, 0, p.name.lower())
            return (1, recent.index(s), p.name.lower()) if s in recent else (2, 0, p.name.lower())

        shown = sorted((p for p in self._project_paths if query in p.name.lower()), key=sort_key)
        self.project_list.clear()
        for p in shown:
            item = QListWidgetItem()
            self._fill_project_item(item, p)
            self.project_list.addItem(item)
            if keep and p == keep:
                self.project_list.setCurrentRow(self.project_list.count() - 1)

        root, total = APP_DIR / "Projects", len(self._project_paths)
        if not total:
            self.projects_status.setText("No projects yet. Use New Project... or Clone Repo... to add one.")
        elif query:
            self.projects_status.setText(f"{len(shown)} of {total} project(s) match in {root}")
        else:
            self.projects_status.setText(f"{total} project(s) in {root}")

    def _start_git_scan(self):
        # The PortableApps launcher fails if git.exe is running while it starts, so don't scan while a
        # terminal is still launching (or during the short startup window).
        if self.terminals.any_launching() or time.time() < self._git_hold_until:
            if not self._git_retry_pending:
                self._git_retry_pending = True

                def retry():
                    self._git_retry_pending = False
                    self._start_git_scan()
                QTimer.singleShot(1500, retry)
            return
        self._git_gen += 1
        gen, git, paths = self._git_gen, find_git(self.settings), list(self._project_paths)
        current = {str(p) for p in paths}
        self.git_cache = {k: v for k, v in self.git_cache.items() if k in current} if git else {}
        if not git:
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
            if self.project_list.item(i).data(ROLE_PATH) == path:
                self._fill_project_item(self.project_list.item(i), Path(path))
                break

    def _open_project(self, path):
        self._record_recent(path)
        self.restart_vscode(path)

    def open_project_in_vscode(self):
        path = self._selected_project()
        if path:
            self._open_project(path)

    def open_terminal_here(self):
        path = self._selected_project()
        if path:
            self.open_terminal_in(path)

    def show_project_in_explorer(self):
        path = self._selected_project()
        if path:
            open_path(path)

    def toggle_pin_selected(self):
        path = self._selected_project()
        if not path:
            return
        s, pinned = str(path), list(self.settings.get("pinned_projects"))
        if s in pinned:
            pinned.remove(s)
            msg = f"Unpinned {path.name}."
        else:
            pinned.append(s)
            msg = f"Pinned {path.name}."
        self.settings.set("pinned_projects", pinned)
        self._render_projects()
        self.projects_status.setText(msg)

    def new_project(self):
        name, ok = QInputDialog.getText(self, "New project", "Project folder name:")
        name = name.strip()
        if not ok:
            return
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
        note, git = "", find_git(self.settings)
        if git and QMessageBox.question(self, "New project",
                                        "Initialize a git repository in this folder?") == QMessageBox.Yes:
            try:
                subprocess.run([str(git), "init"], cwd=str(target), capture_output=True, timeout=15,
                               creationflags=CREATE_NO_WINDOW)
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
        name, dest = repo_name_from_url(url), APP_DIR / "Projects"
        self.projects_status.setText(f"Cloning {url} ...")

        def work():
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # fail instead of hanging on a password prompt
            try:
                r = subprocess.run([str(git), "clone", url], cwd=str(dest), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", env=env, stdin=subprocess.DEVNULL,
                                   creationflags=CREATE_NO_WINDOW)
            except OSError as e:
                self.task_done.emit("clone", False, f"Clone failed: {e}")
                return
            if r.returncode == 0:
                self.task_done.emit("clone", True, name)
            else:
                lines = r.stderr.strip().splitlines()
                self.task_done.emit("clone", False, "Clone failed: " + (lines[-1] if lines else "unknown error"))

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

    def _build_scripts_page(self):
        page, layout = self._page(
            "Scripts",
            "Scripts in your Scripts folder (.bat, .cmd, .ps1, .py, .sh). Double-click to run one in its own "
            "console, or use Run with Log to see the output below.",
            (self._button("Open Scripts Folder", lambda: open_path(APP_DIR / "Scripts")),), gap=16)

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
        self.script_list = self._list_widget(self.run_selected_script)
        self.script_list.currentItemChanged.connect(self._script_selected)
        top_layout.addWidget(self.script_list, stretch=1)

        args_row = QHBoxLayout()
        args_row.addWidget(self._label("Arguments", "Muted"))
        self.script_args_edit = QLineEdit()
        self.script_args_edit.setPlaceholderText("Optional arguments for the selected script (remembered per script)")
        self.script_args_edit.editingFinished.connect(self._save_script_args)
        args_row.addWidget(self.script_args_edit, stretch=1)
        top_layout.addLayout(args_row)
        self.scripts_status = self._status_label(top_layout)
        row = self._button_row(top_layout, ("Run in Console", self.run_selected_script, "Primary"),
                               ("Run with Log", self.run_selected_script_logged), ("Stop", self.stop_script, "Danger"),
                               ("Edit in VS Code", self.edit_selected_script), ("Refresh", self.refresh_scripts))
        self.btn_run_log = row.itemAt(1).widget()
        self.btn_stop_script = row.itemAt(2).widget()
        self.btn_stop_script.setEnabled(False)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(self._label("Output", "SectionTitle"))
        header.addStretch()
        header.addWidget(self._button("Clear", lambda: self.log_view.clear()))
        bottom_layout.addLayout(header)
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

    @staticmethod
    def _list_scripts():
        try:
            return sorted((p for p in (APP_DIR / "Scripts").iterdir()
                           if p.is_file() and p.suffix.lower() in SCRIPT_EXTS), key=lambda p: p.name.lower())
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
        for p in shown:
            item = QListWidgetItem()
            self._fill_script_item(item, p)
            self.script_list.addItem(item)
            if keep and p == keep:
                self.script_list.setCurrentRow(self.script_list.count() - 1)
        root, total = APP_DIR / "Scripts", len(self._script_paths)
        if not total:
            self.scripts_status.setText(f"No scripts found. Put .bat, .cmd, .ps1, .py or .sh files in {root}")
        elif query:
            self.scripts_status.setText(f"{len(shown)} of {total} script(s) match in {root}")
        else:
            self.scripts_status.setText(f"{total} script(s) in {root}")

    def _script_selected(self, current, _previous):
        saved = self.settings.get("script_args").get(Path(current.data(ROLE_PATH)).name, "") if current else ""
        self.script_args_edit.setText(saved)

    def _save_script_args(self):
        path = self._selected_path(self.script_list)
        if not path:
            return
        args, text = dict(self.settings.get("script_args")), self.script_args_edit.text().strip()
        if text:
            args[path.name] = text
        else:
            args.pop(path.name, None)
        self.settings.set("script_args", args)
        if self.script_list.currentItem() is not None:
            self._fill_script_item(self.script_list.currentItem(), path)

    def _run_script_path(self, path, args_text=None):
        """Run a script in its own console. Uses the remembered arguments unless given."""
        if args_text is None:
            args_text = self.settings.get("script_args").get(path.name, "")
        error = run_script(path, self.settings, args_text)
        self.scripts_status.setText(error or f"Started {path.name} in a new console window.")

    def _selected_script(self):
        path = self._selected_path(self.script_list)
        if not path:
            self.scripts_status.setText("Select a script first.")
        return path

    def run_selected_script(self):
        path = self._selected_script()
        if path:
            self._save_script_args()
            self._run_script_path(path, self.script_args_edit.text())

    def _script_running(self):
        return proc_running(self.script_proc)

    def _update_script_buttons(self):
        running = self._script_running()
        self.btn_run_log.setEnabled(not running)
        self.btn_stop_script.setEnabled(running)

    def _log(self, text, color=None):
        append_text(self.log_view, text, color or LOG_COLOR)

    def run_selected_script_logged(self):
        path = self._selected_script()
        if not path:
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
        self.script_proc = new_process(self, self._script_output, self._script_finished, self._script_error,
                                       path.parent, env)
        self._log(f"\n$ {path.name} {' '.join(args)}".rstrip() + "\n", get_palette(self.settings)[2])
        self._script_path = path
        self.script_proc.start(program, argv)
        self._update_script_buttons()
        self.scripts_status.setText(f"Running {path.name} ...")

    _ERROR_LINE = re.compile(r"\b(error|traceback|exception|failed|fatal)\b", re.IGNORECASE)

    def _script_output(self):
        if self.script_proc is None:
            return
        data = bytes(self.script_proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines(keepends=True):
            self._log(line, ERR_COLOR if self._ERROR_LINE.search(line) else None)

    def _script_cleanup(self):
        proc, self.script_proc = self.script_proc, None
        if proc is not None:
            proc.deleteLater()
        self._update_script_buttons()

    def _script_finished(self, code, _status):
        self._script_output()
        self._log(f"[finished with exit code {code}]\n", OK_COLOR if code == 0 else ERR_COLOR)
        self._script_cleanup()
        self.scripts_status.setText(f"Script finished with exit code {code}.")

    def _script_error(self, error):
        detail = self.script_proc.errorString() if self.script_proc is not None else str(error)
        log.warning("Script process error (%s): %s", error, detail)
        if error == QProcess.FailedToStart:
            self._log(f"[failed to start: {detail}]\n", ERR_COLOR)
            self._script_cleanup()
            self.scripts_status.setText("Failed to start the script.")

    def stop_script(self):
        if self._script_running():
            if not kill_tree(self.script_proc.processId()):  # the whole tree, not just the first process
                self.script_proc.kill()
            self._log("[stopped]\n", WARN_COLOR)

    def edit_selected_script(self):
        path = self._selected_script()
        if path:
            self.restart_vscode(path)

    # ---------- tools ----------

    def _build_tools_page(self):
        page, layout = self._page(
            "Tools", "Run other programs inside PocketTerm. Pick one and press Launch, or add your own with "
            "Add Tool...", gap=16)
        row = QHBoxLayout()
        self.tool_combo = QComboBox()
        self.tool_combo.setMinimumWidth(220)
        row.addWidget(self.tool_combo)
        for spec in (("Launch", self.launch_tool, "Primary"), ("Add Tool...", self.add_tool),
                     ("Edit...", self.edit_tool), ("Remove", self.remove_tool, "Danger")):
            row.addWidget(self._button(*spec))
        row.addStretch()
        layout.addLayout(row)
        layout.addSpacing(12)

        self.tool_tabs = QTabWidget()
        self.tool_tabs.setObjectName("Tabs")
        self.tool_tabs.setMovable(True)
        self.tool_tabs.tabCloseRequested.connect(self.close_tool_tab)
        layout.addWidget(self.tool_tabs, stretch=1)
        self.tools_empty = self._label("No tool is running.\nPick one above and press Launch.", "Muted")
        self.tools_empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.tools_empty, stretch=1)
        self._refresh_tool_combo()
        self._update_tools_empty()
        return page

    def _tools(self):
        """Tool entries with relative USB paths resolved for this computer."""
        tools = []
        for item in self.settings.get("tools"):
            if isinstance(item, dict) and item.get("name") and item.get("path"):
                resolved = resolve_configured_path(item["path"])
                tools.append(dict(item, path=str(resolved) if resolved else str(item["path"])))
        return tools

    def _refresh_tool_combo(self, select=None):
        tools = self._tools()
        self.tool_combo.blockSignals(True)
        self.tool_combo.clear()
        self.tool_combo.addItems([str(t["name"]) for t in tools])
        if select is not None and 0 <= select < len(tools):
            self.tool_combo.setCurrentIndex(select)
        self.tool_combo.blockSignals(False)

    def _update_tools_empty(self):
        has = self.tool_tabs.count() > 0
        self.tool_tabs.setVisible(has)
        self.tools_empty.setVisible(not has)

    def launch_tool(self):
        tools, i = self._tools(), self.tool_combo.currentIndex()
        if not (0 <= i < len(tools)):
            self.tools_empty.setText("No tools yet. Use Add Tool... to add a program.")
            return
        t = tools[i]
        widget = EmbeddedTool(self.settings, str(t["name"]), t["path"], t.get("args", ""))
        self.tool_tabs.setCurrentIndex(add_closable_tab(self.tool_tabs, widget, str(t["name"])))
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
        path, _ = QFileDialog.getOpenFileName(self, "Select a program", str(APP_DIR / "Tools"),
                                              "Programs (*.exe);;All files (*)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "Add tool", "Display name:", text=Path(path).stem)
        if not ok or not name.strip():
            return
        args, ok = QInputDialog.getText(self, "Add tool", "Arguments (optional):")
        tools = self._tools()
        tools.append({"name": name.strip(), "path": os.path.normpath(path), "args": args.strip() if ok else ""})
        self.settings.set("tools", tools)
        self._refresh_tool_combo(select=len(tools) - 1)

    def edit_tool(self):
        tools, i = self._tools(), self.tool_combo.currentIndex()
        if not (0 <= i < len(tools)):
            return
        t = tools[i]
        name, ok = QInputDialog.getText(self, "Edit tool", "Display name:", text=str(t["name"]))
        if not ok or not name.strip():
            return
        args, ok = QInputDialog.getText(self, "Edit tool", "Arguments (optional):", text=str(t.get("args", "")))
        t["name"] = name.strip()
        t["args"] = args.strip() if ok else str(t.get("args", ""))
        self.settings.set("tools", tools)
        self._refresh_tool_combo(select=i)

    def remove_tool(self):
        tools, i = self._tools(), self.tool_combo.currentIndex()
        if 0 <= i < len(tools):
            del tools[i]
            self.settings.set("tools", tools)
            self._refresh_tool_combo(select=min(i, len(tools) - 1))

    # ---------- snippets ----------

    def _build_snippets_page(self):
        page, layout = self._page(
            "Snippets",
            "Saved commands for the current terminal tab. Run types the command and presses Enter; Paste only "
            "types it so you can edit it first. Snippets also appear in Ctrl+K.",
            (self._button("Add Snippet...", self.add_snippet, "Primary"),), gap=16)
        self.snippet_list = self._list_widget(lambda: self.run_selected_snippet(True))
        layout.addWidget(self.snippet_list, stretch=1)
        self.snippets_status = self._status_label(layout)
        self._button_row(layout, ("Run in Terminal", lambda: self.run_selected_snippet(True), "Primary"),
                         ("Paste (no Enter)", lambda: self.run_selected_snippet(False)),
                         ("Edit...", self.edit_snippet), ("Remove", self.remove_snippet, "Danger"))
        self._render_snippets()
        return page

    def _snippets(self):
        return [s for s in self.settings.get("snippets") if isinstance(s, dict) and s.get("name") and s.get("command")]

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
            self.snippets_status.setText("The current terminal tab isn't running. Start it on the Terminal page first.")
            return False
        self.show_page(PAGE_TERMINAL)
        ok = term.send_text(command + ("\r" if enter else ""))
        if ok:
            term.focus_soon()
        return ok

    def run_selected_snippet(self, enter):
        row, snippets = self.snippet_list.currentRow(), self._snippets()
        if 0 <= row < len(snippets):
            self._send_command(snippets[row]["command"], enter)
        else:
            self.snippets_status.setText("Select a snippet first.")

    def add_snippet(self):
        name, ok = QInputDialog.getText(self, "Add snippet", "Name:")
        if not ok or not name.strip():
            return
        command, ok = QInputDialog.getText(self, "Add snippet", "Command to type into the terminal:")
        if not ok or not command.strip():
            return
        self.settings.set("snippets", self._snippets() + [{"name": name.strip(), "command": command.strip()}])
        self._render_snippets()
        self.snippet_list.setCurrentRow(self.snippet_list.count() - 1)

    def edit_snippet(self):
        row, snippets = self.snippet_list.currentRow(), self._snippets()
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
        row, snippets = self.snippet_list.currentRow(), self._snippets()
        if 0 <= row < len(snippets):
            del snippets[row]
            self.settings.set("snippets", snippets)
            self._render_snippets()

    # ---------- ports ----------

    def _build_ports_page(self):
        page, layout = self._page(
            "Ports",
            "Programs listening on TCP ports on this PC. Handy when a dev server is stuck on port 3000. "
            "Refreshes every few seconds while this page is open.",
            (self._button("Refresh", self.refresh_ports),), gap=16)
        self.port_search = QLineEdit()
        self.port_search.setPlaceholderText("Filter by port, program name or PID...")
        self.port_search.setClearButtonEnabled(True)
        self.port_search.textChanged.connect(lambda _t: self._render_ports())
        layout.addWidget(self.port_search)
        layout.addSpacing(8)
        self.port_list = self._list_widget(self.open_port_in_browser)
        layout.addWidget(self.port_list, stretch=1)
        self.ports_status = self._status_label(layout)
        specs = [("Open in Browser", self.open_port_in_browser)]
        if WEBVIEW_OK:
            specs.insert(0, ("Preview in PocketTerm", self.preview_selected_port, "Primary"))
        else:
            specs[0] += ("Primary",)
        self._button_row(layout, *specs, ("Kill Process", self.kill_selected_port, "Danger"),
                         ("Copy Port", self.copy_selected_port))
        return page

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
        cur = self.port_list.currentItem()
        keep = (cur.data(ROLE_PORT), cur.data(ROLE_PATH)) if cur is not None else None
        query = self.port_search.text().strip().lower()
        shown = [p for p in self._ports_cache
                 if not query or query in str(p["port"]) or query in p["name"].lower() or query == str(p["pid"])]
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
            if keep == (p["port"], str(p["pid"])):
                select_row = self.port_list.count() - 1
        if select_row >= 0:
            self.port_list.setCurrentRow(select_row)
        self.port_list.setUpdatesEnabled(True)
        total = len(self._ports_cache)
        self.ports_status.setText(f"{len(shown)} of {total} listening port(s) match." if query
                                  else f"{total} listening port(s).")

    def _selected_port(self):
        it = self.port_list.currentItem()
        if it is None:
            self.ports_status.setText("Select a port first.")
            return None
        return int(it.data(ROLE_PORT)), int(it.data(ROLE_PATH)), it.data(ROLE_TITLE)

    def open_port_in_browser(self):
        sel = self._selected_port()
        if sel:
            url = f"http://localhost:{sel[0]}"
            if not open_in_firefox(self.settings, url):
                QDesktopServices.openUrl(QUrl(url))

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
        extra = "\n\nThis is PocketTerm itself." if pid == os.getpid() else ""
        answer = QMessageBox.question(
            self, "Kill process",
            f"Stop {title.split('   ', 1)[-1]} (PID {pid}) that is using port {port}?\n"
            f"Its child processes will be stopped too.{extra}")
        if answer != QMessageBox.Yes:
            return
        self.ports_status.setText(
            f"Stopped PID {pid}." if kill_tree(pid)
            else f"Couldn't stop PID {pid}. It may need administrator rights, or it already exited.")
        QTimer.singleShot(800, self.refresh_ports)

    # ---------- PocketAi web view ----------

    def _build_web_page(self):
        self.ai = EmbeddedScript(self.settings, "PocketAi", POCKETAI_FILE)
        self.ai_btn = self._button("Start PocketAi", self.ai_toggle, "Primary")
        self.ai.toggle_btn = self.ai_btn
        self._script_widgets.append(self.ai)
        page, layout = self._page(
            "PocketAi",
            f"Runs PocketAi.py from the PocketTerm folder ({SCRIPT_DIR}) and shows its window in the first tab. "
            "The Web preview tab is a general preview: type a port (like 3000) or a full address and press "
            "Enter. 'Open in Browser' uses Firefox Portable.",
            (self.ai_btn, self._button("Edit in VS Code", lambda: self._edit_script_file(POCKETAI_FILE)),
             self._button("Open in Browser", self.web_open_external)), gap=8)
        self.ai_status = self._status_label(layout, "PocketAi isn't running.")
        self.ai_tabs = QTabWidget()
        self.ai_tabs.setObjectName("Tabs")
        self.ai_tabs.addTab(self.ai, "PocketAi")
        layout.addWidget(self.ai_tabs, stretch=1)
        self._script_refresh(self.ai)

        if not WEBVIEW_OK:
            msg = self._label(
                "The built-in web view isn't available because QtWebEngine isn't installed.\n\n"
                "Install it with:   pip install PySide6-Addons\n"
                "then restart PocketTerm. Until then, 'Open in Browser' uses Firefox Portable "
                "(or your normal browser if Firefox Portable isn't found).", "Muted", True)
            msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.ai_tabs.addTab(msg, "Web preview")
            return page

        self.web_view = QWebEngineView()
        web_tab = QWidget()
        web_layout = QVBoxLayout(web_tab)
        web_layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        for text, fn in (("←", self.web_view.back), ("→", self.web_view.forward), ("↻", self.web_view.reload)):
            bar.addWidget(self._button(text, fn))
        self.web_url_edit = QLineEdit(str(self.settings.get("web_url")) or DEFAULT_WEB_URL)
        self.web_url_edit.setPlaceholderText("Port or address, e.g. 3000 or http://localhost:8080")
        self.web_url_edit.returnPressed.connect(self.web_go)
        bar.addWidget(self.web_url_edit, stretch=1)
        for spec in (("Go", self.web_go, "Primary"), ("Hard Reload", self.web_hard_reload),
                     ("DevTools", self.web_devtools)):
            bar.addWidget(self._button(*spec))
        web_layout.addLayout(bar)
        web_layout.addWidget(self.web_view, stretch=1)
        self.web_status = self._status_label(web_layout)
        self.ai_tabs.addTab(web_tab, "Web preview")
        self.web_view.loadStarted.connect(lambda: self.web_status.setText("Loading..."))
        self.web_view.loadFinished.connect(self._web_loaded)
        self.web_view.urlChanged.connect(self._web_url_changed)
        return page

    def _web_first_show(self):
        """First time the PocketAi tab is shown: start PocketAi (hidden) or load the address."""
        if self._ai_checked:
            return
        self._ai_checked = True
        if self.settings.get("pocketai_auto_start") and not self.ai.running():
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

    def _web_text(self):
        return self.web_url_edit.text() if self.web_view is not None else str(self.settings.get("web_url"))

    def web_open_external(self):
        """Open the current address in Firefox Portable (falls back to the default browser)."""
        url = normalize_url(self._web_text())
        if open_in_firefox(self.settings, url):
            self._ai_say(f"Opened {url} in Firefox Portable.", True)
        else:
            self._ai_say("Firefox Portable not found, used the default browser instead. "
                         "Set its path in Settings > PocketAi.", False)
            QDesktopServices.openUrl(QUrl(url))

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
            self.web_status.setText(f"Couldn't load {url or 'the page'}. Is the server running on that port?")

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
        self.ai_tabs.setCurrentIndex(1)
        self.web_go()

    # ---------- PocketAi (PocketAi.py, window embedded on its tab) ----------

    def _ai_say(self, text, ok=None):
        if ok is None:
            self.ai_status.setStyleSheet("")
            self.ai_status.setText(text)
        else:
            self._set_status(self.ai_status, ok, text)

    def ai_toggle(self):
        self.ai_stop() if self.ai.is_running() else self.ai_start()

    def ai_start(self):
        if self.ai.is_running():
            return
        if not POCKETAI_FILE.is_file():
            self._ai_say(f"PocketAi.py not found. Put it here:\n{POCKETAI_FILE}", False)
            self._script_refresh(self.ai)
            return
        self.ai.start()
        self._ai_say("Starting PocketAi.py...")
        self._ai_deadline = time.time() + 60
        self._ai_timer.start()
        log.info("Started PocketAi: %s", POCKETAI_FILE)

    def ai_stop(self):
        self._ai_timer.stop()
        if self.ai.is_running():
            self.ai.stop()
            self._ai_say("PocketAi stopped.")

    def _ai_poll(self):
        """Wait until something is listening on the PocketAi address, then load it in the web preview."""
        if not self.ai.is_running():
            self._ai_timer.stop()
            return
        url = QUrl(normalize_url(self._web_text()))
        host, port = (url.host() or "localhost"), url.port(80)
        try:
            socket.create_connection((host, port), timeout=0.3).close()
            ready = True
        except OSError:
            ready = False
        if ready:
            self._ai_timer.stop()
            if self.web_view is not None:
                self._ai_say(f"PocketAi is running; {host}:{port} is ready in the Web preview tab.", True)
                self.web_go()
            else:
                self._ai_say(f"PocketAi is running at {host}:{port}. Use Open in Browser to view it.", True)
        elif time.time() > self._ai_deadline:
            self._ai_timer.stop()
            self._ai_say("PocketAi.py is running. Nothing is listening on the preview port, "
                         "which is fine if it uses its own window.", True)

    # ---------- script tabs: each runs its own .py file and embeds the window it opens ----------

    def _make_script_page(self, attr, title, script_file):
        widget = EmbeddedScript(self.settings, title, script_file)
        setattr(self, attr, widget)
        btn = self._button(f"Start {title}", lambda w=widget: self._script_toggle(w), "Primary")
        widget.toggle_btn = btn
        self._script_widgets.append(widget)
        page, layout = self._page(
            title,
            f"Runs {Path(script_file).name} from the PocketTerm folder ({SCRIPT_DIR}) and shows its window here.",
            (btn, self._button("Edit in VS Code", lambda f=script_file: self._edit_script_file(f)),
             self._button("Open Folder", lambda: open_path(SCRIPT_DIR))), gap=8)
        frame = QFrame()
        frame.setObjectName("TerminalFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(3, 3, 3, 3)
        frame_layout.addWidget(widget)
        layout.addWidget(frame, stretch=1)
        self._script_refresh(widget)
        return page

    def _script_toggle(self, widget):
        widget.stop() if widget.is_running() else widget.start()

    def _script_refresh(self, widget):
        if widget.is_running():
            return
        if widget.script.is_file():
            widget.set_message(widget.IDLE_MESSAGE)
        else:
            widget.set_message(f"{widget.script.name} not found. Put it here:\n{widget.script}", False)

    def _edit_script_file(self, path):
        if Path(path).is_file():
            self.restart_vscode(Path(path))

    def _refresh_script_buttons(self):
        for w in self._script_widgets:
            btn = w.toggle_btn
            if btn is None:
                continue
            running = w.is_running()
            text = f"{'Stop' if running else 'Start'} {w.NAME}"
            wanted = "Danger" if running else "Primary"
            if btn.text() != text:
                btn.setText(text)
            if btn.objectName() != wanted:
                btn.setObjectName(wanted)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def _build_autoclicker_page(self):
        """Autoclicker stays a floating window: Autoclicker.py runs on its own and only its output is shown here."""
        self.ac_btn = self._button("Start Autoclicker", self.ac_toggle, "Primary")
        page, layout = self._page(
            "Autoclicker",
            f"Launches Autoclicker.py from the PocketTerm folder ({SCRIPT_DIR}). It opens as its own floating "
            "window; its output appears below.",
            (self.ac_btn, self._button("Edit in VS Code", self.ac_edit),
             self._button("Open Folder", lambda: open_path(SCRIPT_DIR))), gap=8)
        self.ac_status = self._status_label(layout)
        header = QHBoxLayout()
        header.addWidget(self._label("Output", "SectionTitle"))
        header.addStretch()
        header.addWidget(self._button("Clear", lambda: self.ac_log.clear()))
        layout.addLayout(header)
        self.ac_log = QPlainTextEdit()
        self.ac_log.setObjectName("Log")
        self.ac_log.setReadOnly(True)
        self.ac_log.setMaximumBlockCount(3000)
        self.ac_log.setPlaceholderText("Output from Autoclicker.py appears here.")
        layout.addWidget(self.ac_log, stretch=1)
        self.ac = ManagedProcess(self, "Autoclicker", self.ac_log, self.ac_status, self.ac_btn)
        self._ac_refresh_status()
        return page

    def _build_pocketlauncher_page(self):
        return self._make_script_page("pl", "PocketLauncher", POCKETLAUNCHER_FILE)

    def _build_pocketvm_page(self):
        return self._make_script_page("vm", "PocketVM", POCKETVM_FILE)

    def _build_cpp_page(self):
        return self._make_script_page("cpp", "C++ Compiler", CPP_COMPILER_FILE)

    def _ac_refresh_status(self):
        if self.ac.running():
            return
        if AUTOCLICKER_FILE.is_file():
            self.ac.say(f"Ready: {AUTOCLICKER_FILE}", True)
        else:
            self.ac.say(f"Autoclicker.py not found. Put it here:\n{AUTOCLICKER_FILE}", False)

    def _pl_refresh_status(self):
        self._script_refresh(self.pl)

    def _vm_refresh_status(self):
        self._script_refresh(self.vm)

    def _cpp_refresh_status(self):
        self._script_refresh(self.cpp)

    def ac_toggle(self):
        self.ac.stop() if self.ac.running() else self.ac_start()

    def ac_start(self):
        if self.ac.running():
            return
        if not AUTOCLICKER_FILE.is_file():
            self._ac_refresh_status()
            return
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.ac.start(python_exe(), ["-u", str(AUTOCLICKER_FILE)], SCRIPT_DIR, env)
        log.info("Started Autoclicker: %s", AUTOCLICKER_FILE)

    def ac_edit(self):
        self._edit_script_file(AUTOCLICKER_FILE)

    def pl_toggle(self):
        self._script_toggle(self.pl)

    def vm_toggle(self):
        self._script_toggle(self.vm)

    def cpp_toggle(self):
        self._script_toggle(self.cpp)

    # ---------- JAR launcher ----------

    def _build_jar_launcher_page(self):
        page, layout = self._page(
            "JAR Launcher", "Run Java applications without opening GitPortable Terminal. Output appears below.")
        layout.setSpacing(12)
        panel = QFrame()
        panel.setObjectName("TerminalFrame")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(18, 18, 18, 18)
        pl.setSpacing(10)
        pl.addWidget(self._label("Java application", "SectionTitle"))

        def field(caption, placeholder, browse=None, read_only=False):
            pl.addWidget(QLabel(caption))
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setReadOnly(read_only)
            if browse:
                row = QHBoxLayout()
                row.addWidget(edit, stretch=1)
                row.addWidget(self._button("Browse...", browse))
                pl.addLayout(row)
            else:
                pl.addWidget(edit)
            return edit

        self.jar_file_edit = field("JAR file", "Choose a .jar file...", self.choose_jar_file, True)
        self.jar_java_edit = field("Java executable (optional; leave blank to use PATH)",
                                   "java.exe (auto-detected from PATH)", self.choose_jar_java)
        self.jar_args_edit = field("Program arguments (optional)", 'Example: --help  or  --name "My App"')
        row = self._button_row(pl, ("▶ Run JAR", self.launch_jar_file, "Primary"), ("■ Stop", self.stop_jar_file),
                               ("Clear Output", lambda: self.jar_output.clear()))
        self.jar_run_button, self.jar_stop_button = row.itemAt(0).widget(), row.itemAt(1).widget()
        self.jar_stop_button.setEnabled(False)
        layout.addWidget(panel)

        self.jar_launch_status = self._status_label(layout, "Ready. Choose a JAR file to begin.")
        header = QHBoxLayout()
        header.addWidget(self._label("Application output", "SectionTitle"))
        header.addStretch()
        header.addWidget(QLabel("Input is sent to programs that support console input."))
        layout.addLayout(header)
        self.jar_output = QPlainTextEdit()
        self.jar_output.setObjectName("TerminalOutput")
        self.jar_output.setReadOnly(True)
        self.jar_output.setPlaceholderText("JAR output will appear here...")
        self.jar_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.jar_output, stretch=1)

        input_row = QHBoxLayout()
        self.jar_stdin_edit = QLineEdit()
        self.jar_stdin_edit.setPlaceholderText("Type input for the running Java program and press Enter...")
        self.jar_stdin_edit.returnPressed.connect(self.send_jar_input)
        self.jar_send_button = self._button("Send", self.send_jar_input)
        input_row.addWidget(self.jar_stdin_edit, stretch=1)
        input_row.addWidget(self.jar_send_button)
        layout.addLayout(input_row)
        self._jar_set_running(False)
        return page

    def choose_jar_file(self):
        current = self.jar_file_edit.text().strip()
        start_dir = str(Path(current).parent) if current and Path(current).parent.exists() else str(APP_DIR)
        path, _ = QFileDialog.getOpenFileName(self, "Choose Java JAR", start_dir,
                                              "Java archive files (*.jar);;All files (*)")
        if path:
            self.jar_file_edit.setText(path)
            self.jar_launch_status.setText(f"Selected {Path(path).name}. Press Run JAR to start it.")

    def choose_jar_java(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Java executable", str(PORTABLE_ROOT),
                                              "Java executable (java.exe);;Executable files (*.exe);;All files (*)")
        if path:
            self.jar_java_edit.setText(path)

    def _jar_set_running(self, running):
        self.jar_run_button.setEnabled(not running)
        self.jar_stop_button.setEnabled(running)
        self.jar_stdin_edit.setEnabled(running)
        self.jar_send_button.setEnabled(running)

    def _jar_append_output(self, data):
        if data:
            append_text(self.jar_output, bytes(data).decode("utf-8", errors="replace") if not isinstance(data, str)
                        else data)

    def _jar_read_output(self):
        if self.jar_proc is not None:
            self._jar_append_output(self.jar_proc.readAllStandardOutput())

    def launch_jar_file(self):
        if proc_running(self.jar_proc):
            QMessageBox.information(self, "JAR already running", "Stop the current Java process before starting another.")
            return
        raw_path = self.jar_file_edit.text().strip().strip('"')
        jar_path = Path(raw_path) if raw_path else None
        if jar_path is None or not jar_path.is_file() or jar_path.suffix.lower() != ".jar":
            QMessageBox.warning(self, "Choose a JAR file", "Select an existing .jar file first.")
            return
        java_path = self.jar_java_edit.text().strip().strip('"')
        if java_path and not Path(java_path).is_file():
            QMessageBox.warning(self, "Java executable not found", f"This Java executable does not exist:\n{java_path}")
            return
        try:
            extra_args = shlex.split(self.jar_args_edit.text().strip(), posix=(os.name != "nt"))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid arguments", f"Could not parse the program arguments:\n{exc}")
            return

        program = java_path or shutil.which("java") or ("java.exe" if os.name == "nt" else "java")
        args = ["-jar", str(jar_path)] + extra_args
        proc = new_process(self, self._jar_read_output, self._jar_process_finished, self._jar_process_error,
                           jar_path.parent)
        proc.started.connect(lambda: self.jar_launch_status.setText(f"Running {jar_path.name} inside PocketTerm."))
        self.jar_output.setPlainText(f"$ {Path(program).name} " + " ".join(args) + "\n\n")
        self.jar_proc = proc
        self._jar_set_running(True)
        self.jar_launch_status.setText(f"Starting {jar_path.name}...")
        proc.start(program, args)

    def send_jar_input(self):
        if proc_running(self.jar_proc):
            self.jar_proc.write((self.jar_stdin_edit.text() + "\n").encode("utf-8"))
            self.jar_stdin_edit.clear()

    def stop_jar_file(self):
        proc = self.jar_proc
        if proc_running(proc):
            proc.terminate()
            QTimer.singleShot(2500, lambda p=proc: p.kill() if p.state() != QProcess.NotRunning else None)
            self.jar_launch_status.setText("Stopping Java application...")

    def _jar_process_finished(self, exit_code, _exit_status):
        self._jar_read_output()
        proc, self.jar_proc = self.jar_proc, None
        if proc is not None:
            proc.deleteLater()
        self._jar_set_running(False)
        self.jar_launch_status.setText(f"Java application finished (exit code {exit_code}).")

    def _jar_process_error(self, error):
        proc = self.jar_proc
        self._jar_append_output(f"\n[Launch error] {proc.errorString() if proc is not None else error}\n")
        if error == QProcess.FailedToStart:
            self.jar_proc = None
            if proc is not None:
                proc.deleteLater()
            self._jar_set_running(False)
            self.jar_launch_status.setText("Could not start Java. Check the Java executable or PATH.")

    # ---------- PocketTerm Doctor / diagnostics ----------

    def _build_diagnostics_page(self):
        page, layout = self._page(
            "PocketTerm Doctor",
            "Inspect the portable environment, Python runtime, dependencies, paths, configuration and helper "
            "components. Checks are read-only.",
            (self._button("Run Diagnostics", self.run_diagnostics, "Primary"),
             self._button("Export Report", self.export_diagnostic_report)))
        layout.setSpacing(12)
        self.diag_summary = self._status_label(layout, "Press Run Diagnostics to inspect this PocketTerm setup.",
                                               "Status")
        self.diag_tree = QTreeWidget()
        self.diag_tree.setColumnCount(3)
        self.diag_tree.setHeaderLabels(["CHECK", "STATUS", "DETAILS"])
        self.diag_tree.setAlternatingRowColors(True)
        self.diag_tree.setRootIsDecorated(False)
        self.diag_tree.setUniformRowHeights(True)
        self.diag_tree.setColumnWidth(0, 190)
        self.diag_tree.setColumnWidth(1, 95)
        layout.addWidget(self.diag_tree, stretch=1)
        layout.addWidget(self._label("OK = available   •   WARNING = optional or unavailable component   "
                                     "•   ERROR = a core component needs attention", "Description", True))
        return page

    def run_diagnostics(self):
        """Run local, read-only checks and populate the diagnostics page."""
        results = []

        def add(name, status, details):
            results.append({"name": str(name), "status": status, "details": str(details)})

        def exists(name, ok, detail, missing_status="ERROR", missing=None):
            add(name, "OK" if ok else missing_status, detail if ok else (missing or f"Not found: {detail}"))

        add("Python runtime", "OK", f"{sys.version.split()[0]} • {Path(sys.executable)}")
        add("Operating system", "OK",
            f"{platform.system()} {platform.release()} • {platform.machine() or 'Unknown architecture'}")
        try:
            if psutil is not None:
                mem = psutil.virtual_memory()
                add("Available memory", "OK", f"{format_size(mem.available)} available of {format_size(mem.total)}")
            else:
                add("Available memory", "WARNING", "psutil is not installed; memory details are unavailable.")
        except Exception as exc:
            add("Available memory", "WARNING", f"Could not read memory information: {exc}")

        for label, path in (("Portable root", PORTABLE_ROOT), ("PocketTerm scripts folder", SCRIPT_DIR),
                            ("PocketTerm data folder", APP_DIR), ("Tools folder", APP_DIR / "Tools"),
                            ("Projects folder", APP_DIR / "Projects"), ("Plugins folder", APP_DIR / "Plugins"),
                            ("Images folder", APP_DIR / "Images")):
            exists(label, Path(path).is_dir(), str(path), missing=f"Folder not found: {path}")
        exists("Main application file", Path(__file__).is_file(), str(Path(__file__).resolve()))

        launcher, bash, git = find_launcher(self.settings), find_bash(self.settings), find_git(self.settings)
        firefox = find_firefox(self.settings)
        exists("Git launcher", bool(launcher and launcher.is_file()), str(launcher), "WARNING",
               "No launcher path could be determined.")
        exists("Git Bash", bool(bash and bash.is_file()), str(bash),
               missing="bash.exe was not found in the portable environment or PATH.")
        exists("Git executable", bool(git and git.is_file()), str(git), "WARNING",
               "git.exe was not found. Git-dependent features may be unavailable.")
        exists("Firefox Portable", bool(firefox and firefox.is_file()), str(firefox), "WARNING",
               "FirefoxPortable.exe was not found. 'Open in Browser' falls back to the default browser.")
        for label, path in (("Process manager helper", PROCESS_MANAGER_FILE), ("Autoclicker helper", AUTOCLICKER_FILE),
                            ("PocketLauncher helper", POCKETLAUNCHER_FILE), ("PocketVM helper", POCKETVM_FILE), ("C++ Compiler helper", CPP_COMPILER_FILE),
                            ("PocketAi helper", POCKETAI_FILE)):
            exists(label, Path(path).is_file(), str(path), "WARNING")

        for module_name, required in (("PySide6", True), ("psutil", False), ("PySide6.QtWebEngineWidgets", False)):
            try:
                available = importlib.util.find_spec(module_name) is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                available = False
            add(f"Dependency: {module_name}", "OK" if available else ("ERROR" if required else "WARNING"),
                "Module is available." if available else
                ("Required module is missing." if required else "Optional module is not installed."))

        # Settings integrity: read only, never repaired during a scan.
        if SETTINGS_FILE.is_file():
            try:
                ok = isinstance(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")), dict)
                add("Settings file", "OK" if ok else "ERROR",
                    f"Valid JSON • {SETTINGS_FILE}" if ok else "Settings JSON root must be an object.")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                add("Settings file", "ERROR", f"Could not parse settings.json: {exc}")
        else:
            add("Settings file", "WARNING", f"Not created yet: {SETTINGS_FILE}")
        backup = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".bak")
        exists("Settings backup", backup.is_file(), str(backup), "WARNING", "No settings backup exists yet.")

        if LOG_FILE.exists():
            add("PocketTerm log", "OK", f"Log exists • {format_size(LOG_FILE.stat().st_size)}")
        else:
            writable = LOG_FILE.parent.is_dir() and os.access(LOG_FILE.parent, os.W_OK)
            add("PocketTerm log", "WARNING" if writable else "ERROR",
                "Log has not been created yet." if writable else
                f"Log folder is missing or not writable: {LOG_FILE.parent}")
        try:
            usage = shutil.disk_usage(str(PORTABLE_ROOT))
            free_gb = usage.free / 1024 ** 3
            add("Portable drive space", "WARNING" if free_gb < 1 else "OK",
                f"{free_gb:.2f} GB free of {usage.total / 1024 ** 3:.2f} GB")
        except OSError as exc:
            add("Portable drive space", "WARNING", f"Could not read drive space: {exc}")

        bg = self.background_manager is not None and self.background_manager.poll() is None
        for label, running, detail in (
                ("Terminal process", self.terminals.any_running(), "Git Bash terminal session"),
                ("VS Code process", self.vscode.is_running(), "Embedded VS Code"),
                ("PocketAi process", self.ai.running(), "PocketAi service"),
                ("Autoclicker process", self.ac.running(), "Autoclicker helper"),
                ("PocketLauncher process", self.pl.running(), "PocketLauncher helper"),
                ("PocketVM process", self.vm.running(), "PocketVM helper"),
                ("C++ Compiler process", self.cpp.running(), "CppRunner.py"),
                ("Background manager", bg, "PocketTerm background manager")):
            add(label, "OK", f"{'Running' if running else 'Stopped'} • {detail}")

        colors = {"OK": OK_COLOR, "WARNING": WARN_COLOR}
        self.diag_tree.clear()
        for item in results:
            row = QTreeWidgetItem([item["name"], item["status"], item["details"]])
            row.setForeground(1, QColor(colors.get(item["status"], ERR_COLOR)))
            self.diag_tree.addTopLevelItem(row)
        self.diag_tree.resizeColumnToContents(0)
        self.diag_tree.resizeColumnToContents(1)

        count = lambda s: sum(i["status"] == s for i in results)
        passed, warnings, errors = count("OK"), count("WARNING"), count("ERROR")
        self.diag_summary.setText(
            f"Diagnostics complete  •  {passed} OK  •  {warnings} warnings  •  {errors} errors")
        self.diag_summary.setStyleSheet(
            f"color: {ERR_COLOR if errors else WARN_COLOR if warnings else OK_COLOR};")
        self.diag_report = "\n".join(
            ["POCKETTERM DOCTOR — DIAGNOSTIC REPORT", f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Application: {Path(__file__).resolve()}", f"Portable root: {PORTABLE_ROOT}", ""]
            + [f"[{i['status']}] {i['name']}: {i['details']}" for i in results]
            + ["", f"SUMMARY: {passed} OK, {warnings} warnings, {errors} errors", "",
               "This report contains local system paths and environment details. Review it before sharing."])
        log.info("Diagnostics completed: %s OK, %s warnings, %s errors", passed, warnings, errors)

    def export_diagnostic_report(self):
        if not self.diag_report:
            self.run_diagnostics()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export diagnostic report", str(APP_DIR / "Config" / "pocketterm-diagnostics.txt"),
            "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(self.diag_report, encoding="utf-8")
            QMessageBox.information(self, "Report exported", f"Diagnostic report saved to:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", f"Could not save the report:\n{exc}")

    # ---------- drives ----------

    def _build_drives_page(self):
        page, layout = self._page(
            "Drives", "Free space on every drive in this PC. Updates automatically while this page is open.",
            (self._button("Refresh", self.refresh_drives),), gap=16)
        self.drives_summary = self._label("Scanning drives...", "Muted")
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
        self.drives_status = self._status_label(layout)
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

    def _notify(self, title, text):
        """Show a tray balloon (briefly showing the tray icon if it is normally hidden)."""
        if not self.tray:
            return
        was_visible = self.tray.isVisible()
        self.tray.setVisible(True)
        QTimer.singleShot(500, lambda: self.tray.showMessage(title, text, QSystemTrayIcon.Warning, 10000))
        if not was_visible:
            QTimer.singleShot(16000, self._apply_tray)

    def _check_low_space(self, drives):
        if not self.settings.get("low_space_alerts"):
            self._low_alerted.clear()
            return
        limit = int(self.settings.get("low_space_gb")) * 1024 ** 3
        low = [d for d in drives if d["free"] < limit]
        self._low_alerted &= {d["root"] for d in low}  # forget drives that have recovered
        for d in low:
            if d["root"] not in self._low_alerted:
                self._low_alerted.add(d["root"])
                self._notify("Low disk space", f"{d['root']} has only {format_size(d['free'])} free.")
        if low:
            self._set_status(self.drives_status, False, "Low space: " + "; ".join(
                f"{d['root']} has only {format_size(d['free'])} free" for d in low) + ".")
        elif self.drives_status.text().startswith("Low space"):
            self.drives_status.setText("")

    def _stop_things_on(self, drive):
        """Stop anything PocketTerm is running from the given drive so it can be ejected."""
        msg = "Stopped so the drive can be ejected."
        if drive_of(find_launcher(self.settings)) == drive:
            for t in self.terminals.terminals():
                if t.is_running():
                    t.stop(msg)
        vs_paths = [find_bash(self.settings), resolve_configured_path(self.settings.get("vscode_exe"))]
        if self.vscode.is_running() and any(p and drive_of(p) == drive for p in vs_paths):
            self.vscode.stop(msg)
        for i in range(self.tool_tabs.count() - 1, -1, -1):
            w = self.tool_tabs.widget(i)
            if isinstance(w, EmbeddedTool) and drive_of(w.exe) == drive:
                self.close_tool_tab(i)
        if self._script_running() and self._script_path and drive_of(self._script_path) == drive:
            self.stop_script()
        if self.ac.running() and drive_of(AUTOCLICKER_FILE) == drive:
            self.ac.stop()
        if self.pl.running() and drive_of(POCKETLAUNCHER_FILE) == drive:
            self.pl.stop()
        if self.ai.running() and drive_of(POCKETAI_FILE) == drive:
            self.ai_stop()
        if self.vm.running() and drive_of(POCKETVM_FILE) == drive:
            self.vm.stop()
        if self.cpp.running() and drive_of(CPP_COMPILER_FILE) == drive:
            self.cpp.stop()

    def eject_drive(self, root):
        drive = drive_of(root)
        if not drive:
            return
        if drive_of(APP_DIR) == drive:
            if QMessageBox.question(self, "Eject drive", f"PocketTerm is running from {drive}.\n\n"
                                    f"It will stop everything, close, and then eject {drive}. Continue?"
                                    ) != QMessageBox.Yes:
                return
            error = schedule_eject_after_exit(drive)
            if error:
                self._set_status(self.drives_status, False, error)
                return
            self._skip_close_confirm = True
            self.close()
            return
        if QMessageBox.question(self, "Eject drive", f"Eject {drive}?\n\nAnything running from it inside "
                                "PocketTerm will be stopped first.") != QMessageBox.Yes:
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

        def work():  # off the UI thread: an unreachable network drive can block for a while
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
        limit = int(self.settings.get("low_space_gb")) * 1024 ** 3 if self.settings.get("low_space_alerts") else 0
        sig = (limit,) + tuple((d["root"], d["free"], d["total"]) for d in drives)
        if sig == self._drives_sig:
            return  # nothing changed, don't rebuild the cards
        self._drives_sig = sig
        while self.drives_layout.count() > 1:  # the last item is the stretch
            w = self.drives_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not drives:
            self.drives_summary.setText("No drives found.")
            return
        self.drives_summary.setText(
            f"{len(drives)} drive(s)  ·  {format_size(sum(d['free'] for d in drives))} free in total")
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
        top.addWidget(self._label(f"{d['root']}   {d['name']}".rstrip(), "SectionTitle"))
        top.addStretch()
        top.addWidget(self._label("  ·  ".join(x for x in (d["kind"], d["fs"]) if x), "Muted"))
        top.addWidget(self._button("Open", lambda r=d["root"]: open_path(r)))
        letter, system_drive = drive_of(d["root"]), os.environ.get("SystemDrive", "C:").upper()
        if os.name == "nt" and d["kind"] in ("Removable", "Local disk") and letter and letter != system_drive:
            top.addWidget(self._button("Eject", lambda r=d["root"]: self.eject_drive(r)))
        lay.addLayout(top)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(12)
        bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; border-radius: 5px; }}")
        lay.addWidget(bar)

        bottom = QHBoxLayout()
        free_label = QLabel(f"{format_size(d['free'])} free of {format_size(d['total'])}" + ("  ·  low space" if low else ""))
        free_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        bottom.addWidget(free_label)
        bottom.addStretch()
        bottom.addWidget(self._label(f"{pct}% used", "Muted"))
        lay.addLayout(bottom)
        return card

    # ---------- settings page ----------

    def _build_settings_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(30, 30, 30, 0)
        page_layout.addLayout(self._page_header("Control Settings",
                                                "Changes are saved automatically to Config/settings.json."))
        page_layout.addSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 12, 30)
        cl.setSpacing(16)

        # ----- Terminal -----
        card, lay = self._make_card("Terminal", "Choose which Git Bash launcher to use and control the session.")
        self._path_row(lay, "launcher_path", "Leave empty to auto-detect GitBashPortable.exe",
                       "Select Git Bash launcher")
        self.launcher_status = self._status_label(lay)
        self._check_row(lay, "auto_start", "Start the terminal automatically when PocketTerm opens")
        self._check_row(lay, "terminal_direct_launch",
                        "Always start Git Bash directly (skips the PortableApps launcher, which can say "
                        "\"Another instance of Git is already running\")")
        self._button_row(lay, ("Start / Restart Terminal", self.restart_terminal, "Primary"),
                         ("Stop Terminal", lambda: self.terminals.stop_current("Terminal stopped."), "Danger"))
        cl.addWidget(card)

        # ----- VS Code -----
        card, lay = self._make_card(
            "VS Code", "By default VS Code is started by running your alias through Git Bash "
            "(bash -l -i -c \"code ...\"). If that doesn't work for your setup, set the VS Code executable "
            "below to launch it directly.")
        lay.addWidget(self._labeled("Alias / command"))
        self._text_row(lay, "vscode_command", "code")
        lay.addWidget(self._labeled("Extra arguments (e.g. --disable-gpu if it renders badly)"))
        self._text_row(lay, "vscode_args", "--new-window")
        lay.addWidget(self._labeled("Git Bash executable (bash.exe) - leave empty to auto-detect"))
        self._path_row(lay, "bash_path", "Auto-detected from your GitPortable folder", "Select bash.exe")
        lay.addWidget(self._labeled("VS Code executable (optional, bypasses the alias)"))
        self._path_row(lay, "vscode_exe", "e.g. Tools/VSCode/Code.exe (relative to USB root)", "Select Code.exe")
        self.vscode_status = self._status_label(lay)
        self._check_row(lay, "vscode_auto_start", "Start VS Code automatically the first time I open its tab")
        self._button_row(lay, ("Start / Restart VS Code", self.restart_vscode, "Primary"),
                         ("Stop VS Code", lambda: self.vscode.stop("VS Code stopped."), "Danger"))
        cl.addWidget(card)

        # ----- Git -----
        card, lay = self._make_card("Git", "Used for the branch info in Projects, Clone Repo and git init. "
                                    "Leave empty to auto-detect it from your GitPortable folder.")
        self._path_row(lay, "git_path", "Auto-detected", "Select git.exe")
        self.git_status = self._status_label(lay)
        cl.addWidget(card)

        # ----- PocketAi -----
        card, lay = self._make_card(
            "PocketAi", "PocketAi.py (in the PocketTerm folder) is started in the background with Python and "
            "opens its own chat window. The web view on the PocketAi tab is a general preview for local "
            "ports.")
        lay.addWidget(self._labeled("Firefox Portable for 'Open in Browser' (optional, auto-detected if empty)"))
        self._path_row(lay, "firefox_path",
                       "Auto-detected: ...\\PocketLauncher\\FirefoxPortable\\FirefoxPortable.exe",
                       "Select FirefoxPortable.exe")
        self.firefox_status = self._status_label(lay)
        self._check_row(lay, "pocketai_auto_start", "Start PocketAi automatically the first time I open its tab")
        self._button_row(lay, ("Start / Stop PocketAi", self.ai_toggle, "Primary"))
        cl.addWidget(card)

        # ----- Appearance -----
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
        self._button_row(lay, ("Reset window size", self._reset_size))
        icon_status = self._status_label(lay, ICON_STATUS)
        icon_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(card)

        # ----- Behavior -----
        card, lay = self._make_card("Behavior", "System tray, global hotkey and close confirmation.")
        self._check_row(lay, "minimize_to_tray", "Minimize to the system tray instead of the taskbar",
                        after=self._apply_tray)
        self._check_row(lay, "hotkey_enabled", f"Global hotkey {HOTKEY_TEXT} shows / hides PocketTerm",
                        after=self._register_hotkey)
        self.hotkey_status = self._status_label(lay)
        self._check_row(lay, "confirm_close", "Ask for confirmation when closing while the terminal, VS Code, "
                        "a tool or the autoclicker is running")
        self._check_row(lay, "auto_update", "Automatically download updates from GitHub (a backup is kept)")
        self._button_row(lay, ("Check for updates now", self.check_updates_now))
        cl.addWidget(card)

        # ----- Folders -----
        card, lay = self._make_card("Folders", "Open PocketTerm's working folders in File Explorer.")
        self._button_row(lay, *[(n, lambda n=n: open_path(APP_DIR / n))
                                for n in ("Projects", "Scripts", "Tools", "Config", "Images", "Temp")])
        cl.addWidget(card)

        # ----- Backup -----
        card, lay = self._make_card("Backup", "Export your settings to a file, or import them on another "
                                    "machine or drive.")
        self._button_row(lay, ("Export settings...", self.export_settings), ("Import settings...", self.import_settings))
        self.backup_status = self._status_label(lay)
        cl.addWidget(card)

        cl.addStretch()
        scroll.setWidget(content)
        page_layout.addWidget(scroll, stretch=1)
        self._refresh_statuses()
        return page

    @staticmethod
    def _labeled(text):
        return MainWindow._label(text, "Muted")

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
            start_dir = str(Path(current).parent) if current and Path(current).parent.exists() else str(APP_DIR)
            path, _ = QFileDialog.getOpenFileName(self, dialog_title, start_dir, "Executables (*.exe);;All files (*)")
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
        launcher = find_launcher(self.settings)
        label = "Using" if str(self.settings.get("launcher_path")).strip() else "Auto-detected"
        self._set_status(self.launcher_status, launcher.is_file(),
                         f"{label}: {launcher}" if launcher.is_file() else f"Not found: {launcher}")
        args, _kwargs, info = vscode_launch_plan(self.settings)
        self._set_status(self.vscode_status, args is not None, info)
        git = find_git(self.settings)
        if git:
            custom = bool(str(self.settings.get("git_path")).strip())
            self._set_status(self.git_status, True, f"{'Using' if custom else 'Auto-detected'}: {git}")
        else:
            self._set_status(self.git_status, False, "Git not found. Branch info, Clone Repo and git init are "
                             "disabled. Set its path above.")
        firefox = find_firefox(self.settings)
        if firefox:
            custom = bool(str(self.settings.get("firefox_path")).strip())
            self._set_status(self.firefox_status, True, f"{'Using' if custom else 'Auto-detected'}: {firefox}")
        else:
            self._set_status(self.firefox_status, False,
                             "Firefox Portable not found. 'Open in Browser' will use the default browser. "
                             "Set its path above.")

    # ---------- keyboard shortcuts and command palette ----------

    def _setup_shortcuts(self):
        # These work while the PocketTerm UI has focus; inside an embedded program that program gets the keys.
        # Ctrl+1 .. Ctrl+9 and Ctrl+0 open the first ten pages.
        for i in range(min(PAGE_COUNT, 10)):
            QShortcut(QKeySequence(f"Ctrl+{(i + 1) % 10}"), self).activated.connect(lambda i=i: self.show_page(i))
        for key, slot in (("Ctrl+T", self.new_terminal_tab), ("Ctrl+F", self._focus_search), ("Ctrl+K", self.open_palette)):
            QShortcut(QKeySequence(key), self).activated.connect(slot)

    def _focus_search(self):
        box = {PAGE_PROJECTS: getattr(self, "project_search", None),
               PAGE_SCRIPTS: getattr(self, "script_search", None)}.get(self.stacked_widget.currentIndex())
        if box is not None:
            box.setFocus()
            box.selectAll()

    def open_palette(self):
        commands = [(f"Go to: {label}", lambda i=index: self.show_page(i)) for index, label in self._nav_base.items()]
        commands += [("Terminal: New tab", self.new_terminal_tab), ("VS Code: Open folder...", self.open_folder_in_vscode),
                     ("VS Code: Start / restart", self.restart_vscode), ("Autoclicker: Start / Stop", self.ac_toggle),
                     ("PocketLauncher: Start / Stop", self.pl_toggle), ("PocketVM: Start / Stop", self.vm_toggle),
                     ("C++ Compiler: Start / Stop", self.cpp_toggle),
                     ("Update: Check for updates now", self.check_updates_now)]
        for p in self._list_projects():
            commands.append((f"Project: Open in VS Code - {p.name}", lambda p=p: self._open_project(p)))
            commands.append((f"Project: Terminal here - {p.name}", lambda p=p: self.open_terminal_in(p)))
        for p in self._list_scripts():
            commands.append((f"Script: Run - {p.name}", lambda p=p: self._run_script_path(p)))
            commands.append((f"Script: Edit in VS Code - {p.name}", lambda p=p: self.restart_vscode(p)))
        for s in self._snippets():
            commands.append((f"Snippet: Run - {s['name']}", lambda c=s["command"]: self._send_command(c, True)))
        CommandPalette(self, commands).open_centered()


    # ---------- LAN remote access ----------
    REMOTE_DISCOVERY_PORT = 47630
    REMOTE_COMMAND_PORT = 47631

    def _build_remote_access_page(self):
        page, layout = self._page(
            "Remote Access",
            "Find explicitly installed PocketTerm receiver clients on your local network and send commands "
            "after the receiver owner approves each request.",
            margins=(30, 30, 30, 30), gap=18
        )

        card, body = self._make_card(
            "LAN receivers",
            "Both computers must be on the same local network. Start PocketTermRemoteReceiver.py on the "
            "other PC, then scan for its announcement."
        )
        row = QHBoxLayout()
        self.remote_device_combo = QComboBox()
        self.remote_device_combo.setMinimumWidth(280)
        self.remote_device_combo.addItem("No receivers discovered")
        row.addWidget(self.remote_device_combo, stretch=1)
        row.addWidget(self._button("Scan LAN", self.scan_remote_receivers, "Primary"))
        body.addLayout(row)

        auth_row = QHBoxLayout()
        auth_row.addWidget(self._label("Receiver pairing token"))
        self.remote_token_edit = QLineEdit()
        self.remote_token_edit.setEchoMode(QLineEdit.Password)
        self.remote_token_edit.setPlaceholderText("Enter the token shown on the receiver PC")
        auth_row.addWidget(self.remote_token_edit, stretch=1)
        body.addLayout(auth_row)
        self.remote_status = self._status_label(body, "Not connected. Receiver approval is required for every command.")
        layout.addWidget(card)

        command_card, command_layout = self._make_card(
            "PowerShell command",
            "Only send commands to computers you own or have permission to administer. The receiver displays "
            "the exact command locally and requires approval before running it."
        )
        self.remote_command_edit = QPlainTextEdit()
        self.remote_command_edit.setObjectName("Log")
        self.remote_command_edit.setPlaceholderText("Get-ComputerInfo | Select-Object CsName, WindowsVersion")
        self.remote_command_edit.setFixedHeight(105)
        command_layout.addWidget(self.remote_command_edit)
        controls = QHBoxLayout()
        controls.addWidget(self._button("Send for approval", self.send_remote_command, "Primary"))
        controls.addWidget(self._button("Clear", self.remote_command_edit.clear))
        controls.addStretch()
        command_layout.addLayout(controls)
        layout.addWidget(command_card)

        output_card, output_layout = self._make_card("Receiver response", "Command output or connection status appears here.")
        self.remote_output = QPlainTextEdit()
        self.remote_output.setObjectName("Log")
        self.remote_output.setReadOnly(True)
        self.remote_output.setMinimumHeight(180)
        output_layout.addWidget(self.remote_output)
        layout.addWidget(output_card, stretch=1)
        return page

    def scan_remote_receivers(self):
        self._set_status(self.remote_status, True, "Scanning the local network for receiver announcements...")
        self.remote_output.appendPlainText("[Discovery] Scanning LAN...")
        def worker():
            found = {}
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", self.REMOTE_DISCOVERY_PORT))
                sock.settimeout(2.5)
                end = time.time() + 2.5
                while time.time() < end:
                    try:
                        packet, address = sock.recvfrom(2048)
                        data = json.loads(packet.decode("utf-8", errors="replace"))
                        if data.get("type") == "pocketterm_receiver":
                            name = str(data.get("name") or "PocketTerm Receiver")[:80]
                            ip = address[0]
                            found[f"{name} ({ip})"] = {"name": name, "ip": ip}
                    except socket.timeout:
                        break
                    except (ValueError, OSError):
                        continue
            except OSError as exc:
                self.remote_result_ready.emit(f"Discovery error: {exc}")
            finally:
                sock.close()
            self.remote_devices_ready.emit(found)
        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_devices_ready(self, found):
        self.remote_devices = found
        self.remote_device_combo.clear()
        if not found:
            self.remote_device_combo.addItem("No receivers found")
            self._set_status(self.remote_status, False,
                             "No receiver found. Check both PCs are on the same LAN and the receiver is running.")
            return
        for label, item in found.items():
            self.remote_device_combo.addItem(label, item["ip"])
        self._set_status(self.remote_status, True, f"Found {len(found)} receiver(s). Select one and enter its token.")

    def send_remote_command(self):
        ip = self.remote_device_combo.currentData()
        token = self.remote_token_edit.text().strip()
        command = self.remote_command_edit.toPlainText().strip()
        if not ip:
            QMessageBox.warning(self, "No receiver selected", "Scan the LAN and select a receiver first.")
            return
        if not token:
            QMessageBox.warning(self, "Pairing token required", "Enter the pairing token shown on the receiver PC.")
            return
        if not command:
            QMessageBox.warning(self, "Command required", "Enter a PowerShell command to send.")
            return
        if len(command) > 8000:
            QMessageBox.warning(self, "Command too long", "Commands are limited to 8,000 characters.")
            return

        self._set_status(self.remote_status, True, f"Request sent to {ip}; waiting for local approval...")
        self.remote_output.appendPlainText(f"\n> {command}\n[Waiting for receiver approval...]")
        def worker():
            try:
                with socket.create_connection((ip, self.REMOTE_COMMAND_PORT), timeout=8) as conn:
                    conn.settimeout(125)
                    challenge_data = bytearray()
                    while b"\n" not in challenge_data and len(challenge_data) < 2048:
                        part = conn.recv(1024)
                        if not part:
                            raise ConnectionError("Receiver closed before authentication.")
                        challenge_data.extend(part)
                    challenge_msg = json.loads(bytes(challenge_data).split(b"\n", 1)[0].decode("utf-8"))
                    challenge = challenge_msg.get("challenge", "")
                    proof = hmac.new(token.encode("utf-8"), challenge.encode("ascii"), hashlib.sha256).hexdigest()
                    payload = json.dumps({"proof": proof, "command": command}).encode("utf-8") + b"\n"
                    conn.sendall(payload)
                    chunks = []
                    total = 0
                    while True:
                        part = conn.recv(4096)
                        if not part:
                            break
                        chunks.append(part)
                        total += len(part)
                        if total > 1_000_000:
                            raise ValueError("Receiver response exceeded 1 MB.")
                response = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
                text_out = response.get("output", "")
                if response.get("approved"):
                    result = f"[Approved • exit code {response.get('exit_code', '?')}]\n{text_out}"
                else:
                    result = f"[Not run • {response.get('reason', 'Request declined')}]\n{text_out}"
            except Exception as exc:
                result = f"[Remote connection failed] {exc}"
            self.remote_result_ready.emit(result)
        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_result_ready(self, result):
        self.remote_output.appendPlainText(result)
        ok = not result.startswith("[Remote connection failed]")
        self._set_status(self.remote_status, ok, "Request completed." if ok else "Could not complete the request.")

    # ---------- navigation / focus ----------

    def _embedded_for(self, index):
        if index == PAGE_TERMINAL:
            return self.terminals.current_terminal()
        if index == PAGE_VSCODE:
            return self.vscode
        scripts = {PAGE_POCKETLAUNCHER: self.pl, PAGE_POCKETVM: self.vm, PAGE_CPP: self.cpp}
        if index in scripts:
            return scripts[index]
        if index == PAGE_WEB and self.ai_tabs is not None and self.ai_tabs.currentIndex() == 0:
            return self.ai
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
        group = self._nav_page_group.get(index)  # expand the sidebar group that holds this page
        if group and not self._nav_group_open.get(group, True):
            self._set_group_open(group, True)
        self.stacked_widget.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if self.settings.get("last_page") != index:
            self.settings.set("last_page", index)

        on_show = {PAGE_PROJECTS: self.refresh_projects, PAGE_SCRIPTS: self.refresh_scripts,
                   PAGE_SNIPPETS: self._render_snippets, PAGE_DRIVES: self.refresh_drives,
                   PAGE_PORTS: self.refresh_ports, PAGE_WEB: self._web_first_show,
                   PAGE_SETTINGS: self._refresh_statuses, PAGE_DASHBOARD: self._refresh_dashboard,
                   PAGE_AUTOCLICKER: self._ac_refresh_status, PAGE_POCKETLAUNCHER: self._pl_refresh_status,
                   PAGE_POCKETVM: self._vm_refresh_status, PAGE_CPP: self._cpp_refresh_status,
                   PAGE_DIAGNOSTICS: self.run_diagnostics}
        if index in on_show:
            on_show[index]()
        elif index == PAGE_PLUGINS:
            if not self._plugin_entries:
                self.refresh_plugins()
        elif (index == PAGE_VSCODE and self.settings.get("vscode_auto_start")
              and not self._vscode_autostarted and not self.vscode.is_running()):
            self._vscode_autostarted = True
            self.start_vscode()
        self._focus_current()

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
        """The usable screen area, excluding the taskbar/docks."""
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)

    def _fit_window_size(self, width, height):
        """Clamp the requested size so the entire window fits on screen."""
        area, margin = self._available_screen_geometry(), 24
        max_w, max_h = max(1, area.width() - margin), max(1, area.height() - margin)
        min_w, min_h = min(760, max_w), min(460, max_h)
        self.setMinimumSize(min_w, min_h)
        return max(min_w, min(int(width), max_w)), max(min_h, min(int(height), max_h))

    def _place_window_on_screen(self, width, height):
        """Size and center the normal window inside the current screen area."""
        area = self._available_screen_geometry()
        width, height = self._fit_window_size(width, height)
        self.setGeometry(area.x() + max(0, (area.width() - width) // 2),
                         area.y() + max(0, (area.height() - height) // 2), width, height)

    def _reset_size(self):
        self.showNormal()
        self._place_window_on_screen(1100, 700)
        self.settings.set("window_size", [self.width(), self.height()])

    def _restore_size(self):
        width, height = 1100, 700
        if self.settings.get("remember_size"):
            try:
                width, height = (int(v) for v in self.settings.get("window_size")[:2])
            except (TypeError, ValueError, IndexError):
                width, height = 1100, 700
        self._place_window_on_screen(width, height)  # saved sizes are clamped to short displays

    def showEvent(self, event):
        super().showEvent(event)
        # Re-check after Qt assigns the real screen (multiple monitors / changed resolution).
        QTimer.singleShot(0, self._ensure_window_fits_screen)

    def _ensure_window_fits_screen(self):
        if self.isMaximized() or self.isMinimized():
            return
        area, frame = self._available_screen_geometry(), self.frameGeometry()
        width, height = self._fit_window_size(self.width(), self.height())
        outside = (frame.right() > area.right() or frame.bottom() > area.bottom()
                   or frame.left() < area.left() or frame.top() < area.top())
        if width != self.width() or height != self.height() or outside:
            self._place_window_on_screen(width, height)

    # ---------- backup ----------

    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export settings",
                                              str(APP_DIR / "Config" / "pocketterm-settings.json"),
                                              "JSON files (*.json);;All files (*)")
        if path:
            error = self.settings.export_to(path)
            self._set_status(self.backup_status, not error, error or f"Exported to {path}")

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", str(APP_DIR / "Config"),
                                              "JSON files (*.json);;All files (*)")
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
        self.low_space_spin.blockSignals(True)
        self.low_space_spin.setValue(int(self.settings.get("low_space_gb")))
        self.low_space_spin.blockSignals(False)
        self._apply_style()
        self._refresh_statuses()
        self._refresh_tool_combo()
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

        def go(page):
            self._show_window()
            self.show_page(page)

        add_action("Show PocketTerm", self._show_window)
        for text, page in (("Terminal", PAGE_TERMINAL), ("VS Code", PAGE_VSCODE), ("Autoclicker", PAGE_AUTOCLICKER),
                           ("PocketLauncher", PAGE_POCKETLAUNCHER), ("PocketVM", PAGE_POCKETVM),
                           ("C++ Compiler", PAGE_CPP)):
            add_action(text, lambda p=page: go(p))
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
        self.showMaximized() if self._was_max else self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_visibility(self):
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.hide() if self.tray and self.tray.isVisible() else self.showMinimized()
        else:
            self._show_window()

    def _quit_from_tray(self):
        self._show_window()
        self.close()

    def _set_hotkey_label(self, state, text):
        """state: 'on', 'off' or 'error'."""
        self.hotkey_status.setText(text)
        self.hotkey_status.setStyleSheet({"on": f"color: {OK_COLOR};", "error": f"color: {ERR_COLOR};"}.get(state, ""))

    def _register_hotkey(self):
        if os.name != "nt":
            self._set_hotkey_label("off", "The global hotkey is only available on Windows.")
            return
        try:
            if self._user32 is None:
                u = ctypes.WinDLL("user32", use_last_error=True)
                u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
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
            self._hotkey_registered = bool(self._user32.RegisterHotKey(
                hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_P))
            if self._hotkey_registered:
                self._set_hotkey_label("on", f"{HOTKEY_TEXT} is active.")
            else:
                self._set_hotkey_label("error", f"Couldn't register {HOTKEY_TEXT}. Another program is probably using it.")
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
        return (self.terminals.any_running() or self.vscode.is_running() or self._tools_running()
                or self.ai.running() or self.ac.running() or self.pl.running() or self.vm.running()
                or self.cpp.running() or proc_running(self.jar_proc) or self._script_running())

    @staticmethod
    def _kill_process(proc):
        if not kill_tree(proc.processId()):
            proc.kill()

    def closeEvent(self, event):
        if (self.settings.get("confirm_close") and not self._skip_close_confirm and self._anything_running()):
            answer = QMessageBox.question(
                self, "Close PocketTerm",
                "The terminal, VS Code, PocketAi, the autoclicker, PocketLauncher, PocketVM, the C++ compiler, "
                "a tool or a script is still running.\nClosing PocketTerm will stop them. Close anyway?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return

        if self.settings.get("remember_size") and not self.isMaximized():
            self.settings.data["window_size"] = [self.width(), self.height()]
        self.settings.save()

        for timer in (self._nav_timer, self._drives_timer, self._ports_timer, self._dashboard_timer,
                      self._update_timer):
            timer.stop()
        for rel in list(self._loaded_plugins):
            self._unload_plugin(rel)
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
        self._ai_timer.stop()
        for managed in (self.ai, self.ac, self.pl, self.vm, self.cpp):
            managed.shutdown()
        if proc_running(self.jar_proc):
            self.jar_proc.terminate()
            if not self.jar_proc.waitForFinished(1500):
                self.jar_proc.kill()

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
    # Without this, Windows groups the app under python.exe and the taskbar shows the Python icon.
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PocketTerm.Launcher")
        except Exception:
            log.exception("Could not set the AppUserModelID")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # the window may be hidden in the tray; closeEvent quits explicitly

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