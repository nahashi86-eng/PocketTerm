import os
import sys
import shutil
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
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
QEMU_SHARE = os.path.join(QEMU_DIR, "share")

# Writable copy of the UEFI variable store (created on first run).
UEFI_VARS_PATH = os.path.join(ASSETS_DIR, "PocketVM", "OVMF_VARS.fd")
# QEMU's own error output goes here so failures are diagnosable.
QEMU_LOG_PATH = os.path.join(ASSETS_DIR, "PocketVM", "qemu_last_run.log")

# Stops Windows from opening a black console window for QEMU.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
ICON_PATH = os.path.join(BASE_DIR, "Images", "icon.png")


def find_uefi_firmware():
    """Return (code_fd, vars_template_fd). Either may be None."""
    code = None
    for name in ("edk2-x86_64-code.fd", "OVMF_CODE.fd", "OVMF.fd"):
        path = os.path.join(QEMU_SHARE, name)
        if os.path.isfile(path):
            code = path
            break

    vars_template = None
    for name in ("edk2-i386-vars.fd", "OVMF_VARS.fd"):
        path = os.path.join(QEMU_SHARE, name)
        if os.path.isfile(path):
            vars_template = path
            break

    return code, vars_template


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
            return True

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
            self.window_container.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
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

    def clear(self):
        """Remove the embedded window and any placeholder label."""
        self.detach_window()
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


