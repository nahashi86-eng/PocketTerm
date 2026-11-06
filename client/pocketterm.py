import sys
import os
import re
import copy
import json
import shlex
import shutil
import time
import logging
import logging.handlers
import traceback
import datetime
import urllib.request
import threading
import subprocess
import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QEvent, QProcess, QProcessEnvironment, QUrl, Signal
from PySide6.QtGui import (
    QIcon, QColor, QKeySequence, QShortcut, QTextCursor, QAction, QDesktopServices,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
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
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

# ============================================================
# CONFIGURATION
# ============================================================

FROZEN = bool(getattr(sys, "frozen", False))
# When packaged as an .exe, treat the exe's folder like the script's folder.
SCRIPT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent

FOLDERS = ["Tools", "Scripts", "Config", "Temp", "Projects", "GitPortable", "Images", "Templates"]
for folder in FOLDERS:
    (APP_DIR / folder).mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = APP_DIR / "Config" / "settings.json"

# The app tries icon.png first, then falls back to the first .png it finds.
ICON_NAME = "icon.png"
ICON_DIRS = [
    APP_DIR / "Images",
    SCRIPT_DIR / "Images",
    SCRIPT_DIR.parent / "Images",
    Path.cwd() / "Images",
]
ICON_STATUS = "Icon: not checked yet."

# Used when no custom launcher path is set in Settings. First existing file wins.
LAUNCHER_CANDIDATES = [
    Path(r"E:\Assets\GitPortable\GitBashPortable.exe"),
    APP_DIR / "GitPortable" / "GitBashPortable.exe",
]

SCRIPT_EXTS = (".bat", ".cmd", ".ps1", ".py", ".sh")

# Page indices (must match the order pages are added to the stack)
(PAGE_TERMINAL, PAGE_VSCODE, PAGE_PROJECTS,
 PAGE_SCRIPTS, PAGE_TOOLS, PAGE_SETTINGS) = range(6)
PAGE_COUNT = 6

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

APP_VERSION = "1.1.0"
LOG_FILE = APP_DIR / "Config" / "pocketterm.log"
HISTORY_DIR = APP_DIR / "Temp" / "logs"
TEMPLATES_DIR = APP_DIR / "Templates"
MAX_HISTORY = 50

# ---------- error / event log (Config/pocketterm.log) ----------
log = logging.getLogger("pocketterm")
log.setLevel(logging.INFO)
try:
    _file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
    )
    _file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_file_handler)
except OSError:
    log.addHandler(logging.NullHandler())


def _log_exception(exc_type, exc, tb):
    log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    try:
        sys.__excepthook__(exc_type, exc, tb)
    except Exception:
        pass


def _log_thread_exception(args):
    _log_exception(args.exc_type, args.exc_value, args.exc_traceback)


sys.excepthook = _log_exception
threading.excepthook = _log_thread_exception
log.info("PocketTerm %s starting (frozen=%s)", APP_VERSION, FROZEN)


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

class Settings:
    DEFAULTS = {
        "launcher_path": "",
        "auto_start": True,
        "accent": "Violet",
        "custom_accent": "#8b5cf6",
        "remember_size": True,
        "window_size": [1100, 650],
        "last_page": 0,
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
            {"name": "Notepad", "path": r"C:\Windows\System32\notepad.exe", "args": ""},
        ],
        # Behavior
        "minimize_to_tray": False,
        "hotkey_enabled": True,
        "confirm_close": True,
        "notify_on_finish": True,
        "auto_restart": False,
        "restore_session": True,
        "terminal_cd_keys": True,
        # Appearance
        "theme": "Dark",
        "font_size": 12,
        # Session restore (saved when the app closes)
        "session": {},
        # Projects
        "project_meta": {},
        "templates_seeded": False,
        # Scripts
        "favorite_scripts": [],
        "script_env": {},
        "startup_scripts": [],
        "scheduled_scripts": {},
        "script_history": [],
        # Startup profiles
        "profiles": [],
        "startup_profile": "",
        # Updates
        "update_url": "",
        "check_updates_on_start": False,
    }

    def __init__(self, path):
        self.path = path
        self.data = copy.deepcopy(self.DEFAULTS)
        self.load()

    def _merge(self, raw):
        """Copy known keys (with matching types) from raw. Returns how many were applied."""
        count = 0
        for key, default in self.DEFAULTS.items():
            if key in raw and isinstance(raw[key], type(default)):
                self.data[key] = copy.deepcopy(raw[key])
                count += 1
        return count

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._merge(raw)

    def save(self):
        try:
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError:
            pass

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
    custom = str(settings.get("launcher_path")).strip().strip('"')
    if custom:
        return Path(custom)  # even if missing, so the error shows the chosen path
    for candidate in LAUNCHER_CANDIDATES:
        if candidate.is_file():
            return candidate
    return LAUNCHER_CANDIDATES[0]


def find_bash(settings):
    """Locate bash.exe: custom setting first, then the usual GitPortable layouts."""
    custom = str(settings.get("bash_path")).strip().strip('"')
    if custom:
        p = Path(custom)
        return p if p.is_file() else None

    root = find_launcher(settings).parent
    for base in (root, root.parent):
        for rel in ("App/Git/bin/bash.exe", "App/Git/usr/bin/bash.exe",
                    "App/git/bin/bash.exe", "bin/bash.exe"):
            p = base / rel
            if p.is_file():
                return p
    fallback = Path(r"C:\Program Files\Git\bin\bash.exe")
    return fallback if fallback.is_file() else None


def find_git(settings):
    """Locate git.exe: custom setting, then next to the portable Git Bash, then PATH."""
    custom = str(settings.get("git_path")).strip().strip('"')
    if custom:
        p = Path(custom)
        return p if p.is_file() else None

    root = find_launcher(settings).parent
    for base in (root, root.parent):
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
    home = find_launcher(settings).parent / "Data" / "home"
    if home.is_dir():
        env["HOME"] = str(home)
    return env


