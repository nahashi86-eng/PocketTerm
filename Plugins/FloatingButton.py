"""Floating Button - a PocketTerm plugin.

Put this file in the Plugins folder, open the Plugins page, press Scan, select it and press Enable.
A small round button floats above your other windows:
  * click it            -> brings the PocketTerm window to the front
  * drag it             -> move it anywhere on screen
  * right-click it      -> menu to hide it or bring PocketTerm forward

Two buttons appear under "Plugin actions": "Toggle floating button" and "Reset button position".
"""
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QPushButton

SIZE = 56
MARGIN = 24
DRAG_THRESHOLD = 4        # pixels the mouse must move before a click becomes a drag

_button = None


# =====================================
# FLOATING BUTTON
# =====================================

def _show_main_window():
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QMainWindow):
            if widget.isMinimized():
                widget.showNormal()
            widget.show()
            widget.raise_()
            widget.activateWindow()
            return True
    return False


class FloatingButton(QPushButton):

    def __init__(self, app):
        super().__init__("P")
        self._app = app
        self._press_global = None
        self._press_window_pos = None
        self._dragged = False

        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(SIZE, SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("PocketTerm (drag to move, right-click for menu)")
        self.setFocusPolicy(Qt.NoFocus)

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: #7C3AED;
                color: white;
                border: 2px solid #9561FF;
                border-radius: {SIZE // 2}px;
                font-size: 22px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #8B5CF6;
                border: 2px solid #B69AFF;
            }}
            QPushButton:pressed {{
                background-color: #5B21B6;
            }}
        """)

        self.reset_position()

    def reset_position(self):
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        if area is None:
            self.move(100, 100)
            return
        self.move(area.right() - SIZE - MARGIN, area.bottom() - SIZE - MARGIN - 40)

    # ---------- mouse: click vs drag ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_window_pos = self.pos()
            self._dragged = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_global is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragged and delta.manhattanLength() < DRAG_THRESHOLD:
            return
        self._dragged = True
        self.move(self._clamped(self._press_window_pos + delta))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_global is not None:
            was_click = not self._dragged
            self._press_global = None
            self._dragged = False
            if was_click:
                self._on_click()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _clamped(self, pos):
        """Keep the button fully on one of the connected screens."""
        center = pos + QPoint(SIZE // 2, SIZE // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            return pos
        area = screen.availableGeometry()
        x = min(max(pos.x(), area.left()), area.right() - SIZE + 1)
        y = min(max(pos.y(), area.top()), area.bottom() - SIZE + 1)
        return QPoint(x, y)

    # ---------- actions ----------

    def _on_click(self):
        if not _show_main_window():
            self._app.notify("The PocketTerm window could not be found.", "Floating Button")

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Show PocketTerm", self._on_click)
        menu.addAction("Reset position", self.reset_position)
        menu.addSeparator()
        menu.addAction("Hide button", self.hide)
        menu.exec(event.globalPos())


# =====================================
# ACTIONS
# =====================================

def _toggle(app):
    if _button is None:
        return
    if _button.isVisible():
        _button.hide()
        app.log("Floating button hidden")
    else:
        _button.show()
        app.log("Floating button shown")


def _reset(app):
    if _button is not None:
        _button.reset_position()
        _button.show()


# =====================================
# PLUGIN HOOKS
# =====================================

def register(app):
    """Called by PocketTerm when the plugin is enabled."""
    global _button
    _button = FloatingButton(app)
    _button.show()
    app.add_action("Toggle floating button", lambda: _toggle(app), "Show or hide the floating button")
    app.add_action("Reset button position", lambda: _reset(app), "Move the button back to the bottom-right corner")
    app.log("Floating Button plugin registered")


def unregister(app):
    """Remove the floating window; PocketTerm removes the action buttons itself."""
    global _button
    if _button is not None:
        _button.close()
        _button.deleteLater()
        _button = None
    app.log("Floating Button plugin unregistered")
