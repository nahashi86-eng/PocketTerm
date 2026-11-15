import os
import sys
import time
import subprocess

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QWindow
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QSpinBox,
)

# ============================================================
# PORTABLE PATHS
# Folder layout:
# Assets/
#   OperatingSystems/
#   PocketVM/qemu/qemu-system-x86_64.exe
#   GitPortable/Data/home/Scripts/PocketTerm_VM.py
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.abspath(
    os.path.join(BASE_DIR, "..", "..", "..", "..")
)

ISO_DIR = os.path.join(ASSETS_DIR, "OperatingSystems")
QEMU_DIR = os.path.join(ASSETS_DIR, "PocketVM", "qemu")
QEMU_EXE = os.path.join(QEMU_DIR, "qemu-system-x86_64.exe")
ICON_PATH = os.path.join(BASE_DIR, "Images", "icon.png")


class VMDisplay(QWidget):
    """Host widget for QEMU's native display window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setStyleSheet(
            "background-color: #000000; border: 1px solid #3B2A66;"
        )
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.window_container = None
        self.foreign_window = None

    def attach_window(self, hwnd):
        if self.window_container is not None:
            return

        try:
            self.foreign_window = QWindow.fromWinId(hwnd)
            if self.foreign_window is None:
                return False

            self.window_container = QWidget.createWindowContainer(
                self.foreign_window, self
            )
            self.window_container.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus
            )
            self.layout.addWidget(self.window_container)
            return True
        except Exception:
            self.foreign_window = None
            self.window_container = None
            return False

    def detach_window(self):
        if self.window_container is not None:
            self.layout.removeWidget(self.window_container)
            self.window_container.deleteLater()
            self.window_container = None
            self.foreign_window = None


class PocketVM(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("PocketTerm Virtual Machine")
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1050, 700)
        self.setMinimumSize(760, 520)

        self.selected_iso = None
        self.qemu_process = None
        self.embed_timer = QTimer(self)
        self.embed_timer.setInterval(300)
        self.embed_timer.timeout.connect(self.find_qemu_window)
        self.started_at = None

        self.build_ui()
        self.refresh_iso_list()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setFixedWidth(215)
        sidebar.setStyleSheet("""
            QFrame {
                background-color: #0E0A18;
                border-right: 2px solid #3B2A66;
            }
        """)

        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 20, 16, 16)
        side.setSpacing(9)

        logo = QLabel("PocketTerm")
        logo.setStyleSheet(
            "color: white; font-size: 25px; font-weight: bold; border: none;"
        )
        subtitle = QLabel("VIRTUAL MACHINE")
        subtitle.setStyleSheet(
            "color: #A78BFA; font-size: 9px; border: none;"
        )
        side.addWidget(logo)
        side.addWidget(subtitle)
        side.addSpacing(22)

        section = QLabel("▾  VM CONTROLS")
        section.setStyleSheet(
            "color: #A78BFA; font-size: 10px; font-weight: bold; border: none;"
        )
        side.addWidget(section)

        self.iso_combo = QComboBox()
        self.iso_combo.setMinimumHeight(34)
        self.iso_combo.setStyleSheet(self.input_style())
        self.iso_combo.currentIndexChanged.connect(self.iso_changed)
        side.addWidget(self.iso_combo)

        browse = QPushButton("Browse for ISO")
        browse.setFixedHeight(36)
        browse.setStyleSheet(self.button_style())
        browse.clicked.connect(self.browse_iso)
        side.addWidget(browse)

        ram_label = QLabel("MEMORY (MB)")
        ram_label.setStyleSheet(
            "color: #B9A6E8; font-size: 10px; border: none;"
        )
        side.addWidget(ram_label)

        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(512, 16384)
        self.ram_spin.setSingleStep(512)
        self.ram_spin.setValue(2048)
        self.ram_spin.setSuffix(" MB")
        self.ram_spin.setStyleSheet(self.input_style())
        side.addWidget(self.ram_spin)

        core_label = QLabel("CPU CORES")
        core_label.setStyleSheet(
            "color: #B9A6E8; font-size: 10px; border: none;"
        )
        side.addWidget(core_label)

        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, max(1, os.cpu_count() or 2))
        self.cpu_spin.setValue(2 if (os.cpu_count() or 2) >= 2 else 1)
        self.cpu_spin.setStyleSheet(self.input_style())
        side.addWidget(self.cpu_spin)

        side.addSpacing(8)

        self.start_button = QPushButton("Start Virtual Machine")
        self.start_button.setFixedHeight(40)
        self.start_button.setStyleSheet(self.button_style())
        self.start_button.clicked.connect(self.start_vm)
        side.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Virtual Machine")
        self.stop_button.setFixedHeight(40)
        self.stop_button.setStyleSheet(self.button_style())
        self.stop_button.clicked.connect(self.stop_vm)
        self.stop_button.setEnabled(False)
        side.addWidget(self.stop_button)

        side.addStretch()

        self.status_label = QLabel("VM ready.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "color: #A78BFA; font-size: 11px; border: none;"
        )
        side.addWidget(self.status_label)

        footer = QLabel("Powered by QEMU")
        footer.setStyleSheet(
            "color: #806AAE; font-size: 9px; border: none;"
        )
        side.addWidget(footer)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 20, 22, 20)
        content_layout.setSpacing(10)

        title = QLabel("Virtual Machine")
        title.setStyleSheet(
            "font-size: 24px; font-weight: bold; color: white;"
        )
        description = QLabel(
            "Boot an ISO from your portable OperatingSystems folder."
        )
        description.setStyleSheet(
            "font-size: 12px; color: #B9A6E8;"
        )

        self.display = VMDisplay()
        self.display.setMinimumSize(480, 320)

        self.display_placeholder = QLabel(
            "VM DISPLAY\\n\\nSelect an ISO and click Start Virtual Machine."
        )
        self.display_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_placeholder.setStyleSheet("""
            color: #806AAE;
            background-color: #000000;
            border: 1px solid #3B2A66;
            font-size: 13px;
        """)
        self.display.layout.addWidget(self.display_placeholder)

        content_layout.addWidget(title)
        content_layout.addWidget(description)
        content_layout.addWidget(self.display, 1)

        root.addWidget(sidebar)
        root.addWidget(content, 1)

        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #08070D;
                color: white;
            }
        """)

    def button_style(self):
        return """
            QPushButton {
                background-color: #7C3AED;
                color: white;
                border: 1px solid #9561FF;
                border-radius: 7px;
                font-size: 11px;
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

    def input_style(self):
        return """
            QComboBox, QSpinBox {
                background-color: #151020;
                color: white;
                border: 1px solid #3B2A66;
                border-radius: 6px;
                padding: 5px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #151020;
                color: white;
                selection-background-color: #1E1535;
            }
        """

    # --------------------------------------------------------
    # ISO selection
    # --------------------------------------------------------

    def refresh_iso_list(self):
        os.makedirs(ISO_DIR, exist_ok=True)
        self.iso_combo.blockSignals(True)
        self.iso_combo.clear()

        iso_files = sorted(
            name for name in os.listdir(ISO_DIR)
            if name.lower().endswith(".iso")
            and os.path.isfile(os.path.join(ISO_DIR, name))
        )

        self.iso_combo.addItem("-- Select an ISO --", None)
        for name in iso_files:
            self.iso_combo.addItem(name, os.path.join(ISO_DIR, name))

        self.iso_combo.blockSignals(False)
        self.selected_iso = None

        if not iso_files:
            self.status_label.setText(
                "No ISO files found in OperatingSystems."
            )

    def iso_changed(self, index):
        self.selected_iso = self.iso_combo.itemData(index)

    def browse_iso(self):
        os.makedirs(ISO_DIR, exist_ok=True)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an ISO image",
            ISO_DIR,
            "ISO Images (*.iso)"
        )
        if not file_path:
            return

        self.selected_iso = file_path
        display_name = os.path.basename(file_path)

        existing_index = self.iso_combo.findData(file_path)
        if existing_index >= 0:
            self.iso_combo.setCurrentIndex(existing_index)
        else:
            self.iso_combo.addItem(display_name, file_path)
            self.iso_combo.setCurrentIndex(self.iso_combo.count() - 1)

    # --------------------------------------------------------
    # QEMU window embedding (Windows)
    # --------------------------------------------------------

    def find_qemu_window(self):
        if self.qemu_process is None or self.qemu_process.poll() is not None:
            self.embed_timer.stop()
            return

        if self.display.window_container is not None:
            self.embed_timer.stop()
            self.status_label.setText("Virtual machine running.")
            return

        if os.name != "nt":
            self.embed_timer.stop()
            self.status_label.setText(
                "Native window embedding is currently supported on Windows."
            )
            return

        try:
            import ctypes
            from ctypes import wintypes

            target_pid = self.qemu_process.pid
            user32 = ctypes.windll.user32
            found_hwnds = []

            EnumWindowsProc = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )

            def enum_callback(hwnd, lparam):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(
                    hwnd, ctypes.byref(pid)
                )
                if pid.value == target_pid and user32.IsWindowVisible(hwnd):
                    found_hwnds.append(hwnd)
                return True

            callback = EnumWindowsProc(enum_callback)
            user32.EnumWindows(callback, 0)

            if not found_hwnds:
                self.status_label.setText(
                    "QEMU started; waiting for its display window..."
                )
                return

            hwnd = found_hwnds[0]
            if self.display.layout.count():
                item = self.display.layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

            if self.display.attach_window(hwnd):
                self.embed_timer.stop()
                self.status_label.setText("Virtual machine running.")
            else:
                self.status_label.setText(
                    "QEMU is running, but its window could not be embedded."
                )

        except Exception as error:
            self.embed_timer.stop()
            self.status_label.setText("Could not embed QEMU window.")
            QMessageBox.warning(
                self,
                "Window embedding error",
                f"QEMU started, but embedding failed:\\n{error}"
            )

    # --------------------------------------------------------
    # Start / stop VM
    # --------------------------------------------------------

    def start_vm(self):
        if self.qemu_process is not None and self.qemu_process.poll() is None:
            return

        if not self.selected_iso:
            QMessageBox.information(
                self, "Select an ISO", "Choose an ISO file first."
            )
            return

        if not os.path.isfile(self.selected_iso):
            QMessageBox.warning(
                self, "ISO not found", "The selected ISO file no longer exists."
            )
            return

        if not os.path.isfile(QEMU_EXE):
            QMessageBox.critical(
                self,
                "QEMU not found",
                "Could not find QEMU at:\\n\\n" + QEMU_EXE
            )
            return

        self.display.detach_window()
        while self.display.layout.count():
            item = self.display.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        args = [
            "-m", str(self.ram_spin.value()),
            "-smp", str(self.cpu_spin.value()),
            "-cdrom", os.path.abspath(self.selected_iso),
            "-boot", "order=d",
            "-display", "sdl",
        ]

        try:
            self.qemu_process = subprocess.Popen(
                [QEMU_EXE] + args,
                cwd=QEMU_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except Exception as error:
            self.qemu_process = None
            self.status_label.setText("Could not start QEMU.")
            QMessageBox.critical(
                self, "QEMU launch failed", str(error)
            )
            return

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.iso_combo.setEnabled(False)
        self.ram_spin.setEnabled(False)
        self.cpu_spin.setEnabled(False)

        self.display_placeholder = QLabel(
            "Starting QEMU...\\nWaiting for the virtual machine display."
        )
        self.display_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_placeholder.setStyleSheet(
            "color: #A78BFA; background-color: #000000; font-size: 12px;"
        )
        self.display.layout.addWidget(self.display_placeholder)

        self.status_label.setText("Launching virtual machine...")
        self.embed_timer.start()

    def stop_vm(self):
        self.embed_timer.stop()

        if self.qemu_process is not None and self.qemu_process.poll() is None:
            try:
                self.qemu_process.terminate()
                try:
                    self.qemu_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.qemu_process.kill()
                    self.qemu_process.wait(timeout=2)
            except Exception:
                pass

        self.qemu_process = None
        self.display.detach_window()

        while self.display.layout.count():
            item = self.display.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.display_placeholder = QLabel(
            "VM DISPLAY\\n\\nSelect an ISO and click Start Virtual Machine."
        )
        self.display_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_placeholder.setStyleSheet(
            "color: #806AAE; background-color: #000000; font-size: 13px;"
        )
        self.display.layout.addWidget(self.display_placeholder)

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.iso_combo.setEnabled(True)
        self.ram_spin.setEnabled(True)
        self.cpu_spin.setEnabled(True)
        self.status_label.setText("Virtual machine stopped.")

    def closeEvent(self, event):
        self.stop_vm()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))

    window = PocketVM()
    window.show()
    sys.exit(app.exec())
