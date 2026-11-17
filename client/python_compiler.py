import os
import sys
import shlex
import shutil
import subprocess
import importlib.util
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QCheckBox, QComboBox,
    QPlainTextEdit, QMessageBox, QFrame
)

APP_TITLE = "PocketTerm Python Compiler"
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


def discover_portable_root():
    here = Path(__file__).resolve().parent
    for folder in (here, *here.parents):
        if any((folder / rel).is_file() for rel in (
            "GitBashPortable.exe", "App/Git/bin/bash.exe", "App/Git/git-bash.exe"
        )):
            return folder
    for folder in (here, *here.parents):
        candidate = folder / "GitPortable"
        if any((candidate / rel).is_file() for rel in (
            "GitBashPortable.exe", "App/Git/bin/bash.exe", "App/Git/git-bash.exe"
        )):
            return candidate
    return here.parent


PORTABLE_ROOT = discover_portable_root()


def find_portable_file(*relative_paths):
    for rel in relative_paths:
        candidate = PORTABLE_ROOT / rel
        if candidate.is_file():
            return candidate
    return None


def git_bash_paths():
    bash = find_portable_file("App/Git/bin/bash.exe", "App/Git/usr/bin/bash.exe",
                              "App/git/bin/bash.exe", "bin/bash.exe")
    mintty = find_portable_file("App/Git/usr/bin/mintty.exe", "App/Git/bin/mintty.exe",
                                "App/git/usr/bin/mintty.exe")
    return bash, mintty


def qss():
    return f"""
    QWidget {{
        background: {BG}; color: {TEXT};
        font-family: 'Segoe UI'; font-size: 10pt;
    }}
    QMainWindow {{ background: {BG}; }}
    QFrame#Panel {{
        background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
    }}
    QLabel#Title {{ font-size: 20pt; font-weight: 700; color: {TEXT}; }}
    QLabel#Subtitle {{ color: {MUTED}; font-size: 9pt; }}
    QLabel#Section {{ color: {ACCENT_HOVER}; font-size: 10pt; font-weight: 700; }}
    QLabel#Status {{ color: {GREEN}; }}
    QLineEdit, QComboBox, QPlainTextEdit {{
        background: #0c1119; border: 1px solid {BORDER}; border-radius: 6px;
        padding: 8px; selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {ACCENT};
    }}
    QComboBox::drop-down {{ border: 0; width: 24px; }}
    QPushButton {{
        background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 7px;
        padding: 8px 13px; font-weight: 600;
    }}
    QPushButton:hover {{ border-color: {ACCENT}; background: #252d3d; }}
    QPushButton:pressed {{ background: #302448; }}
    QPushButton#Primary {{
        background: {ACCENT}; color: white; border: 1px solid {ACCENT};
    }}
    QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
    QCheckBox {{ spacing: 8px; color: {TEXT}; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {BORDER}; background: #0c1119;
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
    QScrollBar:vertical {{ background: {BG}; width: 9px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 20px; }}
    """


