"""Windows system tray icon + menu -- secondary/background management, not
the primary interface (spec: tray should be secondary once the controller
can run headless via HIDE & PLAY)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from motiondrive.controller_state import ControllerState

_STATUS_TEXT = {
    ControllerState.OFF: "\U0001F534 Controller Off",
    ControllerState.RUNNING: "\U0001F7E2 Controller Running",
    ControllerState.PAUSED: "\U0001F7E1 Controller Paused",
}


class TrayController(QObject):
    openRequested = Signal()
    startRequested = Signal()
    stopRequested = Signal()
    pauseResumeRequested = Signal()
    showOverlayRequested = Signal()
    hideOverlayRequested = Signal()
    settingsRequested = Signal()
    exitRequested = Signal()

    def __init__(self, icon: QIcon, parent=None):
        super().__init__(parent)
        self.tray = QSystemTrayIcon(icon, parent)
        self.tray.setToolTip("MotionDrive")

        menu = QMenu()
        self._status_action = menu.addAction(_STATUS_TEXT[ControllerState.OFF])
        self._status_action.setEnabled(False)
        menu.addSeparator()

        open_action = menu.addAction("Open MotionDrive")
        open_action.triggered.connect(self.openRequested.emit)
        menu.addSeparator()

        start_action = menu.addAction("Start Controller")
        start_action.triggered.connect(self.startRequested.emit)
        pause_action = menu.addAction("Pause Controller")
        pause_action.triggered.connect(self.pauseResumeRequested.emit)
        stop_action = menu.addAction("Stop Controller")
        stop_action.triggered.connect(self.stopRequested.emit)
        menu.addSeparator()

        show_overlay_action = menu.addAction("Show Camera Overlay")
        show_overlay_action.triggered.connect(self.showOverlayRequested.emit)
        hide_overlay_action = menu.addAction("Hide Camera Overlay")
        hide_overlay_action.triggered.connect(self.hideOverlayRequested.emit)
        menu.addSeparator()

        settings_action = menu.addAction("Settings")
        settings_action.triggered.connect(self.settingsRequested.emit)
        menu.addSeparator()

        exit_action = menu.addAction("Exit MotionDrive")
        exit_action.triggered.connect(self.exitRequested.emit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.openRequested.emit()

    def set_controller_state(self, state: ControllerState) -> None:
        self._status_action.setText(_STATUS_TEXT.get(state, _STATUS_TEXT[ControllerState.OFF]))

    def show(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def notify(self, title: str, message: str) -> None:
        if self.tray.isVisible():
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 3000)