class PocketVM(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("PocketTerm Virtual Machine")
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1050, 700)
        self.setMinimumSize(760, 560)

        self.selected_iso = None
        self.qemu_process = None
        self.qemu_log = None
        self.embed_timer = QTimer(self)
        self.embed_timer.setInterval(300)
        self.embed_timer.timeout.connect(self.find_qemu_window)

        self.build_ui()
        self.refresh_iso_list()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def make_label(self, text, style):
        label = QLabel(text)
        label.setStyleSheet(style + " border: none; background: transparent;")
        return label

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar: a fixed-width frame holding a scroll area, so controls
        # never overlap when the window (or an embedding host) is short.
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        sidebar.setStyleSheet("""
            QFrame#sidebar {
                background-color: #0E0A18;
                border-right: 2px solid #3B2A66;
            }
        """)
        sidebar_outer = QVBoxLayout(sidebar)
        sidebar_outer.setContentsMargins(0, 0, 0, 0)
        sidebar_outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        sidebar_outer.addWidget(scroll)

        inner = QWidget()
        inner.setObjectName("sideInner")
        inner.setStyleSheet("QWidget#sideInner { background-color: #0E0A18; }")
        scroll.setWidget(inner)

        side = QVBoxLayout(inner)
        side.setContentsMargins(16, 20, 16, 16)
        side.setSpacing(8)

        side.addWidget(self.make_label(
            "PocketTerm", "color: white; font-size: 25px; font-weight: bold;"
        ))
        side.addWidget(self.make_label(
            "VIRTUAL MACHINE", "color: #A78BFA; font-size: 9px;"
        ))
        side.addSpacing(18)

        side.addWidget(self.make_label(
            "▾  VM CONTROLS",
            "color: #A78BFA; font-size: 10px; font-weight: bold;"
        ))

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

        side.addSpacing(6)
        side.addWidget(self.make_label(
            "MEMORY (MB)", "color: #B9A6E8; font-size: 10px;"
        ))

        self.ram_spin = QSpinBox()
        self.ram_spin.setMinimumHeight(34)
        self.ram_spin.setRange(512, 16384)
        self.ram_spin.setSingleStep(512)
        self.ram_spin.setValue(4096)
        self.ram_spin.setSuffix(" MB")
        self.ram_spin.setStyleSheet(self.input_style())
        side.addWidget(self.ram_spin)

        side.addSpacing(4)
        side.addWidget(self.make_label(
            "CPU CORES", "color: #B9A6E8; font-size: 10px;"
        ))

        cpu_count = os.cpu_count() or 2
        self.cpu_spin = QSpinBox()
        self.cpu_spin.setMinimumHeight(34)
        self.cpu_spin.setRange(1, max(1, cpu_count))
        self.cpu_spin.setValue(min(4, cpu_count))
        self.cpu_spin.setStyleSheet(self.input_style())
        side.addWidget(self.cpu_spin)

        side.addSpacing(4)
        side.addWidget(self.make_label(
            "BOOT MODE", "color: #B9A6E8; font-size: 10px;"
        ))
        self.boot_combo = QComboBox()
        self.boot_combo.setMinimumHeight(34)
        self.boot_combo.setStyleSheet(self.input_style())
        self.boot_combo.addItem("UEFI (modern ISOs)", "uefi")
        self.boot_combo.addItem("Legacy BIOS (Puppy, older)", "bios")
        side.addWidget(self.boot_combo)

        side.addSpacing(4)
        side.addWidget(self.make_label(
            "GRAPHICS", "color: #B9A6E8; font-size: 10px;"
        ))
        self.gpu_combo = QComboBox()
        self.gpu_combo.setMinimumHeight(34)
        self.gpu_combo.setStyleSheet(self.input_style())
        self.gpu_combo.addItem("Standard VGA", "std")
        self.gpu_combo.addItem("VMware SVGA", "vmware")
        self.gpu_combo.addItem("Cirrus (very old)", "cirrus")
        self.gpu_combo.addItem("VirtIO", "virtio")
        side.addWidget(self.gpu_combo)

        side.addSpacing(12)

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

        side.addSpacing(10)
        side.addStretch()

        self.status_label = self.make_label(
            "VM ready.", "color: #A78BFA; font-size: 11px;"
        )
        self.status_label.setWordWrap(True)
        side.addWidget(self.status_label)

        side.addWidget(self.make_label(
            "Powered by QEMU", "color: #806AAE; font-size: 9px;"
        ))

        # Content area
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
        description.setStyleSheet("font-size: 12px; color: #B9A6E8;")

        self.display = VMDisplay()
        self.display.setMinimumSize(480, 320)
        self.show_placeholder(
            "VM DISPLAY\n\nSelect an ISO and click Start Virtual Machine.",
            "#806AAE", 13,
        )

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

    def show_placeholder(self, text, color, size):
        self.display.clear()
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"color: {color}; background-color: #000000; "
            f"border: none; font-size: {size}px;"
        )
        self.display.layout.addWidget(label)

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
        # Puppy Linux ISOs usually behave best with legacy BIOS boot.
        if self.selected_iso and "puppy" in os.path.basename(
            self.selected_iso
        ).lower():
            self.boot_combo.setCurrentIndex(self.boot_combo.findData("bios"))

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
        if self.qemu_process is None:
            self.embed_timer.stop()
            return

        if self.qemu_process.poll() is not None:
            # QEMU died early: tell the user why instead of failing silently.
            self.embed_timer.stop()
            self.report_qemu_exit()
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
            self.display.clear()

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
                f"QEMU started, but embedding failed:\n{error}"
            )

    def report_qemu_exit(self):
        code = self.qemu_process.returncode if self.qemu_process else None
        details = ""
        try:
            if self.qemu_log:
                self.qemu_log.flush()
            with open(QEMU_LOG_PATH, "r", errors="replace") as fh:
                details = fh.read().strip()[-1500:]
        except Exception:
            pass

        self.finish_vm_ui("Virtual machine exited.")
        QMessageBox.warning(
            self,
            "QEMU stopped",
            f"QEMU exited with code {code}.\n\n{details or 'No output.'}"
        )

    # --------------------------------------------------------
    # Start / stop VM
    # --------------------------------------------------------

    def build_qemu_args(self):
        args = [
            "-machine", "q35",
            # Try Windows Hypervisor Platform first, fall back to software.
            "-accel", "whpx,kernel-irqchip=off",
            "-accel", "tcg",
            "-m", str(self.ram_spin.value()),
            "-smp", str(self.cpu_spin.value()),
            "-drive",
            f"file={os.path.abspath(self.selected_iso)},media=cdrom,"
            "readonly=on,if=none,id=cd0",
            "-device", "ide-cd,drive=cd0,bootindex=0",
            "-vga", self.gpu_combo.currentData(),
            "-usb",
            "-device", "usb-tablet",
            "-display", "sdl",
        ]

        if self.boot_combo.currentData() == "bios":
            return args, True

        code, vars_template = find_uefi_firmware()
        if code is None:
            return args, False

        if vars_template is not None:
            if not os.path.isfile(UEFI_VARS_PATH):
                os.makedirs(os.path.dirname(UEFI_VARS_PATH), exist_ok=True)
                shutil.copyfile(vars_template, UEFI_VARS_PATH)
            args += [
                "-drive",
                f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
                "-drive",
                f"if=pflash,format=raw,unit=1,file={UEFI_VARS_PATH}",
            ]
        else:
            args += ["-bios", code]

        return args, True

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
                "Could not find QEMU at:\n\n" + QEMU_EXE
            )
            return

        args, has_uefi = self.build_qemu_args()
        if not has_uefi:
            answer = QMessageBox.question(
                self,
                "UEFI firmware not found",
                "No UEFI firmware (edk2-x86_64-code.fd) was found in:\n\n"
                f"{QEMU_SHARE}\n\n"
                "UEFI-only ISOs such as Bazzite will show 'No bootable "
                "device' with the legacy BIOS. Start anyway?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.display.clear()

        try:
            os.makedirs(os.path.dirname(QEMU_LOG_PATH), exist_ok=True)
            self.qemu_log = open(QEMU_LOG_PATH, "w")
            self.qemu_process = subprocess.Popen(
                [QEMU_EXE] + args,
                cwd=QEMU_DIR,
                stdin=subprocess.DEVNULL,
                stdout=self.qemu_log,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as error:
            self.qemu_process = None
            self.status_label.setText("Could not start QEMU.")
            self.show_placeholder(
                "VM DISPLAY\n\nSelect an ISO and click Start Virtual Machine.",
                "#806AAE", 13,
            )
            QMessageBox.critical(
                self, "QEMU launch failed", str(error)
            )
            return

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.iso_combo.setEnabled(False)
        self.ram_spin.setEnabled(False)
        self.cpu_spin.setEnabled(False)
        self.boot_combo.setEnabled(False)
        self.gpu_combo.setEnabled(False)

        self.show_placeholder(
            "Starting QEMU...\nWaiting for the virtual machine display.",
            "#A78BFA", 12,
        )
        self.status_label.setText("Launching virtual machine...")
        self.embed_timer.start()

    def finish_vm_ui(self, message):
        self.embed_timer.stop()
        self.qemu_process = None
        if self.qemu_log:
            try:
                self.qemu_log.close()
            except Exception:
                pass
            self.qemu_log = None

        self.show_placeholder(
            "VM DISPLAY\n\nSelect an ISO and click Start Virtual Machine.",
            "#806AAE", 13,
        )
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.iso_combo.setEnabled(True)
        self.ram_spin.setEnabled(True)
        self.cpu_spin.setEnabled(True)
        self.boot_combo.setEnabled(True)
        self.gpu_combo.setEnabled(True)
        self.status_label.setText(message)

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

        self.finish_vm_ui("Virtual machine stopped.")

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