class PythonCompiler(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(760, 650)
        self.setMinimumSize(620, 540)
        self.bash_path, self.mintty_path = git_bash_paths()
        self.embedded_terminal = None
        self._build_ui()
        self._load_embedded_terminal()
        self._refresh_terminal_status()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)

        heading = QVBoxLayout()
        title = QLabel("Python Compiler")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Build standalone Windows applications with PyInstaller through GitPortable.")
        subtitle.setObjectName("Description")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        outer.addLayout(heading)

        panel = QFrame()
        panel.setObjectName("Card")
        form = QGridLayout(panel)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(12)

        section = QLabel("BUILD CONFIGURATION")
        section.setObjectName("SectionTitle")
        form.addWidget(section, 0, 0, 1, 3)

        form.addWidget(QLabel("Python file"), 1, 0)
        self.script_edit = QLineEdit()
        self.script_edit.setPlaceholderText("Choose the .py file to compile")
        form.addWidget(self.script_edit, 1, 1)
        browse_script = QPushButton("Browse")
        browse_script.clicked.connect(self._browse_script)
        form.addWidget(browse_script, 1, 2)

        form.addWidget(QLabel("Application name"), 2, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Use the script filename")
        form.addWidget(self.name_edit, 2, 1, 1, 2)

        form.addWidget(QLabel("Output folder"), 3, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Default: create build files beside the script")
        form.addWidget(self.output_edit, 3, 1)
        browse_output = QPushButton("Browse")
        browse_output.clicked.connect(self._browse_output)
        form.addWidget(browse_output, 3, 2)

        form.addWidget(QLabel("Build mode"), 4, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("One file (.exe)", "onefile")
        self.mode_combo.addItem("One folder (faster startup)", "onedir")
        form.addWidget(self.mode_combo, 4, 1, 1, 2)

        self.windowed_check = QCheckBox("Windowed app (hide the console window)")
        self.windowed_check.setChecked(True)
        self.clean_check = QCheckBox("Clean previous build files before compiling")
        form.addWidget(self.windowed_check, 5, 0, 1, 3)
        form.addWidget(self.clean_check, 6, 0, 1, 3)
        outer.addWidget(panel)

        actions = QHBoxLayout()
        self.compile_button = QPushButton("Compile with PyInstaller")
        self.compile_button.setObjectName("Primary")
        self.compile_button.clicked.connect(self._compile)
        actions.addWidget(self.compile_button)
        clear_button = QPushButton("Clear fields")
        clear_button.clicked.connect(self._clear_fields)
        actions.addWidget(clear_button)
        actions.addStretch()
        outer.addLayout(actions)

        self.status = QLabel("Ready.")
        self.status.setObjectName("Status")
        outer.addWidget(self.status)

        log_panel = QFrame()
        log_panel.setObjectName("Card")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(14, 12, 14, 14)
        log_head = QHBoxLayout()
        log_title = QLabel("TERMINAL OUTPUT")
        log_title.setObjectName("SectionTitle")
        log_head.addWidget(log_title)
        log_head.addStretch()
        clear_log = QPushButton("Clear")
        clear_log.clicked.connect(lambda: self.log_view.clear())
        log_head.addWidget(clear_log)
        log_layout.addLayout(log_head)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1500)
        self.log_view.setPlaceholderText("Terminal setup messages will appear here.")
        log_layout.addWidget(self.log_view, 1)
        outer.addWidget(log_panel)

        terminal_panel = QFrame()
        terminal_panel.setObjectName("Card")
        terminal_layout = QVBoxLayout(terminal_panel)
        terminal_layout.setContentsMargins(12, 10, 12, 12)
        terminal_header = QHBoxLayout()
        terminal_title = QLabel("EMBEDDED GITPORTABLE TERMINAL")
        terminal_title.setObjectName("SectionTitle")
        terminal_header.addWidget(terminal_title)
        terminal_header.addStretch()
        self.start_terminal_button = QPushButton("Start terminal")
        self.start_terminal_button.clicked.connect(self._start_embedded_terminal)
        terminal_header.addWidget(self.start_terminal_button)
        terminal_layout.addLayout(terminal_header)
        self.terminal_host_layout = QVBoxLayout()
        self.terminal_host_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.addLayout(self.terminal_host_layout, 1)
        outer.addWidget(terminal_panel, 2)

        foot = QLabel("The GitPortable Bash terminal is embedded directly in this window. Compilation output appears there.")
        foot.setObjectName("Subtitle")
        foot.setWordWrap(True)
        outer.addWidget(foot)

    @staticmethod
    def _find_main_app_file():
        """Find PocketTerm's main source even when the user renamed the main script."""
        here = Path(__file__).resolve().parent
        names = (
            "PocketTerm.py",
            "PocketTerm_Enhanced.py",
            "PocketTerm_Main.py",
            "PocketTermLauncher.py",
        )
        search_dirs = []
        for folder in (here, *here.parents):
            if folder not in search_dirs:
                search_dirs.append(folder)
        for folder in search_dirs:
            for name in names:
                candidate = folder / name
                if candidate.is_file() and candidate.resolve() != Path(__file__).resolve():
                    return candidate
            # Also accept a renamed PocketTerm main file, but not arbitrary scripts.
            matches = sorted(folder.glob("PocketTerm*.py"))
            for candidate in matches:
                if candidate.is_file() and candidate.resolve() != Path(__file__).resolve():
                    return candidate
        return None

    def _load_embedded_terminal(self):
        """Reuse PocketTerm's native Windows terminal-embedding implementation."""
        main_file = self._find_main_app_file()
        if os.name != "nt":
            self.log_view.appendPlainText("Embedded Git Bash window hosting is supported on Windows only.")
            return
        if not main_file.is_file():
            self.log_view.appendPlainText(
                "PocketTerm main source was not found. Looked for PocketTerm.py, "
                "PocketTerm_Enhanced.py and other PocketTerm*.py files in the Scripts folder "
                "and its parent folders."
            )
            return
        try:
            spec = importlib.util.spec_from_file_location("pocketterm_embedding_runtime", main_file)
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not load PocketTerm's terminal embedding module.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Reuse the exact icon lookup used by PocketTerm's main window.
            icon_path, icon_status = module.find_icon()
            if icon_path:
                main_icon = QIcon(str(icon_path))
                if not main_icon.isNull():
                    self.setWindowIcon(main_icon)
                    app = QApplication.instance()
                    if app is not None:
                        app.setWindowIcon(main_icon)
                    self.log_view.appendPlainText(f"PocketTerm icon loaded: {icon_path}")
                else:
                    self.log_view.appendPlainText(f"PocketTerm icon could not be read: {icon_path}")
            else:
                self.log_view.appendPlainText(f"PocketTerm icon not found. {icon_status}")

            # Use the exact stylesheet and accent palette used by the main PocketTerm app.
            # Settings() reads the same Config/settings.json, including a custom accent.
            theme_settings = module.Settings(module.SETTINGS_FILE)
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(module.build_style(theme_settings.data))

            settings = dict(module.Settings.DEFAULTS)
            settings["terminal_direct_launch"] = True
            self.embedded_terminal = module.EmbeddedGitBash(settings, self)
            self.terminal_host_layout.addWidget(self.embedded_terminal)
            self.embedded_terminal.setMinimumHeight(210)
            self._start_embedded_terminal()
        except Exception as exc:
            self.embedded_terminal = None
            self.log_view.appendPlainText(f"Could not initialize embedded terminal: {exc}")

    def _start_embedded_terminal(self):
        if self.embedded_terminal is None:
            self.status.setText("Embedded terminal is unavailable. Check PocketTerm.py is beside this file.")
            self.status.setStyleSheet(f"color: {RED};")
            return
        target = str(Path(__file__).resolve().parent)
        self.embedded_terminal.start(target)
        self.status.setText("Starting embedded GitPortable terminal...")
        self.status.setStyleSheet(f"color: {YELLOW};")

    def closeEvent(self, event):
        if self.embedded_terminal is not None:
            self.embedded_terminal.shutdown()
        super().closeEvent(event)

    def _refresh_terminal_status(self):
        if self.bash_path:
            self.log_view.appendPlainText(f"GitPortable Bash: {self.bash_path}")
            if self.mintty_path:
                self.log_view.appendPlainText(f"Terminal: {self.mintty_path}")
        else:
            self.status.setText("GitPortable Bash was not found. Check the USB folder layout.")
            self.status.setStyleSheet(f"color: {RED};")
            self.log_view.appendPlainText("Expected App/Git/bin/bash.exe below the portable root.")

    def _browse_script(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select Python file", str(PORTABLE_ROOT), "Python files (*.py)"
        )
        if filename:
            self.script_edit.setText(filename)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(filename).stem)

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", str(PORTABLE_ROOT))
        if folder:
            self.output_edit.setText(folder)

    def _clear_fields(self):
        self.script_edit.clear()
        self.name_edit.clear()
        self.output_edit.clear()
        self.status.setText("Ready.")
        self.status.setStyleSheet("")

    def _compile(self):
        if not self.bash_path:
            QMessageBox.critical(self, "GitPortable not found",
                                 "Could not locate GitPortable Bash. Check that this app is inside your PocketTerm USB setup.")
            return

        script_text = self.script_edit.text().strip().strip('"')
        if not script_text:
            QMessageBox.warning(self, "No Python file", "Choose a .py file first.")
            return
        script = Path(script_text).expanduser()
        if not script.is_file() or script.suffix.lower() != ".py":
            QMessageBox.warning(self, "Invalid Python file", "Select an existing .py file.")
            return

        app_name = self.name_edit.text().strip() or script.stem
        if any(ch in app_name for ch in '\\/:*?"<>|'):
            QMessageBox.warning(self, "Invalid name", "Use a filename without Windows path characters.")
            return

        output_text = self.output_edit.text().strip().strip('"')
        output_dir = Path(output_text).expanduser() if output_text else script.parent
        if not output_dir.is_dir():
            QMessageBox.warning(self, "Invalid output folder", "Choose an existing output folder.")
            return

        args = ["--noconfirm", "--name", app_name,
                "--onefile" if self.mode_combo.currentData() == "onefile" else "--onedir"]
        if self.windowed_check.isChecked():
            args.append("--windowed")
        else:
            args.append("--console")
        if self.clean_check.isChecked():
            args.append("--clean")
        args.extend(["--distpath", str(output_dir / "dist"),
                     "--workpath", str(output_dir / "build"),
                     "--specpath", str(output_dir),
                     str(script.resolve())])

        # Send the build command to the already-embedded GitPortable Bash session.
        command = "python -m PyInstaller " + " ".join(shlex.quote(a) for a in args)
        command += "; result=$?; echo; if [ $result -eq 0 ]; then "
        command += "echo 'BUILD SUCCEEDED'; else echo \"BUILD FAILED (exit code $result)\"; fi"

        if self.embedded_terminal is None:
            QMessageBox.critical(
                self, "Embedded terminal unavailable",
                "The embedded GitPortable terminal could not be initialized. Make sure PocketTerm.py is in the same folder as python_compiler.py."
            )
            return
        if not self.embedded_terminal.hwnd:
            self._start_embedded_terminal()
            self.status.setText("Wait for the embedded terminal to appear, then click Compile again.")
            self.status.setStyleSheet(f"color: {YELLOW};")
            return

        if not self.embedded_terminal.send_text(command + "\n"):
            self.status.setText("Could not send the build command to GitPortable terminal.")
            self.status.setStyleSheet(f"color: {RED};")
            return

        self.embedded_terminal.focus_soon()
        self.status.setText("Build command sent. Watch the embedded terminal for progress and errors.")
        self.status.setStyleSheet(f"color: {GREEN};")
        self.log_view.appendPlainText(f"Build started for: {script}")
        self.log_view.appendPlainText("Output folder: " + str(output_dir / "dist"))


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PocketTerm.Launcher")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")
    window = PythonCompiler()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
