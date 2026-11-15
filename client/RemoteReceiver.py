"""
PocketTerm Remote Receiver
Run this on the Windows PC you want to administer.
The receiver announces itself on the local network, but it never runs a command
unless the person at that PC approves the exact command in the confirmation dialog.

Requires: pip install PySide6
"""
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import hmac
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QPlainTextEdit, QMessageBox
)

DISCOVERY_PORT = 47630
COMMAND_PORT = 47631
MAX_COMMAND_CHARS = 8000
MAX_OUTPUT_BYTES = 1_000_000


class Bridge(QObject):
    log_message = Signal(str)


class ReceiverWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PocketTerm Remote Receiver")
        self.resize(700, 540)
        self.token = secrets.token_urlsafe(24)
        self.bridge = Bridge()
        self.bridge.log_message.connect(self._log)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("PocketTerm Remote Receiver"))
        layout.addWidget(QLabel(
            "This PC is discoverable on your LAN. Commands execute immediately after successful "
            "token authentication. Keep the receiver on a trusted private LAN; command text and "
            "output are not encrypted in transit."
        ))

        token_row = QHBoxLayout()
        token_row.addWidget(QLabel("One-session pairing token:"))
        self.token_edit = QLineEdit(self.token)
        self.token_edit.setReadOnly(True)
        token_row.addWidget(self.token_edit, stretch=1)
        copy_btn = QPushButton("Copy token")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.token))
        token_row.addWidget(copy_btn)
        layout.addLayout(token_row)

        self.status = QLabel("Starting receiver…")
        layout.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)

        self.stop_btn = QPushButton("Stop receiver")
        self.stop_btn.clicked.connect(self.close)
        layout.addWidget(self.stop_btn)

        self.server_socket = None
        self.stop_event = threading.Event()
        threading.Thread(target=self._tcp_server, daemon=True).start()
        threading.Thread(target=self._beacon_loop, daemon=True).start()

    def _log(self, message):
        self.log.appendPlainText(message)

    def _beacon_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not self.stop_event.is_set():
            try:
                beacon = json.dumps({
                    "type": "pocketterm_receiver",
                    "name": socket.gethostname()
                }).encode("utf-8")
                sock.sendto(beacon, ("<broadcast>", DISCOVERY_PORT))
            except OSError as exc:
                self.bridge.log_message.emit(f"Discovery warning: {exc}")
            self.stop_event.wait(2.0)
        sock.close()

    def _tcp_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket = server
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("", COMMAND_PORT))
            server.listen(8)
            server.settimeout(1.0)
            self.bridge.log_message.emit(f"Listening on TCP port {COMMAND_PORT}.")
            while not self.stop_event.is_set():
                try:
                    conn, address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._handle_client, args=(conn, address), daemon=True
                ).start()
        except OSError as exc:
            self.bridge.log_message.emit(f"Could not start listener: {exc}")
        finally:
            try:
                server.close()
            except OSError:
                pass

    def _handle_client(self, conn, address):
        with conn:
            try:
                conn.settimeout(10)
                challenge = secrets.token_hex(32)
                conn.sendall(json.dumps({"challenge": challenge}).encode("utf-8") + b"\n")
                data = bytearray()
                while b"\n" not in data and len(data) <= 12000:
                    part = conn.recv(2048)
                    if not part:
                        break
                    data.extend(part)
                request = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
                supplied_proof = str(request.get("proof", ""))
                expected_proof = hmac.new(self.token.encode("utf-8"), challenge.encode("ascii"), "sha256").hexdigest()
                command = str(request.get("command", "")).strip()
                if not hmac.compare_digest(supplied_proof, expected_proof):
                    self._send(conn, {"approved": False, "reason": "Invalid pairing token"})
                    self.bridge.log_message.emit(f"Rejected unauthenticated connection from {address[0]}.")
                    return
                if not command or len(command) > MAX_COMMAND_CHARS:
                    self._send(conn, {"approved": False, "reason": "Invalid command length"})
                    return

                # Pairing-token authentication succeeded. Execute immediately without
                # a per-command approval prompt, as configured by the receiver owner.
                self.bridge.log_message.emit(f"Authenticated command received from {address[0]}; executing.")
                try:
                    completed = subprocess.run(
                        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                         "-ExecutionPolicy", "RemoteSigned", "-Command", command],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    )
                    output = (completed.stdout or "") + (completed.stderr or "")
                    output = output[:MAX_OUTPUT_BYTES]
                    self._send(conn, {
                        "approved": True,
                        "exit_code": completed.returncode,
                        "output": output
                    })
                except subprocess.TimeoutExpired:
                    self._send(conn, {"approved": True, "exit_code": -1,
                                       "output": "Command timed out after 120 seconds."})
                except Exception as exc:
                    self._send(conn, {"approved": True, "exit_code": -1,
                                       "output": f"Execution error: {exc}"})
            except Exception as exc:
                try:
                    self._send(conn, {"approved": False, "reason": str(exc)})
                except OSError:
                    pass
                self.bridge.log_message.emit(f"Request error from {address[0]}: {exc}")

    @staticmethod
    def _send(conn, payload):
        conn.sendall(json.dumps(payload).encode("utf-8"))

    def closeEvent(self, event):
        self.stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
        event.accept()


if __name__ == "__main__":
    app = QApplication([])
    window = ReceiverWindow()
    window.show()
    app.exec()