def git_summary(git_exe, path):
    """Short text like '[main  |  3 changed]' for a project folder, or '' if it isn't a repo."""
    if not (Path(path) / ".git").exists():
        return ""
    try:
        r = subprocess.run(
            [str(git_exe), "-c", "safe.directory=*", "-C", str(path),
             "status", "--porcelain=v1", "-b"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode != 0:
        return ""
    lines = r.stdout.splitlines()
    if not lines or not lines[0].startswith("## "):
        return ""

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

    changes = len(lines) - 1
    right = f"\u25cf {changes} changed" if changes else "clean"
    return "[" + " ".join(left) + "  |  " + right + "]"


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
    if FROZEN:
        # sys.executable is PocketTerm.exe itself, so look for a real Python.
        return shutil.which("python") or shutil.which("py") or "python"
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        alt = exe.with_name("python.exe")
        if alt.is_file():
            return str(alt)
    return str(exe)


def vscode_launch_plan(settings, target=None):
    """Return (args, popen_kwargs, description). args is None on error (description = reason)."""
    extra = str(settings.get("vscode_args")).strip()
    quiet = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    exe = str(settings.get("vscode_exe")).strip().strip('"')
    if exe:
        p = Path(exe)
        if not p.is_file():
            return None, None, f"VS Code executable not found: {p}"
        args = [str(p)] + shlex.split(extra) + ([str(target)] if target else [])
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


def run_script(path, settings, args_text="", env_extra=None):
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

    if env_extra:
        env = dict(env if env is not None else os.environ)
        env.update(env_extra)
    try:
        subprocess.Popen(
            cmd, cwd=str(path.parent), env=env,
            creationflags=CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        )
    except OSError as e:
        return f"Failed to start: {e}"
    return None


def _script_command_base(path, settings, args):
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


def script_command(path, settings, args, env_extra=None):
    program, argv, env, error = _script_command_base(path, settings, args)
    if env_extra and not error:
        env = dict(env if env is not None else os.environ)
        env.update(env_extra)
    return program, argv, env, error


def script_key(path):
    """Stable key for a script: its path relative to the Scripts folder."""
    try:
        return Path(path).relative_to(APP_DIR / "Scripts").as_posix()
    except ValueError:
        return Path(path).name


def parse_env(text):
    """'A=1; B=two' (or one per line) -> {'A': '1', 'B': 'two'}."""
    env = {}
    for part in re.split(r"[;\n]", text or ""):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            if k:
                env[k] = v.strip()
    return env


def to_posix_path(path):
    """C:\\Users\\me\\proj -> /c/Users/me/proj (Git Bash style)."""
    p = Path(path).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m:
        return f"/{m.group(1).lower()}/{m.group(2)}"
    return p


def version_tuple(v):
    nums = re.findall(r"\d+", str(v))
    return tuple(int(n) for n in nums[:4]) or (0,)


def remote_to_web_url(remote):
    """git@github.com:me/repo.git or https://host/me/repo.git -> https://host/me/repo"""
    r = (remote or "").strip()
    if not r:
        return ""
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", r)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"^(https?://.+?)(?:\.git)?/?$", r)
    if m:
        return re.sub(r"^(https?://)[^/@]+@", r"\1", m.group(1))
    return ""


# ---------- project templates ----------

DEFAULT_TEMPLATES = {
    "Python": {
        "main.py": 'def main():\n    print("Hello from {{PROJECT_NAME}}")\n\n\nif __name__ == "__main__":\n    main()\n',
        "requirements.txt": "",
        "README.md": "# {{PROJECT_NAME}}\n\nA Python project.\n",
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\n.env\n",
    },
    "Node": {
        "index.js": 'console.log("Hello from {{PROJECT_NAME}}");\n',
        "package.json": '{\n  "name": "{{PROJECT_NAME}}",\n  "version": "1.0.0",\n  "main": "index.js"\n}\n',
        "README.md": "# {{PROJECT_NAME}}\n\nA Node project.\n",
        ".gitignore": "node_modules/\n.env\n",
    },
    "Web page": {
        "index.html": '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <title>{{PROJECT_NAME}}</title>\n  <link rel="stylesheet" href="style.css">\n</head>\n<body>\n  <h1>{{PROJECT_NAME}}</h1>\n  <script src="script.js"></script>\n</body>\n</html>\n',
        "style.css": "body {\n  font-family: sans-serif;\n  margin: 2rem;\n}\n",
        "script.js": 'console.log("{{PROJECT_NAME}} loaded");\n',
    },
}


def seed_default_templates():
    for name, files in DEFAULT_TEMPLATES.items():
        d = TEMPLATES_DIR / name
        if d.exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        for fname, content in files.items():
            (d / fname).write_text(content, encoding="utf-8")


def list_templates():
    try:
        return sorted((x for x in TEMPLATES_DIR.iterdir() if x.is_dir()),
                      key=lambda x: x.name.lower())
    except OSError:
        return []


def apply_template(template_dir, target, project_name):
    shutil.copytree(template_dir, target, dirs_exist_ok=True)
    for f in Path(target).rglob("*"):
        if f.is_file() and f.stat().st_size < 512 * 1024:
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "{{PROJECT_NAME}}" in text:
                f.write_text(text.replace("{{PROJECT_NAME}}", project_name), encoding="utf-8")


def open_path(path):
    path.mkdir(parents=True, exist_ok=True) if not path.suffix else None
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


# ============================================================
# THEME
# ============================================================

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


THEMES = {
    "Dark": dict(
        light=False, bg="#08070d", sidebar="#0e0a18", panel="#130e21", btn="#1a1230",
        btn_hover="#2a1d4d", nav_hover="#181129", checked="#1e1535", side_border="#231a3a",
        border2="#2a1f45", dis_bg="#120d20", dis_border="#1f1736", dis_text="#5d5478",
        text="#ede9fe", title="#f5f3ff", muted="#8b83a8", subtitle="#6d5fa0",
        nav_text="#a99fc9", danger_bg="#2a1220", danger_border="#5b1f3a",
        danger_text="#fda4af", danger_hover_bg="#3d1730", danger_hover_border="#be3a68",
        log_text="#d4cdf0", ok="#6ee7b7", err="#fda4af",
    ),
    "Midnight": dict(
        light=False, bg="#070b14", sidebar="#0b111d", panel="#101827", btn="#162033",
        btn_hover="#1f2f4d", nav_hover="#131c2e", checked="#18253d", side_border="#1c2840",
        border2="#223250", dis_bg="#0d1420", dis_border="#18223a", dis_text="#51607f",
        text="#e6edf7", title="#f5f9ff", muted="#8494b0", subtitle="#5f7196",
        nav_text="#9db0cc", danger_bg="#2a1220", danger_border="#5b1f3a",
        danger_text="#fda4af", danger_hover_bg="#3d1730", danger_hover_border="#be3a68",
        log_text="#cfd9ea", ok="#6ee7b7", err="#fda4af",
    ),
    "Light": dict(
        light=True, bg="#f4f2fa", sidebar="#ebe7f6", panel="#ffffff", btn="#ece8f7",
        btn_hover="#ddd6f3", nav_hover="#e2ddf2", checked="#d9d1f2", side_border="#d3cce8",
        border2="#d6d0e8", dis_bg="#f0eef6", dis_border="#e2deee", dis_text="#a8a2bd",
        text="#241a40", title="#160f2e", muted="#6a6385", subtitle="#8a82a8",
        nav_text="#4a4166", danger_bg="#fde8ec", danger_border="#f3b4c2",
        danger_text="#b4234a", danger_hover_bg="#fbd3dc", danger_hover_border="#d1496c",
        log_text="#2b2150", ok="#0f8a5f", err="#c2284d",
    ),
}
THEME_CHOICES = list(THEMES.keys())
FONT_SIZES = {"Small": 11, "Normal": 12, "Large": 14, "Extra large": 16}
CURRENT_THEME = THEMES["Dark"]

STYLE_TEMPLATE = """
QMainWindow, QWidget {
    background-color: @BG@;
    color: @TEXT@;
    font-family: 'Segoe UI';
}

QLabel { background: transparent; }

QToolTip {
    background-color: @PANEL@;
    color: @TEXT@;
    border: 1px solid @BORDER2@;
    padding: 4px;
}

QFrame#Sidebar {
    background-color: @SIDEBAR@;
    border: none;
    border-right: 1px solid @SIDE_BORDER@;
}

QLabel#Logo { font-family: 'Consolas'; font-size: 26px; font-weight: 700; color: @TITLE@; }
QLabel#Subtitle { color: @SUBTITLE@; font-size: 9px; font-weight: 600; }
QLabel#PageTitle { font-size: 24px; font-weight: 700; color: @TITLE@; }
QLabel#Description { color: @MUTED@; font-size: 12px; }
QLabel#SectionTitle { font-size: 15px; font-weight: 700; color: @LIGHTER@; }
QLabel#Muted { color: @MUTED@; font-size: 12px; }
QLabel#Status { color: @SOFT@; font-weight: bold; }

QFrame#Card {
    background-color: @PANEL@;
    border: 1px solid @BORDER2@;
    border-radius: 12px;
}

QFrame#TerminalFrame {
    background-color: #000000;
    border: 1px solid @BORDER@;
    border-radius: 8px;
}

QPushButton {
    background-color: @BTN@;
    color: @TEXT@;
    border: 1px solid @BORDER2@;
    border-radius: 8px;
    padding: 10px 15px;
    font-size: 12px;
}
QPushButton:hover { background-color: @BTN_HOVER@; border: 1px solid @HOVERBORDER@; }
QPushButton:disabled { background-color: @DIS_BG@; color: @DIS_TEXT@; border: 1px solid @DIS_BORDER@; }

QPushButton#Navigation {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    color: @NAV_TEXT@;
    text-align: left;
    padding: 12px 16px;
    font-size: 13px;
}
QPushButton#Navigation:hover {
    background-color: @NAV_HOVER@;
    border: 1px solid transparent;
    color: @TEXT@;
}
QPushButton#Navigation:checked {
    background-color: @CHECKED@;
    border: 1px solid @HOVERBORDER@;
    color: @LIGHTER@;
    font-weight: 600;
}

QPushButton#Primary { background-color: @STRONG@; border: none; font-weight: bold; color: #ffffff; }
QPushButton#Primary:hover { background-color: @ACCENT@; }

QPushButton#Danger { background-color: @DANGER_BG@; border: 1px solid @DANGER_BORDER@; color: @DANGER_TEXT@; }
QPushButton#Danger:hover { background-color: @DANGER_HOVER_BG@; border: 1px solid @DANGER_HOVER_BORDER@; }

QPushButton#TabClose {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 0px;
    font-size: 15px;
    color: @NAV_TEXT@;
}
QPushButton#TabClose:hover { background-color: @DANGER_HOVER_BG@; color: @DANGER_TEXT@; }

QLineEdit, QSpinBox {
    background-color: @SIDEBAR@;
    border: 1px solid @BORDER2@;
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 12px;
    selection-background-color: @STRONG@;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus { border: 1px solid @ACCENT@; }

QPlainTextEdit {
    background-color: @SIDEBAR@;
    border: 1px solid @BORDER2@;
    border-radius: 8px;
    padding: 6px;
    font-size: 12px;
    selection-background-color: @STRONG@;
    selection-color: #ffffff;
}

QPlainTextEdit#Log {
    font-family: 'Consolas';
    font-size: 12px;
    color: @LOG_TEXT@;
}

QComboBox {
    background-color: @SIDEBAR@;
    border: 1px solid @BORDER2@;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
    min-width: 140px;
}
QComboBox:hover, QComboBox:focus { border: 1px solid @ACCENT@; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background-color: @PANEL@;
    border: 1px solid @BORDER2@;
    selection-background-color: @STRONG@;
    selection-color: #ffffff;
    color: @TEXT@;
    outline: 0;
}

QListWidget {
    background-color: @SIDEBAR@;
    border: 1px solid @BORDER2@;
    border-radius: 8px;
    padding: 6px;
    font-size: 13px;
    outline: 0;
}
QListWidget::item { padding: 9px 10px; border-radius: 6px; }
QListWidget::item:hover { background-color: @NAV_HOVER@; }
QListWidget::item:selected { background-color: @STRONG@; color: #ffffff; }
QListWidget::indicator, QTableWidget::indicator {
    width: 14px; height: 14px;
    border: 1px solid @BORDER@;
    border-radius: 3px;
    background: @SIDEBAR@;
}
QListWidget::indicator:checked, QTableWidget::indicator:checked {
    background: @ACCENT@;
    border: 1px solid @ACCENT@;
}

QTableWidget {
    background-color: @SIDEBAR@;
    border: 1px solid @BORDER2@;
    border-radius: 8px;
    gridline-color: @BORDER2@;
    font-size: 12px;
    outline: 0;
}
QTableWidget::item { padding: 4px 6px; }
QTableWidget::item:selected { background-color: @STRONG@; color: #ffffff; }
QHeaderView::section {
    background-color: @PANEL@;
    color: @MUTED@;
    border: none;
    border-bottom: 1px solid @BORDER2@;
    padding: 6px;
    font-size: 12px;
}

QTabWidget#Tabs::pane {
    border: 1px solid @BORDER@;
    border-radius: 8px;
    background: #000000;
    top: 0px;
    padding: 4px;
}
QTabBar { background: transparent; }
QTabBar::tab {
    background-color: @PANEL@;
    color: @NAV_TEXT@;
    border: 1px solid @BORDER2@;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 12px;
    margin-right: 3px;
    margin-bottom: 6px;
    font-size: 12px;
}
QTabBar::tab:selected { background-color: @CHECKED@; color: @LIGHTER@; border-color: @HOVERBORDER@; }
QTabBar::tab:hover:!selected { background-color: @NAV_HOVER@; color: @TEXT@; }

QMenu {
    background-color: @PANEL@;
    border: 1px solid @BORDER2@;
    padding: 4px;
}
QMenu::item { padding: 7px 22px; border-radius: 6px; }
QMenu::item:selected { background-color: @STRONG@; color: #ffffff; }
QMenu::separator { height: 1px; background: @BORDER2@; margin: 4px 8px; }

QDialog#Palette { border: 1px solid @HOVERBORDER@; }

QStatusBar {
    background-color: @SIDEBAR@;
    color: @MUTED@;
    border-top: 1px solid @SIDE_BORDER@;
    font-size: 11px;
}
QStatusBar::item { border: none; }

QCheckBox { background: transparent; spacing: 10px; font-size: 12px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid @BORDER@;
    border-radius: 4px;
    background: @SIDEBAR@;
}
QCheckBox::indicator:checked { background: @ACCENT@; border: 1px solid @ACCENT@; }

QScrollArea { border: none; }
QScrollBar:vertical { background: @BG@; width: 10px; }
QScrollBar::handle:vertical { background: @BORDER@; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: @HOVERBORDER@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""


def build_style(settings):
    global CURRENT_THEME
    theme = THEMES.get(settings.get("theme"), THEMES["Dark"])
    CURRENT_THEME = theme

    accent, strong, soft, light, lighter, border, hover_border = get_palette(settings)
    if theme["light"]:
        # The palette's light shades are made for dark backgrounds; use darker ones here.
        light = lighter = soft = strong
        border = mix(accent, "#ffffff", 0.70)
        hover_border = mix(accent, "#ffffff", 0.35)

    style = STYLE_TEMPLATE
    for token, value in (
        ("@ACCENT@", accent), ("@STRONG@", strong), ("@SOFT@", soft),
        ("@LIGHT@", light), ("@LIGHTER@", lighter),
        ("@BORDER@", border), ("@HOVERBORDER@", hover_border),
    ):
        style = style.replace(token, value)
    for key, value in theme.items():
        if key != "light":
            style = style.replace(f"@{key.upper()}@", value)

    try:
        fs = max(9, min(20, int(settings.get("font_size"))))
    except (TypeError, ValueError):
        fs = 12
    style = re.sub(
        r"font-size:\s*(\d+)px",
        lambda m: f"font-size: {max(8, round(int(m.group(1)) * fs / 12))}px",
        style,
    )
    return style


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

    # Windows already embedded by any EmbeddedWindow, so two tabs never grab the same one.
    _claimed = set()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

        self.setMinimumSize(400, 300)
        self.setStyleSheet("background-color: #000000;")
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.hwnd = None
        self.process = None
        self.launcher_started = False
        self.previous_windows = set()
        self._attached = None
        self._last_target = None
        self._restart_count = 0
        self._embedded_at = 0.0
        self._stopping = False

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
            "color: #8b83a8; padding: 20px; font-size: 14px; background: transparent;"
        )

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.addWidget(self.status_label)

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

    def on_embedded(self):
        """Called once the program's window has been embedded. Optional."""
        pass

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

    def set_message(self, text):
        self.status_label.setText(text)
        self.status_label.show()

    def is_running(self):
        return bool(self.hwnd) or self.launcher_started

    def start(self, target=None):
        if os.name != "nt":
            self.set_message("Native window embedding is only supported on Windows.")
            return

        if self.hwnd or self.launcher_started:
            return

        self._stopping = False
        self._last_target = target
        args, kwargs, error = self.build_launch(target)
        if args is None:
            self.set_message(error)
            return

        self.launcher_started = True
        self.set_message(f"Starting {self.NAME}...")
        self.previous_windows = self.get_candidate_windows()

        try:
            self.process = subprocess.Popen(args, **kwargs)
            self.poll_timer.start()
            self.timeout_timer.start(self.TIMEOUT_MS)
        except Exception as e:
            self.launcher_started = False
            self.set_message(f"Failed to launch {self.NAME}: {e}")

    def stop(self, message=None):
        self._stopping = True
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
            self.process.terminate()
        self.process = None
        self.launcher_started = False
        self.set_message(message or f"{self.NAME} stopped.")

    def restart(self, target=None):
        if target is None:
            target = self._last_target
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
            log.info("%s window closed", self.NAME)
            if time.time() - self._embedded_at > 60:
                self._restart_count = 0
            if self.settings.get("auto_restart") and self._restart_count < 3:
                self._restart_count += 1
                self.set_message(
                    f"{self.NAME} closed. Restarting automatically ({self._restart_count}/3)..."
                )
                QTimer.singleShot(1500, self._auto_restart)
            else:
                self.set_message(
                    f"{self.NAME} was closed.\nUse Start / Restart to open it again."
                )

    def _auto_restart(self):
        try:
            if self._stopping or self.hwnd or self.launcher_started:
                return
            log.info("Auto-restarting %s", self.NAME)
            self.start(self._last_target)
        except RuntimeError:
            pass  # the widget was deleted in the meantime

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
            self.status_label.hide()
            self.embed_window()
            self.watch_timer.start()
            self._embedded_at = time.time()
            log.info("%s window embedded", self.NAME)
            self.on_embedded()

    def embedding_timeout(self):
        if not self.hwnd:
            self.poll_timer.stop()
            self.launcher_started = False
            log.warning("%s: no window captured before the timeout", self.NAME)
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

    def send_text(self, text):
        """Type text into the embedded window with WM_CHAR messages."""
        if os.name != "nt" or not self.hwnd:
            return
        WM_CHAR = 0x0102
        hwnd_w = wintypes.HWND(self.hwnd)
        for ch in text:
            self.user32.PostMessageW(hwnd_w, WM_CHAR, ord(ch), 0)

    def focus_window(self):
        """Give keyboard focus to the embedded window."""
        if os.name != "nt" or not self.hwnd:
            return
        hwnd_w = wintypes.HWND(self.hwnd)
        if not self.user32.IsWindow(hwnd_w):
            return
        self._attach_input()
        self.user32.SetFocus(hwnd_w)

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
        cwd = launcher.parent
        env = None
        if target and Path(target).is_dir():
            # CHERE_INVOKING makes a Git Bash login shell stay in the starting folder.
            cwd = Path(target)
            env = os.environ.copy()
            env["CHERE_INVOKING"] = "1"
        kwargs = {"cwd": str(cwd), "creationflags": CREATE_NEW_PROCESS_GROUP}
        if env:
            kwargs["env"] = env
        return [str(launcher)], kwargs, ""

    def on_embedded(self):
        # Some launchers ignore the start folder, so also type a cd into the new shell.
        target = self._last_target
        if target and Path(target).is_dir() and self.settings.get("terminal_cd_keys"):
            line = "cd " + shlex.quote(to_posix_path(target)) + " && clear\r"
            QTimer.singleShot(1800, lambda: self.send_text(line))

    def accepts(self, hwnd):
        return self.window_class(hwnd) == "mintty"


class EmbeddedVSCode(EmbeddedWindow):
    NAME = "VS Code"
    TIMEOUT_MS = 45000
    IDLE_MESSAGE = "VS Code isn't running.\nPress Start / Restart VS Code above."
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
        self.tabBar().tabBarDoubleClicked.connect(self.rename_tab)
        self.add_terminal(start=False)

    def add_terminal(self, start=True, start_dir=None, name=None):
        term = EmbeddedGitBash(self.settings)
        self._counter += 1
        idx = add_closable_tab(self, term, name or f"Terminal {self._counter}")
        self.setCurrentIndex(idx)
        if start:
            term.start(start_dir)
        return term

    def rename_tab(self, index):
        if index < 0:
            return
        text, ok = QInputDialog.getText(
            self, "Rename tab", "Tab name:", text=self.tabText(index)
        )
        if ok and text.strip():
            self.setTabText(index, text.strip())

    def tab_names(self):
        return [self.tabText(i) for i in range(self.count())]

    def terminals(self):
        return [self.widget(i) for i in range(self.count())]

    def current_terminal(self):
        return self.currentWidget()

    def close_tab(self, index):
        term = self.widget(index)
        if term is None:
            return
        if self.count() == 1:
            term.stop("Terminal stopped.\nUse + New Tab or Start / Restart Terminal in Settings.")
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


# ============================================================
# DIALOGS
# ============================================================

def _muted_label(text):
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


class CommandPalette(QDialog):
    """Ctrl+K: type to find a page, project, script, tool or profile and run it."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setObjectName("Palette")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setMinimumWidth(580)
        self.entries = entries
        self.chosen = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Type to search pages, projects, scripts, tools and profiles..."
        )
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._run_current)
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.setMinimumHeight(300)
        self.results.itemActivated.connect(lambda _i: self._run_current())
        self.results.itemDoubleClicked.connect(lambda _i: self._run_current())
        layout.addWidget(self.results)

        self._filter("")
        self.search.setFocus()

    def _filter(self, text):
        tokens = text.lower().split()
        self.results.clear()
        for i, (label, _action) in enumerate(self.entries):
            low = label.lower()
            if all(t in low for t in tokens):
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, i)
                self.results.addItem(item)
                if self.results.count() >= 60:
                    break
        if self.results.count():
            self.results.setCurrentRow(0)

    def _run_current(self):
        item = self.results.currentItem()
        if item is None:
            return
        self.chosen = self.entries[item.data(Qt.UserRole)][1]
        self.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Down, Qt.Key_Up):
            step = 1 if event.key() == Qt.Key_Down else -1
            row = max(0, min(self.results.count() - 1, self.results.currentRow() + step))
            self.results.setCurrentRow(row)
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            g = parent.geometry()
            self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 90)


class ProjectMetaDialog(QDialog):
    COLORS = {
        "None": "", "Red": "#f87171", "Orange": "#fb923c", "Yellow": "#facc15",
        "Green": "#4ade80", "Blue": "#60a5fa", "Purple": "#c084fc", "Pink": "#f472b6",
    }

    def __init__(self, name, meta, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Tag and notes - {name}")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(_muted_label("Tag (short label shown next to the project name)"))
        self.tag_edit = QLineEdit(str(meta.get("tag", "")))
        self.tag_edit.setPlaceholderText("e.g. work, client, personal")
        layout.addWidget(self.tag_edit)

        layout.addWidget(_muted_label("Color (tints the project's row)"))
        self.color_combo = QComboBox()
        self.color_combo.addItems(list(self.COLORS))
        current = str(meta.get("color", ""))
        for label, hexv in self.COLORS.items():
            if hexv == current:
                self.color_combo.setCurrentText(label)
        layout.addWidget(self.color_combo)

        layout.addWidget(_muted_label("Notes (shown as a tooltip when you hover the project)"))
        self.note_edit = QPlainTextEdit(str(meta.get("note", "")))
        self.note_edit.setMinimumHeight(140)
        layout.addWidget(self.note_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_meta(self):
        return {
            "tag": self.tag_edit.text().strip(),
            "color": self.COLORS.get(self.color_combo.currentText(), ""),
            "note": self.note_edit.toPlainText().strip(),
        }


class ProfileDialog(QDialog):
    def __init__(self, profile, tool_names, script_keys, parent=None):
        super().__init__(parent)
        profile = profile or {}
        self.setWindowTitle("Startup profile")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(_muted_label("Profile name"))
        self.name_edit = QLineEdit(str(profile.get("name", "")))
        self.name_edit.setPlaceholderText("e.g. Web dev")
        layout.addWidget(self.name_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("Terminal tabs to open"))
        self.term_spin = QSpinBox()
        self.term_spin.setRange(0, 8)
        try:
            self.term_spin.setValue(int(profile.get("terminals", 1)))
        except (TypeError, ValueError):
            self.term_spin.setValue(1)
        row.addWidget(self.term_spin)
        row.addStretch()
        layout.addLayout(row)

        layout.addWidget(_muted_label("VS Code folder (optional)"))
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(str(profile.get("vscode_project", "")))
        self.folder_edit.setPlaceholderText("Folder to open in VS Code")
        folder_row.addWidget(self.folder_edit, stretch=1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        self.in_project = QCheckBox("Start the terminals in that folder")
        self.in_project.setChecked(bool(profile.get("terminal_in_project", True)))
        layout.addWidget(self.in_project)

        layout.addWidget(_muted_label("Tools to launch"))
        self.tool_list = self._checklist(tool_names, profile.get("tools", []))
        layout.addWidget(self.tool_list)

        layout.addWidget(_muted_label("Scripts to run (each in its own console window)"))
        self.script_list = self._checklist(script_keys, profile.get("scripts", []))
        layout.addWidget(self.script_list)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _checklist(names, checked):
        widget = QListWidget()
        widget.setMaximumHeight(110)
        checked = set(checked if isinstance(checked, list) else [])
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in checked else Qt.Unchecked)
            widget.addItem(item)
        return widget

    @staticmethod
    def _checked(widget):
        return [
            widget.item(i).text() for i in range(widget.count())
            if widget.item(i).checkState() == Qt.Checked
        ]

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Folder to open in VS Code", self.folder_edit.text() or str(APP_DIR / "Projects")
        )
        if folder:
            self.folder_edit.setText(os.path.normpath(folder))

    def accept(self):
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            self.name_edit.setPlaceholderText("A name is required")
            return
        super().accept()

    def result_profile(self):
        return {
            "name": self.name_edit.text().strip(),
            "terminals": self.term_spin.value(),
            "vscode_project": self.folder_edit.text().strip().strip('"'),
            "terminal_in_project": self.in_project.isChecked(),
            "tools": self._checked(self.tool_list),
            "scripts": self._checked(self.script_list),
        }


class ScheduleDialog(QDialog):
    """Pick scripts that run when PocketTerm starts, or repeat every N minutes."""

    def __init__(self, keys, startup, scheduled, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Startup and scheduled scripts")
        self.setMinimumSize(580, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(_muted_label(
            "These scripts run in the background (no console window) and you get a "
            "notification when they finish. Repeating scripts only run while PocketTerm is open."
        ))

        self.table = QTableWidget(len(keys), 3)
        self.table.setHorizontalHeaderLabels(
            ["Script", "Run at startup", "Repeat every N min (0 = off)"]
        )
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        for row, key in enumerate(keys):
            name_item = QTableWidgetItem(key)
            name_item.setFlags(Qt.ItemIsEnabled)
            startup_item = QTableWidgetItem()
            startup_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            startup_item.setCheckState(Qt.Checked if key in startup else Qt.Unchecked)
            try:
                minutes = int(scheduled.get(key, 0))
            except (TypeError, ValueError):
                minutes = 0
            minutes_item = QTableWidgetItem(str(minutes))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, startup_item)
            self.table.setItem(row, 2, minutes_item)
        layout.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_values(self):
        startup = []
        scheduled = {}
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).text()
            if self.table.item(row, 1).checkState() == Qt.Checked:
                startup.append(key)
            try:
                minutes = int(self.table.item(row, 2).text().strip() or 0)
            except ValueError:
                minutes = 0
            minutes = max(0, min(10080, minutes))
            if minutes > 0:
                scheduled[key] = minutes
        return startup, scheduled


class HistoryDialog(QDialog):
    """Recent script runs. Select one to see its saved output; re-run it if you like."""

    def __init__(self, history, log_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Script run history")
        self.setMinimumSize(860, 480)
        self.history = [h for h in history if isinstance(h, dict)]
        self.log_dir = Path(log_dir)
        self.rerun_key = None
        self.cleared = False

        layout = QVBoxLayout(self)
        body = QHBoxLayout()

        self.runs = QListWidget()
        self.runs.setMinimumWidth(330)
        self.runs.currentRowChanged.connect(self._show)
        body.addWidget(self.runs)

        self.output = QPlainTextEdit()
        self.output.setObjectName("Log")
        self.output.setReadOnly(True)
        body.addWidget(self.output, stretch=1)
        layout.addLayout(body, stretch=1)

        row = QHBoxLayout()
        self.btn_rerun = QPushButton("Re-run with log")
        self.btn_rerun.setObjectName("Primary")
        self.btn_rerun.clicked.connect(self._rerun)
        row.addWidget(self.btn_rerun)
        btn_clear = QPushButton("Clear history")
        btn_clear.setObjectName("Danger")
        btn_clear.clicked.connect(self._clear)
        row.addWidget(btn_clear)
        row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        layout.addLayout(row)

        for h in self.history:
            self.runs.addItem(
                f"{h.get('time', '')}   {h.get('script', '?')}   [{self._status(h)}]"
            )
        if self.history:
            self.runs.setCurrentRow(0)
        else:
            self.output.setPlainText("No runs recorded yet.")
            self.btn_rerun.setEnabled(False)

    @staticmethod
    def _status(h):
        parts = []
        mode = h.get("mode")
        code = h.get("code")
        if mode == "console":
            parts.append("console")
        elif code is None:
            parts.append("failed to start")
        else:
            parts.append(f"exit {code}")
        dur = h.get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            parts.append(f"{dur:.1f}s")
        if mode in ("startup", "scheduled"):
            parts.append(mode)
        return ", ".join(parts)

    def _show(self, row):
        if not (0 <= row < len(self.history)):
            self.output.clear()
            return
        h = self.history[row]
        header = f"$ {h.get('script', '?')} {h.get('args', '')}".rstrip() + "\n\n"
        name = h.get("log")
        text = ""
        if name:
            try:
                text = (self.log_dir / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = "(the saved output file is no longer available)"
        else:
            text = "(no output was captured for this run)"
        self.output.setPlainText(header + text)
        self.btn_rerun.setEnabled(True)

    def _rerun(self):
        row = self.runs.currentRow()
        if 0 <= row < len(self.history):
            self.rerun_key = self.history[row].get("script")
            self.accept()

    def _clear(self):
        self.cleared = True
        self.history = []
        self.runs.clear()
        self.output.setPlainText("History cleared.")
        self.btn_rerun.setEnabled(False)


# ============================================================
# MAIN WINDOW
# ============================================================

class MainWindow(QMainWindow):
    # Emitted from background threads, handled on the UI thread.
    git_info_ready = Signal(int, str, str)
    task_done = Signal(str, bool, str)

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

        self.setWindowTitle("PocketTerm Launcher")
        self.setAcceptDrops(True)
        self._apply_style()
        self._restore_size()

        self.git_info_ready.connect(self._on_git_info)
        self.task_done.connect(self._on_task_done)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create the embedded widgets first; pages and sidebar reference them.
        self.terminals = TerminalTabs(self.settings)
        self.vscode = EmbeddedVSCode(self.settings)

        main_layout.addWidget(self._build_sidebar())

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self._build_terminal_page())   # PAGE_TERMINAL
        self.stacked_widget.addWidget(self._build_vscode_page())     # PAGE_VSCODE
        self.stacked_widget.addWidget(self._build_projects_page())   # PAGE_PROJECTS
        self.stacked_widget.addWidget(self._build_scripts_page())    # PAGE_SCRIPTS
        self.stacked_widget.addWidget(self._build_tools_page())      # PAGE_TOOLS
        self.stacked_widget.addWidget(self._build_settings_page())   # PAGE_SETTINGS
        main_layout.addWidget(self.stacked_widget, stretch=1)

        for index, button in self.nav_buttons.items():
            button.clicked.connect(lambda _=False, i=index: self.show_page(i))

        self.terminals.currentChanged.connect(lambda _i: self._focus_current())
        self.tool_tabs.currentChanged.connect(lambda _i: self._focus_current())

        self._setup_shortcuts()
        self.refresh_projects()
        self.refresh_scripts()

        if self.settings.get("auto_start"):
            QTimer.singleShot(500, self.start_terminal)
        else:
            self.terminals.current_terminal().set_message(
                "Auto-start is off.\nOpen Settings and choose Start / Restart Terminal."
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
        label.setStyleSheet("color: #6ee7b7;" if ok else "color: #fda4af;")

    def _apply_style(self):
        self.setStyleSheet(build_style(self.settings))

    # ---------- building the pages ----------

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 30, 20, 30)
        layout.setSpacing(6)

        logo_label = QLabel("PocketTerm")
        logo_label.setObjectName("Logo")
        subtitle_label = QLabel("DEVELOPER ENVIRONMENT")
        subtitle_label.setObjectName("Subtitle")
        layout.addWidget(logo_label)
        layout.addWidget(subtitle_label)
        layout.addSpacing(30)

        self.nav_buttons = {}
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        def add_nav(label, index):
            btn = QPushButton(label)
            btn.setObjectName("Navigation")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"Ctrl+{index + 1}")
            self.nav_group.addButton(btn)
            self.nav_buttons[index] = btn
            layout.addWidget(btn)

        add_nav("Terminal View", PAGE_TERMINAL)
        add_nav("VS Code", PAGE_VSCODE)
        add_nav("Projects", PAGE_PROJECTS)
        add_nav("Scripts", PAGE_SCRIPTS)
        add_nav("Tools", PAGE_TOOLS)
        layout.addStretch()
        add_nav("Settings Profile", PAGE_SETTINGS)

        self.nav_buttons[PAGE_TERMINAL].setChecked(True)
        return sidebar

    def _build_terminal_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        buttons = (self._button("+ New Tab", self.new_terminal_tab, "Primary"),)
        layout.addLayout(self._page_header(
            "Workspace Terminal",
            "Native standalone sandbox console environment. Use + New Tab for more sessions "
            "(Ctrl+T).",
            buttons,
        ))
        layout.addSpacing(20)
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
        self.project_list.itemDoubleClicked.connect(lambda _item: self.open_project_in_vscode())
        layout.addWidget(self.project_list, stretch=1)

        self.projects_status = QLabel("")
        self.projects_status.setObjectName("Muted")
        self.projects_status.setWordWrap(True)
        layout.addWidget(self.projects_status)

        row = QHBoxLayout()
        row.addWidget(self._button("Open in VS Code", self.open_project_in_vscode, "Primary"))
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

        self.script_search = QLineEdit()
        self.script_search.setPlaceholderText("Search scripts...  (Ctrl+F)")
        self.script_search.setClearButtonEnabled(True)
        self.script_search.textChanged.connect(lambda _t: self._render_scripts())
        layout.addWidget(self.script_search)
        layout.addSpacing(8)

        self.script_list = QListWidget()
        self.script_list.itemDoubleClicked.connect(lambda _item: self.run_selected_script())
        self.script_list.currentItemChanged.connect(self._script_selected)
        layout.addWidget(self.script_list, stretch=3)

        args_row = QHBoxLayout()
        args_label = QLabel("Arguments")
        args_label.setObjectName("Muted")
        args_row.addWidget(args_label)
        self.script_args_edit = QLineEdit()
        self.script_args_edit.setPlaceholderText("Optional arguments for the selected script (remembered per script)")
        self.script_args_edit.editingFinished.connect(self._save_script_args)
        args_row.addWidget(self.script_args_edit, stretch=1)
        layout.addLayout(args_row)

        self.scripts_status = QLabel("")
        self.scripts_status.setObjectName("Muted")
        self.scripts_status.setWordWrap(True)
        layout.addWidget(self.scripts_status)

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
        layout.addLayout(row)

        layout.addSpacing(8)
        log_header = QHBoxLayout()
        log_title = QLabel("Output")
        log_title.setObjectName("SectionTitle")
        log_header.addWidget(log_title)
        log_header.addStretch()
        log_header.addWidget(self._button("Clear", lambda: self.log_view.clear()))
        layout.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setPlaceholderText("Output from 'Run with Log' appears here.")
        layout.addWidget(self.log_view, stretch=2)
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
            "Terminal stopped. Use Start / Restart Terminal in Settings."), "Danger"))
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
            lay, "vscode_exe", "e.g. E:\\Tools\\VSCode\\Code.exe", "Select Code.exe",
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
            "VS Code stopped.\nPress Start / Restart VS Code above."), "Danger"))
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
        for i in range(PAGE_COUNT):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda i=i: self.show_page(i))

        sc = QShortcut(QKeySequence("Ctrl+T"), self)
        sc.activated.connect(self.new_terminal_tab)

        sc = QShortcut(QKeySequence("Ctrl+F"), self)
        sc.activated.connect(self._focus_search)

    def _focus_search(self):
        index = self.stacked_widget.currentIndex()
        if index == PAGE_PROJECTS:
            self.project_search.setFocus()
            self.project_search.selectAll()
        elif index == PAGE_SCRIPTS:
            self.script_search.setFocus()
            self.script_search.selectAll()

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
        self.stacked_widget.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if self.settings.get("last_page") != index:
            self.settings.set("last_page", index)

        if index == PAGE_PROJECTS:
            self.refresh_projects()
        elif index == PAGE_SCRIPTS:
            self.refresh_scripts()
        elif index == PAGE_SETTINGS:
            self._refresh_statuses()
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
        return Path(item.data(Qt.UserRole)) if item else None

    def _record_recent(self, path):
        s = str(Path(path))
        recent = [r for r in self.settings.get("recent_projects") if r != s]
        recent.insert(0, s)
        self.settings.set("recent_projects", recent[:MAX_RECENT])
        self._render_projects()

    def _project_text(self, p):
        pinned = str(p) in self.settings.get("pinned_projects")
        text = ("\u2605 " if pinned else "") + p.name
        summary = self.git_cache.get(str(p), "")
        if summary:
            text += "     " + summary
        return text

    def refresh_projects(self):
        root = APP_DIR / "Projects"
        try:
            self._project_paths = sorted(
                (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            self._project_paths = []
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
            item = QListWidgetItem(self._project_text(p))
            item.setData(Qt.UserRole, str(p))
            item.setToolTip(str(p))
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
        if not git:
            self.git_cache = {}
            return
        paths = list(self._project_paths)

        def work():
            for p in paths:
                if gen != self._git_gen:
                    return
                self.git_info_ready.emit(gen, str(p), git_summary(git, p))

        threading.Thread(target=work, daemon=True).start()

    def _on_git_info(self, gen, path, text):
        if gen != self._git_gen:
            return
        self.git_cache[path] = text
        for i in range(self.project_list.count()):
            item = self.project_list.item(i)
            if item.data(Qt.UserRole) == path:
                item.setText(self._project_text(Path(path)))
                break

    def open_project_in_vscode(self):
        path = self._selected_path(self.project_list)
        if not path:
            self.projects_status.setText("Select a project first.")
            return
        self._record_recent(path)
        self.restart_vscode(path)

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
            if Path(self.project_list.item(i).data(Qt.UserRole)).name == name:
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

    # ---------- scripts ----------

    def refresh_scripts(self):
        root = APP_DIR / "Scripts"
        try:
            self._script_paths = sorted(
                (p for p in root.iterdir()
                 if p.is_file() and p.suffix.lower() in SCRIPT_EXTS),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            self._script_paths = []
        self._render_scripts()

    def _render_scripts(self):
        keep = self._selected_path(self.script_list)
        query = self.script_search.text().strip().lower()
        shown = [p for p in self._script_paths if query in p.name.lower()]

        self.script_list.clear()
        select_row = -1
        for p in shown:
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, str(p))
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
            saved = self.settings.get("script_args").get(Path(current.data(Qt.UserRole)).name, "")
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

    def run_selected_script(self):
        path = self._selected_path(self.script_list)
        if not path:
            self.scripts_status.setText("Select a script first.")
            return
        self._save_script_args()
        error = run_script(path, self.settings, self.script_args_edit.text())
        self.scripts_status.setText(error if error else f"Started {path.name} in a new console window.")

    def _script_running(self):
        return self.script_proc is not None and self.script_proc.state() != QProcess.NotRunning

    def _update_script_buttons(self):
        running = self._script_running()
        self.btn_run_log.setEnabled(not running)
        self.btn_stop_script.setEnabled(running)

    def _log(self, text):
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.insertPlainText(text)
        self.log_view.moveCursor(QTextCursor.End)

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
        self._log(f"\n$ {path.name} {shown_args}".rstrip() + "\n")
        self.script_proc = proc
        proc.start(program, argv)
        self._update_script_buttons()
        self.scripts_status.setText(f"Running {path.name} ...")

    def _script_output(self):
        if self.script_proc is None:
            return
        data = bytes(self.script_proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self._log(data)

    def _script_finished(self, code, _status):
        self._script_output()
        self._log(f"[finished with exit code {code}]\n")
        proc = self.script_proc
        self.script_proc = None
        if proc is not None:
            proc.deleteLater()
        self._update_script_buttons()
        self.scripts_status.setText(f"Script finished with exit code {code}.")

    def _script_error(self, error):
        if error == QProcess.FailedToStart:
            self._log("[failed to start]\n")
            proc = self.script_proc
            self.script_proc = None
            if proc is not None:
                proc.deleteLater()
            self._update_script_buttons()
            self.scripts_status.setText("Failed to start the script.")

    def stop_script(self):
        if not self._script_running():
            return
        pid = self.script_proc.processId()
        if os.name == "nt" and pid:
            # Kill the whole process tree, not just the first process.
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
        else:
            self.script_proc.kill()
        self._log("[stopped]\n")

    def edit_selected_script(self):
        path = self._selected_path(self.script_list)
        if not path:
            self.scripts_status.setText("Select a script first.")
            return
        self.restart_vscode(path)

    # ---------- tools ----------

    def _tools(self):
        return [
            t for t in self.settings.get("tools")
            if isinstance(t, dict) and t.get("name") and t.get("path")
        ]

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

    def _reset_size(self):
        self.showNormal()
        self.resize(1100, 650)
        self.settings.set("window_size", [1100, 650])

    def _restore_size(self):
        w, h = 1100, 650
        if self.settings.get("remember_size"):
            size = self.settings.get("window_size")
            try:
                w, h = int(size[0]), int(size[1])
            except (TypeError, ValueError, IndexError):
                pass
        self.resize(max(w, 800), max(h, 500))

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
            self.hotkey_status.setStyleSheet("color: #6ee7b7;")
        elif state == "error":
            self.hotkey_status.setStyleSheet("color: #fda4af;")
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
            self._set_hotkey_label("error", f"Hotkey error: {e}")

    def _unregister_hotkey(self):
        if os.name == "nt" and self._hotkey_registered and self._user32 is not None:
            try:
                self._user32.UnregisterHotKey(wintypes.HWND(int(self.winId())), HOTKEY_ID)
            except Exception:
                pass
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
                pass
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
        for i in range(self.tool_tabs.count()):
            w = self.tool_tabs.widget(i)
            if isinstance(w, EmbeddedWindow) and w.is_running():
                return True
        return self._script_running()

    def closeEvent(self, event):
        if self.settings.get("confirm_close") and self._anything_running():
            answer = QMessageBox.question(
                self, "Close PocketTerm",
                "The terminal, VS Code, a tool or a script is still running.\n"
                "Closing PocketTerm will stop them. Close anyway?",
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return

        if self.settings.get("remember_size") and not self.isMaximized():
            self.settings.data["window_size"] = [self.width(), self.height()]
        self.settings.save()

        self._unregister_hotkey()
        if self.tray:
            self.tray.hide()
        if self._script_running():
            self.stop_script()

        self.terminals.shutdown_all()
        self.vscode.shutdown()
        for i in range(self.tool_tabs.count()):
            w = self.tool_tabs.widget(i)
            if isinstance(w, EmbeddedWindow):
                w.shutdown()

        super().closeEvent(event)
        QApplication.quit()


if __name__ == "__main__":
    # Without this, Windows groups the app under python.exe and the taskbar
    # shows the Python icon. Must run before any window is created.
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PocketTerm.Launcher")
        except Exception:
            pass

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

    window = MainWindow()
    if app_icon:
        window.setWindowIcon(app_icon)
    window.show()
    window.finish_setup()
    sys.exit(app.exec())
