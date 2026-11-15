import sys
import os

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QFileDialog,
    QPlainTextEdit, QFrame
)
from PySide6.QtCore import (
    Qt, QObject, QProcess, QProcessEnvironment, Signal
)
from PySide6.QtGui import QIcon


# =====================================
# CONFIGURATION
# =====================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ICON_PATH = os.path.join(BASE_DIR, "Images", "icon.png")
CPP_DIR = os.path.join(BASE_DIR, "cpp")

# GitPortable is three levels above Scripts:
# Scripts -> home -> Data -> GitPortable -> App/Git/bin/...
GITPORTABLE_ROOT = os.path.abspath(
    os.path.join(BASE_DIR, "..", "..", "..")
)

GIT_BIN_DIR = os.path.join(GITPORTABLE_ROOT, "App", "Git", "bin")
GPP_DIR = os.path.join(GIT_BIN_DIR, "w64devkit", "bin")

# Windows normally resolves G++ as g++.exe; keep an extensionless fallback.
GPP_PATH = os.path.join(GPP_DIR, "g++.exe")
if not os.path.isfile(GPP_PATH):
    GPP_PATH = os.path.join(GPP_DIR, "g++")


# =====================================
# COMPILING LOGIC (no UI code)
# =====================================

class CppCompiler(QObject):
    """Runs G++ directly and reports back through signals."""

    status = Signal(str)     # short status text
    output = Signal(str)     # compiler output text
    cleared = Signal()       # output should be cleared
    busy = Signal(bool)      # True while compiling

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None

    @staticmethod
    def is_inside_cpp_dir(file_path):
        try:
            cpp_real = os.path.realpath(CPP_DIR)
            return os.path.commonpath(
                [os.path.realpath(file_path), cpp_real]
            ) == cpp_real
        except ValueError:
            return False

    def is_running(self):
        return (
            self.process is not None
            and self.process.state() != QProcess.NotRunning
        )

    def compile(self, source_path):

        if self.is_running():
            return

        if not source_path:
            self.status.emit("Select a C++ file first.")
            return

        if not os.path.isfile(source_path):
            self.status.emit("Source file not found.")
            return

        if not os.path.isfile(GPP_PATH):
            self.status.emit("G++ compiler not found!")
            self.cleared.emit()
            self.output.emit(
                "Could not find the portable G++ compiler.\n\n"
                f"Expected compiler: {GPP_PATH}\n"
                f"Compiler folder: {GPP_DIR}\n"
                f"GitPortable root: {GITPORTABLE_ROOT}\n"
            )
            return

        source = os.path.basename(source_path)
        output_file = os.path.splitext(source)[0] + ".exe"

        self.cleared.emit()
        self.output.emit(
            f"Compiler: {GPP_PATH}\n"
            f"Source: {source}\n"
            f"Output: {output_file}\n\n"
            "Starting compilation...\n\n"
        )

        self.status.emit("Compiling...")
        self.busy.emit(True)

        self.process = QProcess(self)
        self.process.setWorkingDirectory(CPP_DIR)
        self.process.setProcessChannelMode(QProcess.MergedChannels)

        # Put the portable toolchain on PATH for G++'s linker and assembler.
        env = QProcessEnvironment.systemEnvironment()
        old_path = env.value("PATH")
        new_path = os.pathsep.join(
            [p for p in (GPP_DIR, GIT_BIN_DIR) if os.path.isdir(p)]
            + ([old_path] if old_path else [])
        )
        env.insert("PATH", new_path)
        self.process.setProcessEnvironment(env)

        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.started.connect(self._started)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

        self.process.start(GPP_PATH, [source, "-o", output_file])

        if not self.process.waitForStarted(3000):
            self.status.emit("Could not start G++!")
            self.output.emit("\n" + self.process.errorString())
            self.busy.emit(False)

    def _started(self):
        self.status.emit("Compiler process started...")
        self.output.emit("G++ started successfully.\n")

    def _read_output(self):
        data = self.process.readAllStandardOutput()
        self.output.emit(bytes(data).decode(errors="replace"))

    def _error(self, error):
        self.busy.emit(False)
        self.status.emit("Compiler process error!")
        self.output.emit(
            "\nPROCESS ERROR: " + self.process.errorString()
        )

    def _finished(self, exit_code, exit_status):
        self.busy.emit(False)

        if (
            exit_code == 0
            and exit_status == QProcess.ExitStatus.NormalExit
        ):
            self.status.emit("Compilation successful!")
            self.output.emit("\nSUCCESS: Executable created.")
        else:
            self.status.emit("Compilation failed.")
            self.output.emit(
                f"\nCompilation exited with code: {exit_code}"
            )


