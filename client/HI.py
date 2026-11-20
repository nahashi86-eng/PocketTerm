import sys
import time
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class UserHubWindow(QMainWindow):

    def __init__(self, username):
        super().__init__()
        self.username = username
        self.setWindowTitle(f"User Hub - {self.username}")
        self.resize(400, 250)

        # Central widget and layout (Qt requires a layout to organize widgets)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(15)

        # Welcome Label
        title_label = QLabel(f"Welcome back, {self.username}!")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # Status Button
        status_btn = QPushButton("Check Status")
        status_btn.clicked.connect(self.check_status)
        layout.addWidget(status_btn)

        # Quit Button
        quit_btn = QPushButton("Exit Hub")
        quit_btn.clicked.connect(self.close)
        layout.addWidget(quit_btn)

    def check_status(self):
        QMessageBox.information(
            self, "Status", f"Logged in as {self.username}"
        )


class AdminHubWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Admin Hub - Chronos")
        self.resize(450, 300)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(15)

        # Header
        header = QLabel("Admin Control Panel")
        header.setFont(QFont("Arial", 16, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        status_label = QLabel("System Status: Online")
        status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(status_label)

        # Close Button
        quit_btn = QPushButton("Close Admin Panel")
        quit_btn.clicked.connect(self.close)
        layout.addWidget(quit_btn)


def start_app():
    print("Hello, please enter a Username:")
    username = input().strip()

    if username == "admin":
        print("Welcome admin!")
        time.sleep(1)
        verify_admin()
    else:
        print(f"Welcome {username}")
        time.sleep(1)
        print("Starting User Hub...")
        time.sleep(1)
        launch_gui("user", username)


def verify_admin():
    print("Enter admin password:")
    password = input().strip()

    if password == "chronos":
        print("Correct Password, Starting Admin Hub...")
        time.sleep(1)
        launch_gui("admin")
    else:
        print("Wrong Password, Quitting...")
        time.sleep(1.5)
        sys.exit()


def launch_gui(role, username=""):
    # Every PySide6 app needs exactly one QApplication instance
    app = QApplication(sys.argv)

    if role == "admin":
        window = AdminHubWindow()
    else:
        window = UserHubWindow(username)

    window.show()
    # Start the Qt event loop (replaces root.mainloop())
    sys.exit(app.exec())


if __name__ == "__main__":
    start_app()
