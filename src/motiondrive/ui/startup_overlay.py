"""Full-window startup ignition overlay -- lives INSIDE MainWindow as a child widget.

Covers MainWindow completely (0,0 -> width, height) from the very first visible
frame, animates content opacity/scale on top of the single top-level window,
and smoothly reveals Home when initialization completes.
"""
from __future__ import annotations

from PySide6.QtCore import (Qt, QTimer, QPropertyAnimation, QSequentialAnimationGroup,
                               QParallelAnimationGroup, QEasingCurve, Signal)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect, QHBoxLayout, QPushButton

from motiondrive.paths import resource_path, LOGS_DIR
from motiondrive.ui.theme import COLOR_BG, COLOR_ACCENT, COLOR_TEXT_DIM
from motiondrive.logging_ import get_logger

log = get_logger(__name__)


def _fade_in(widget: QWidget, duration: int) -> QPropertyAnimation:
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity")
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    return anim


class StartupOverlay(QWidget):
    """Child overlay covering MainWindow's full rect. Emits `finished` when reveal completes."""

    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"background-color: {COLOR_BG}; "
            "background-image: qradialgradient(cx:0.5, cy:0.42, radius:0.7, fx:0.5, fy:0.42, "
            "stop:0 rgba(59,157,255,38), stop:0.55 rgba(59,157,255,10), stop:1 rgba(0,0,0,0));"
        )

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(14)

        # Hero logo artwork
        self._logo_label = QLabel()
        self._logo_label.setAlignment(Qt.AlignCenter)
        logo_path = resource_path("assets/logo.png")
        self._logo_pixmap = QPixmap(str(logo_path)) if logo_path.exists() else QPixmap()
        if not self._logo_pixmap.isNull():
            self._logo_label.setPixmap(
                self._logo_pixmap.scaledToWidth(420, Qt.SmoothTransformation))
        else:
            # Fallback text if image asset is missing
            self._logo_label.setText("MOTIONDRIVE")
            self._logo_label.setStyleSheet("font-size: 28px; font-weight: 800; color: #e6edf3; letter-spacing: 3px;")
        layout.addWidget(self._logo_label, alignment=Qt.AlignCenter)

        self._tagline_label = QLabel("TURN MOTION INTO CONTROL")
        self._tagline_label.setAlignment(Qt.AlignCenter)
        self._tagline_label.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_ACCENT}; letter-spacing: 2px;")
        layout.addWidget(self._tagline_label)

        layout.addSpacing(10)
        self._status_label = QLabel("Initializing MotionDrive...")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet(
            f"font-size: 11px; color: {COLOR_TEXT_DIM}; letter-spacing: 1px;")
        layout.addWidget(self._status_label)

        self._status_timers: list[QTimer] = []
        self._sequence: QSequentialAnimationGroup | None = None
        self._fade_out_anim: QPropertyAnimation | None = None
        log.info("STARTUP OVERLAY CREATED")
        self._build_animation()

    def _build_animation(self) -> None:
        logo_anim = _fade_in(self._logo_label, 450)
        tag_anim = _fade_in(self._tagline_label, 300)

        self._sequence = QSequentialAnimationGroup(self)
        self._sequence.addPause(200)            # 0-200ms: dark background
        self._sequence.addAnimation(logo_anim)   # 200-650ms: logo powers on
        self._sequence.addAnimation(tag_anim)    # 650-950ms: tagline appears
        self._sequence.addPause(550)            # hold beat before reveal
        self._sequence.finished.connect(self._start_fade_out)

    def start(self) -> None:
        """Starts the startup overlay animation."""
        log.info("STARTUP OVERLAY SHOWN")
        self.show()
        self.raise_()
        self._status_label.setText("Initializing MotionDrive...")
        for delay, text in ((400, "Preparing controls..."), (1000, "Ready")):
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(lambda text=text: self._status_label.setText(text))
            t.start(delay)
            self._status_timers.append(t)
        if self._sequence is not None:
            self._sequence.start()

    def skip(self) -> None:
        """Skip animation (e.g. start minimized)."""
        self._cleanup_timers()
        self.hide()
        log.info("STARTUP COMPLETE")
        self.finished.emit()

    def show_error(self, reason: str) -> None:
        """Renders failure state inside the startup overlay."""
        self.stop()
        while self.layout().count():
            item = self.layout().takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        title = QLabel("MotionDrive couldn't start")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #e6edf3;")
        self.layout().addWidget(title)

        body = QLabel("MotionDrive encountered a problem while starting."
                       + (f"\n\n{reason}" if reason else ""))
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_DIM};")
        self.layout().addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        restart_btn = QPushButton("Restart")
        restart_btn.setObjectName("primary")
        restart_btn.clicked.connect(self._restart_app)
        view_log_btn = QPushButton("View Log")
        view_log_btn.clicked.connect(self._open_log)
        btn_row.addStretch()
        btn_row.addWidget(view_log_btn)
        btn_row.addWidget(restart_btn)
        btn_row.addStretch()
        self.layout().addLayout(btn_row)
        self.setGraphicsEffect(None)
        self.show()
        self.raise_()

    def _restart_app(self) -> None:
        import os
        import subprocess
        import sys
        try:
            subprocess.Popen([sys.executable] + sys.argv)
        except Exception:
            pass
        os._exit(1)

    def _open_log(self) -> None:
        import os
        try:
            os.startfile(str(LOGS_DIR / "startup.log"))
        except Exception:
            pass

    def stop(self) -> None:
        """Cancel animation and timers on close."""
        if self._sequence is not None:
            self._sequence.stop()
        if self._fade_out_anim is not None:
            self._fade_out_anim.stop()
        self._cleanup_timers()

    def _cleanup_timers(self) -> None:
        for t in self._status_timers:
            try:
                t.stop()
            except Exception:
                pass
        self._status_timers.clear()

    def _start_fade_out(self) -> None:
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(1.0)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(280)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self._on_fade_out_finished)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._fade_out_anim = anim

    def _on_fade_out_finished(self) -> None:
        self.hide()
        log.info("STARTUP COMPLETE")
        self.finished.emit()