# =====================================
# MAIN WINDOW (GUI)
# =====================================

class PocketApp(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("PocketTerm C++ Code Runner")
        self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(850, 550)

        self.selected_file = None

        self.compiler = CppCompiler(self)

        self.build_interface()

        # Connect the compiler logic to the GUI
        self.compiler.status.connect(self.status.setText)
        self.compiler.output.connect(self.append_output)
        self.compiler.cleared.connect(self.output.clear)
        self.compiler.busy.connect(
            lambda busy: self.compile_button.setEnabled(not busy)
        )

    # =================================
    # INTERFACE
    # =================================

    def build_interface(self):

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # SIDEBAR
        sidebar = QFrame()
        sidebar.setFixedWidth(210)

        sidebar.setStyleSheet("""
            QFrame {
                background-color: #0E0A18;
                border-right: 2px solid #3B2A66;
            }
        """)

        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(15, 20, 15, 15)
        side_layout.setSpacing(8)

        logo = QLabel("PocketTerm")
        logo.setStyleSheet("""
            color: white;
            font-size: 25px;
            font-weight: bold;
            border: none;
        """)

        subtitle = QLabel("DEVELOPER ENVIRONMENT")
        subtitle.setStyleSheet("""
            color: #A78BFA;
            font-size: 9px;
            border: none;
        """)

        side_layout.addWidget(logo)
        side_layout.addWidget(subtitle)
        side_layout.addSpacing(25)

        workspace = QLabel("▾  WORKSPACE")
        workspace.setStyleSheet("""
            color: #A78BFA;
            font-size: 11px;
            font-weight: bold;
            border: none;
        """)

        side_layout.addWidget(workspace)
        side_layout.addSpacing(5)

        self.nav_buttons = []

        self.add_nav_button(side_layout, "Dashboard", 0)
        self.add_nav_button(side_layout, "Compile Code", 1)

        side_layout.addStretch()

        footer = QLabel("PocketTerm C++ Runner")
        footer.setStyleSheet("""
            color: #806AAE;
            font-size: 9px;
            border: none;
        """)

        side_layout.addWidget(footer)

        # CONTENT
        self.pages = QStackedWidget()
        self.pages.addWidget(self.dashboard_page())
        self.pages.addWidget(self.compiler_page())

        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.pages, 1)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #08070D;
            }

            QWidget {
                background-color: #08070D;
                color: white;
            }
        """)

        self.show_page(0)

    # =================================
    # NAVIGATION
    # =================================

    def add_nav_button(self, layout, name, index):

        button = QPushButton(name)
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(42)

        button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #B9A6E8;
                border: none;
                border-radius: 13px;
                text-align: left;
                padding-left: 18px;
                font-size: 14px;
            }

            QPushButton:hover {
                background-color: #1E1535;
            }

            QPushButton:checked {
                background-color: #1E1535;
                color: white;
                border: 1px solid #7C3AED;
                border-left: 4px solid #9561FF;
                font-weight: bold;
            }
        """)

        button.clicked.connect(
            lambda checked=False, i=index: self.show_page(i)
        )

        layout.addWidget(button)
        self.nav_buttons.append(button)

    def show_page(self, index):

        self.pages.setCurrentIndex(index)

        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    # =================================
    # DASHBOARD
    # =================================

    def dashboard_page(self):

        page = QWidget()
        layout = QVBoxLayout(page)

        layout.setContentsMargins(28, 25, 28, 25)
        layout.setSpacing(12)

        title = QLabel("C++ Code Runner")
        title.setStyleSheet("""
            font-size: 25px;
            font-weight: bold;
            color: white;
        """)

        description = QLabel(
            "Portable C++ compilation environment."
        )
        description.setStyleSheet("""
            font-size: 13px;
            color: #B9A6E8;
        """)

        info = QLabel(
            "Select Compile Code from the sidebar "
            "to compile your C++ programs."
        )
        info.setWordWrap(True)
        info.setStyleSheet("""
            background-color: #0E0A18;
            border: 1px solid #3B2A66;
            border-radius: 9px;
            padding: 14px;
            color: #B9A6E8;
            font-size: 12px;
        """)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(12)
        layout.addWidget(info)
        layout.addStretch()

        return page

    # =================================
    # COMPILER PAGE
    # =================================

    def compiler_page(self):

        page = QWidget()
        layout = QVBoxLayout(page)

        layout.setContentsMargins(28, 25, 28, 25)
        layout.setSpacing(12)

        title = QLabel("Compile Code")
        title.setStyleSheet("""
            font-size: 25px;
            font-weight: bold;
            color: white;
        """)

        description = QLabel(
            "Compile C++ source files using portable G++."
        )
        description.setStyleSheet("""
            color: #B9A6E8;
            font-size: 12px;
        """)

        self.file_label = QLabel("No C++ file selected.")
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("""
            background-color: #0E0A18;
            border: 1px solid #3B2A66;
            border-radius: 8px;
            padding: 10px;
            color: #B9A6E8;
            font-size: 12px;
        """)

        browse_button = QPushButton("Select C++ File")
        browse_button.setFixedHeight(36)
        browse_button.clicked.connect(self.select_file)
        browse_button.setStyleSheet(self.button_style())

        self.compile_button = QPushButton("Compile Code")
        self.compile_button.setFixedHeight(38)
        self.compile_button.clicked.connect(self.compile_code)
        self.compile_button.setStyleSheet(self.button_style())

        self.status = QLabel("Compiler ready.")
        self.status.setStyleSheet("""
            color: #A78BFA;
            font-size: 12px;
        """)

        output_title = QLabel("COMPILER OUTPUT")
        output_title.setStyleSheet("""
            color: #A78BFA;
            font-size: 10px;
            font-weight: bold;
        """)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("""
            QPlainTextEdit {
                background-color: #000000;
                color: #B9A6E8;
                border: 1px solid #3B2A66;
                border-radius: 8px;
                padding: 8px;
                font-family: Consolas;
                font-size: 11px;
            }
        """)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(5)
        layout.addWidget(self.file_label)
        layout.addWidget(browse_button)
        layout.addWidget(self.compile_button)
        layout.addWidget(self.status)
        layout.addSpacing(5)
        layout.addWidget(output_title)
        layout.addWidget(self.output, 1)

        return page

    # =================================
    # BUTTON STYLE
    # =================================

    def button_style(self):

        return """
            QPushButton {
                background-color: #7C3AED;
                color: white;
                border: 1px solid #9561FF;
                border-radius: 7px;
                font-size: 12px;
                font-weight: bold;
                padding: 5px;
            }

            QPushButton:hover {
                background-color: #8B5CF6;
                border: 1px solid #B69AFF;
            }

            QPushButton:pressed {
                background-color: #5B21B6;
            }

            QPushButton:disabled {
                background-color: #302344;
                color: #8A7A9E;
                border: 1px solid #3B2A66;
            }
        """

    # =================================
    # ACTIONS
    # =================================

    def append_output(self, text):

        self.output.insertPlainText(text)
        self.output.ensureCursorVisible()

    def select_file(self):

        os.makedirs(CPP_DIR, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select C++ Source File",
            CPP_DIR,
            "C++ Files (*.cpp *.cc *.cxx)"
        )

        if not file_path:
            return

        if not CppCompiler.is_inside_cpp_dir(file_path):
            self.status.setText(
                "Please select a file inside Scripts/cpp."
            )
            return

        self.selected_file = file_path

        self.file_label.setText(os.path.basename(file_path))
        self.status.setText("File selected successfully.")

    def compile_code(self):

        self.compiler.compile(self.selected_file)


# =====================================
# APPLICATION ENTRY POINT
# =====================================

if __name__ == "__main__":

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(ICON_PATH))

    window = PocketApp()
    window.show()

    sys.exit(app.exec())