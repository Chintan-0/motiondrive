"""Calibration wizard UI. Wraps calibration.CalibrationWizard, feeding it live
angle/distance samples for the wheel steps and pinch-intensity samples for
the new pedal steps, each frame."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame

from motiondrive.calibration import CalibrationWizard, CalibrationStep, STEP_ORDER, Calibration
from motiondrive.gestures import pinch_intensity
from motiondrive.steering import SteeringState
from motiondrive.ui.theme import COLOR_RED, COLOR_ACCENT, COLOR_GREEN, COLOR_TEXT_DIM
from motiondrive.ui.widgets import CameraView, TrackingIndicator

_WHEEL_STEPS = {CalibrationStep.CENTER, CalibrationStep.ROTATE_LEFT, CalibrationStep.ROTATE_RIGHT}
_RIGHT_PINCH_STEPS = {CalibrationStep.RIGHT_PINCH_OPEN, CalibrationStep.RIGHT_PINCH_CLOSED}
_LEFT_PINCH_STEPS = {CalibrationStep.LEFT_PINCH_OPEN, CalibrationStep.LEFT_PINCH_CLOSED}

# The real wizard has 8 capture steps (needed for both wheel AND pedal
# calibration) -- the user should never have to think in those terms.
# Group them into 4 friendly phases for display only; the underlying
# CalibrationWizard state machine is completely untouched.
_PHASES = [
    ("CENTER YOUR HANDS", {CalibrationStep.INTRO, CalibrationStep.CENTER}),
    ("TURN LEFT / TURN RIGHT", {CalibrationStep.ROTATE_LEFT, CalibrationStep.ROTATE_RIGHT}),
    ("SET UP PEDALS", {CalibrationStep.RIGHT_PINCH_OPEN, CalibrationStep.RIGHT_PINCH_CLOSED,
                        CalibrationStep.LEFT_PINCH_OPEN, CalibrationStep.LEFT_PINCH_CLOSED}),
    ("CALIBRATION COMPLETE ✓", {CalibrationStep.DONE}),
]


def _phase_index(step: CalibrationStep) -> int:
    for i, (_, steps) in enumerate(_PHASES):
        if step in steps:
            return i
    return 0


class CalibrationPage(QWidget):
    finished = Signal(Calibration)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.wizard = CalibrationWizard()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("CALIBRATION")
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        # 4 friendly phase dots -- no "step 3 of 9", no degrees/thresholds.
        dots_row = QHBoxLayout()
        dots_row.setSpacing(8)
        self._phase_dots: list[QLabel] = []
        for _ in _PHASES:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 14px;")
            dots_row.addWidget(dot)
            self._phase_dots.append(dot)
        dots_row.addStretch()
        root.addLayout(dots_row)

        body = QHBoxLayout()
        body.setSpacing(20)

        self.camera_view = CameraView()
        body.addWidget(self.camera_view, stretch=3)

        side = QFrame()
        side.setObjectName("panel")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(24, 24, 24, 24)
        side_layout.setSpacing(16)
        side.setFixedWidth(340)

        self.phase_label = QLabel("")
        self.phase_label.setWordWrap(True)
        self.phase_label.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {COLOR_ACCENT};")
        side_layout.addWidget(self.phase_label)

        self.step_label = QLabel(self.wizard.prompt)
        self.step_label.setWordWrap(True)
        self.step_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        side_layout.addWidget(self.step_label)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {COLOR_RED}; font-size: 12px;")
        side_layout.addWidget(self.error_label)

        self.tracking_indicator = TrackingIndicator()
        side_layout.addWidget(self.tracking_indicator)
        side_layout.addStretch()

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_DIM};")
        side_layout.addWidget(self.progress_label)

        self.capture_btn = QPushButton("CAPTURE")
        self.capture_btn.setObjectName("primary")
        self.capture_btn.clicked.connect(self._on_capture)
        side_layout.addWidget(self.capture_btn)

        self.skip_btn = QPushButton("Use recommended defaults instead")
        self.skip_btn.clicked.connect(self._on_skip)
        side_layout.addWidget(self.skip_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        side_layout.addWidget(self.cancel_btn)

        body.addWidget(side, stretch=1)
        root.addLayout(body)

        self._update_progress()

    def reset(self) -> None:
        self.wizard = CalibrationWizard()
        self.step_label.setText(self.wizard.prompt)
        self.error_label.setText("")
        self._update_progress()

    def _update_progress(self) -> None:
        phase_idx = _phase_index(self.wizard.step)
        self.phase_label.setText(_PHASES[phase_idx][0])
        for i, dot in enumerate(self._phase_dots):
            if i < phase_idx:
                dot.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 14px;")
            elif i == phase_idx:
                dot.setStyleSheet(f"color: {COLOR_ACCENT}; font-size: 14px;")
            else:
                dot.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 14px;")
        self.progress_label.setText(f"Phase {phase_idx + 1} of {len(_PHASES)}")
        finishing_steps = {CalibrationStep.ROTATE_RIGHT, CalibrationStep.LEFT_PINCH_CLOSED}
        self.capture_btn.setText(
            "DONE" if self.wizard.step == CalibrationStep.DONE else
            ("CONTINUE" if self.wizard.step in finishing_steps else "CAPTURE")
        )

    def on_frame(self, qimage, tracking, state: SteeringState, pedals=None) -> None:
        self.camera_view.update_frame(qimage, tracking)
        self.tracking_indicator.set_quality(state.quality)

        step = self.wizard.step
        if step in _WHEEL_STEPS and tracking and tracking.both_present:
            self.wizard.feed_sample(state.raw_angle_deg, state.hand_distance)
        elif step in _RIGHT_PINCH_STEPS and tracking and tracking.right is not None:
            self.wizard.feed_pinch_sample(pinch_intensity(tracking.right))
        elif step in _LEFT_PINCH_STEPS and tracking and tracking.left is not None:
            self.wizard.feed_pinch_sample(pinch_intensity(tracking.left))

    def _on_capture(self) -> None:
        error = self.wizard.capture()
        self.step_label.setText(self.wizard.prompt)
        self.error_label.setText(error or "")
        self._update_progress()
        if self.wizard.is_done():
            self.finished.emit(self.wizard.result)

    def _on_skip(self) -> None:
        self.finished.emit(Calibration.default())
