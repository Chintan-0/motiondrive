"""First-launch welcome screen + automatic system check."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                 QPushButton, QFrame, QSizePolicy)

from motiondrive.paths import resource_path
from motiondrive.ui.theme import COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_ACCENT, COLOR_TEXT_DIM


class WelcomePage(QWidget):
    getStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(18)

        logo_path = resource_path("assets/icon_256.png")
        if logo_path.exists():
            logo = QLabel()
            logo.setPixmap(QPixmap(str(logo_path)).scaled(
                140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo)

        tagline = QLabel("YOUR HANDS. YOUR WHEEL.")
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setStyleSheet(f"font-size: 13px; font-weight: 700; letter-spacing: 3px; color: {COLOR_ACCENT};")
        layout.addWidget(tagline)

        title = QLabel("How to drive")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 32px; font-weight: 700;")
        subtitle = QLabel("Turn your hands into a steering wheel, accelerator, and brake.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 16px; color: #8b96a5;")

        needs = QLabel(
            "\U0001F450 Hold your invisible wheel\n"
            "↔ Rotate your hands to steer\n\n"
            "\U0001F90F Right-hand pinch\n"
            "Accelerate\n\n"
            "\U0001F90F Left-hand pinch\n"
            "Brake\n\n"
            "ESC\n"
            "Emergency stop"
        )
        needs.setAlignment(Qt.AlignCenter)
        needs.setStyleSheet("font-size: 15px; line-height: 160%;")

        privacy = QLabel("Camera processing happens locally on your PC.\nNo video is ever uploaded or sent anywhere.")
        privacy.setAlignment(Qt.AlignCenter)
        privacy.setStyleSheet(f"font-size: 12px; color: {COLOR_GREEN};")

        btn = QPushButton("GET STARTED")
        btn.setObjectName("primary")
        btn.setFixedWidth(220)
        btn.clicked.connect(self.getStarted.emit)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(10)
        layout.addWidget(needs)
        layout.addSpacing(10)
        layout.addWidget(privacy)
        layout.addSpacing(14)
        layout.addWidget(btn, alignment=Qt.AlignCenter)


class _CheckRow(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self._icon = QLabel("⏳")
        self._icon.setFixedWidth(24)
        self._text = QLabel(label)
        self._text.setStyleSheet("font-size: 14px;")
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        layout.addStretch()

    def set_state(self, ok: bool, detail: str = "") -> None:
        if ok:
            self._icon.setText("✓")
            self._icon.setStyleSheet(f"color: {COLOR_GREEN}; font-weight: 700; font-size: 15px;")
        else:
            self._icon.setText("✗")
            self._icon.setStyleSheet(f"color: {COLOR_RED}; font-weight: 700; font-size: 15px;")
        if detail:
            self._text.setText(detail)


class SystemCheckPage(QWidget):
    continueClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(14)

        title = QLabel("SYSTEM CHECK")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignCenter)

        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(420)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 24, 24, 24)

        self.row_camera = _CheckRow("Checking for a camera...")
        self.row_tracking = _CheckRow("Checking hand tracking...")
        self.row_controller = _CheckRow("Checking controller support...")
        self.row_compat = _CheckRow("Checking Windows compatibility...")
        for r in (self.row_camera, self.row_tracking, self.row_controller, self.row_compat):
            panel_layout.addWidget(r)

        self.status_label = QLabel("Checking your system...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 15px; font-weight: 700; margin-top: 8px;")

        self.continue_btn = QPushButton("CONTINUE")
        self.continue_btn.setObjectName("primary")
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self.continueClicked.emit)

        layout.addWidget(title)
        layout.addWidget(panel, alignment=Qt.AlignCenter)
        layout.addWidget(self.status_label)
        layout.addWidget(self.continue_btn, alignment=Qt.AlignCenter)

    def set_results(self, camera_ok: bool, tracking_ok: bool, controller_ok: bool, compat_ok: bool) -> None:
        self.row_camera.set_state(camera_ok, "Camera detected" if camera_ok else "No camera detected")
        self.row_tracking.set_state(tracking_ok, "Hand tracking ready" if tracking_ok else "Hand tracking unavailable")
        self.row_controller.set_state(controller_ok, "Controller ready" if controller_ok else "Controller unavailable (Keyboard mode will be used)")
        self.row_compat.set_state(compat_ok, "System compatible" if compat_ok else "Unsupported Windows version")

        all_ok = camera_ok and tracking_ok and compat_ok
        self.status_label.setText("READY TO DRIVE" if all_ok else "Some checks failed — you can still continue")
        self.status_label.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {COLOR_GREEN if all_ok else COLOR_YELLOW}; margin-top: 8px;"
        )
        self.continue_btn.setEnabled(compat_ok)
