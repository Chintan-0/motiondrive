"""Friendly, non-technical error surfaces. No stack traces or console text
ever reach the user -- those go to the log file only. Rounded dark card
matching _CloseDialog's visual language rather than a stock QMessageBox."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                 QPushButton, QFrame)

from motiondrive.logging_ import get_logger
from motiondrive.paths import LOGS_DIR

log = get_logger(__name__)


class _FriendlyErrorDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str, body: str):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(440)
        self.retry = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        card = QFrame()
        card.setObjectName("errorCard")
        # Scoped to #errorCard specifically -- a bare `QFrame { ... }`
        # type-selector stylesheet leaks its `border` onto every child
        # widget inside the frame too (icon/title/body labels below),
        # confirmed by directly rendering this dialog in isolation; same
        # fix already applied to _CloseDialog in main_window.py.
        card.setStyleSheet("""
            QFrame#errorCard {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 16px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        icon_row = QHBoxLayout()
        icon = QLabel("⚠")
        icon.setStyleSheet("font-size: 20px; color: #f5c542;")
        icon_row.addWidget(icon)
        title_lbl = QLabel(title)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #e6edf3;")
        icon_row.addWidget(title_lbl, stretch=1)
        layout.addLayout(icon_row)

        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet("font-size: 13px; color: #8b96a5;")
        layout.addWidget(body_lbl)

        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #21262d;
                border: 1px solid #30363d;
                color: #c9d1d9;
                padding: 8px 16px;
                border-radius: 8px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #30363d; }
        """)
        ok_btn.clicked.connect(self.reject)

        retry_btn = QPushButton("TRY AGAIN")
        retry_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b9dff;
                border: none;
                color: #051019;
                padding: 8px 16px;
                border-radius: 8px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #5cb0ff; }
        """)
        retry_btn.clicked.connect(self._on_retry)

        btn_row.addWidget(ok_btn)
        btn_row.addWidget(retry_btn)
        layout.addLayout(btn_row)

        outer.addWidget(card)

    def _on_retry(self) -> None:
        self.retry = True
        self.accept()


def show_friendly_error(parent: QWidget, title: str, message: str,
                         tips: list[str] | None = None, exc: Exception | None = None) -> None:
    if exc is not None:
        log.exception("%s: %s", title, message, exc_info=exc)
    else:
        log.error("%s: %s", title, message)

    body = message
    if tips:
        body += "\n\nTry:\n" + "\n".join(f"• {t}" for t in tips)

    dlg = _FriendlyErrorDialog(parent, title, body)
    dlg.exec()


CAMERA_START_TIPS = [
    "Closing other apps using your webcam",
    "Selecting another camera in Settings",
    "Restarting MotionDrive",
]

GAMEPAD_TIPS = [
    "Reinstalling MotionDrive so the ViGEmBus driver installs",
    "Switching to Keyboard mode in Settings",
    "Restarting your PC after installation",
]
