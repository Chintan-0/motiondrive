"""Home: the simple "am I ready to drive?" screen. Camera hero on the left,
readiness status underneath, and a single primary action on the right --
START & PLAY. No separate camera/controller start buttons: the user should
never need to understand camera or controller initialization, just click
one button."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
                                 QGraphicsOpacityEffect)

from motiondrive.controller_state import ControllerState, TrackingState, TRACKING_LABELS
from motiondrive.pedals import PedalState
from motiondrive.steering import SteeringState
from motiondrive.ui.widgets import CameraView, TelemetryMeter
from motiondrive.ui.theme import COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_TEXT_DIM, COLOR_ACCENT

_TRACKING_COLOR = {
    TrackingState.EXCELLENT: COLOR_GREEN,
    TrackingState.UNSTABLE: COLOR_YELLOW,
    TrackingState.RECOVERING: COLOR_YELLOW,
    TrackingState.RIGHT_HAND_LOST: COLOR_YELLOW,
    TrackingState.LEFT_HAND_LOST: COLOR_YELLOW,
    TrackingState.BOTH_HANDS_LOST: COLOR_RED,
}


def _mini_status_card(icon: str, title: str) -> tuple[QFrame, QLabel, QLabel]:
    """One of the status-strip cards under the camera (Camera / Tracking) --
    an icon badge, a bold title, and a detail line callers update with
    real state."""
    card = QFrame()
    card.setObjectName("panel")
    row = QHBoxLayout(card)
    row.setContentsMargins(14, 12, 14, 12)
    row.setSpacing(10)

    icon_lbl = QLabel(icon)
    icon_lbl.setStyleSheet("font-size: 18px; background-color: #1c2330; border-radius: 16px; "
                            "min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;")
    icon_lbl.setAlignment(Qt.AlignCenter)
    row.addWidget(icon_lbl)

    text_col = QVBoxLayout()
    text_col.setSpacing(1)
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {COLOR_TEXT_DIM};")
    detail_lbl = QLabel("—")
    detail_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
    text_col.addWidget(title_lbl)
    text_col.addWidget(detail_lbl)
    row.addLayout(text_col, stretch=1)
    return card, title_lbl, detail_lbl


class DashboardPage(QWidget):
    # START & PLAY is the only user-facing action on this page -- it
    # internally chains camera -> tracking -> controller -> game launch in
    # one step (see MainWindow._start_and_play). startCamera/startController/
    # stopController/emergencyStop/hideAndPlay used to be separate buttons
    # here; they're gone now, so those signals were removed too rather than
    # kept declared-but-never-emitted.
    startAndPlay = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        # -------------------------------------------------------- left: camera
        left_col = QVBoxLayout()
        left_col.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("MotionDrive")
        title.setObjectName("appTitle")
        self.status_pill = QLabel("● NOT READY")
        self.status_pill.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {COLOR_RED}; background-color: #1c1414; "
            "border-radius: 10px; padding: 4px 12px;")
        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(self.status_pill)
        header.addStretch()
        left_col.addLayout(header)

        self.camera_view = CameraView()
        left_col.addWidget(self.camera_view, stretch=1)

        # status strip: Camera / Tracking readiness -- exactly the two
        # things a first-time user needs to know before driving.
        strip = QHBoxLayout()
        strip.setSpacing(12)
        cam_card, _, self.camera_detail_lbl = _mini_status_card("📷", "CAMERA")
        track_card, _, self.tracking_detail_lbl = _mini_status_card("🎯", "TRACKING")
        for card in (cam_card, track_card):
            strip.addWidget(card, stretch=1)
        left_col.addLayout(strip)

        privacy = QLabel("\U0001F512 Camera processing happens locally on your PC.")
        privacy.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 11px;")
        left_col.addWidget(privacy)

        root.addLayout(left_col, stretch=3)

        # ------------------------------------------------------- right: play
        right_col = QVBoxLayout()
        right_col.setSpacing(16)
        right_col.addStretch()

        ready_card = QFrame()
        ready_card.setObjectName("panel")
        rc = QVBoxLayout(ready_card)
        rc.setContentsMargins(20, 22, 20, 22)
        rc.setSpacing(10)

        self.ready_headline = QLabel("NOT READY YET")
        self.ready_headline.setAlignment(Qt.AlignCenter)
        self.ready_headline.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {COLOR_TEXT_DIM}; "
                                            "letter-spacing: 1px;")
        rc.addWidget(self.ready_headline)

        self.steering_meter = TelemetryMeter("Steering", COLOR_ACCENT, mode="slider")
        rc.addWidget(self.steering_meter)

        rc.addSpacing(6)
        self.start_and_play_btn = QPushButton("▶  START & PLAY")
        self.start_and_play_btn.setObjectName("primary")
        self.start_and_play_btn.setToolTip("Start your game and drive with your hands.")
        self.start_and_play_btn.clicked.connect(self.startAndPlay.emit)
        rc.addWidget(self.start_and_play_btn)

        self.helper_label = QLabel("Start your game and drive with your hands.")
        self.helper_label.setWordWrap(True)
        self.helper_label.setAlignment(Qt.AlignCenter)
        self.helper_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px; padding-top: 2px;")
        rc.addWidget(self.helper_label)

        hint = QLabel("Put your hands up like you're holding a steering wheel.")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color: {COLOR_ACCENT}; font-size: 11px; font-weight: 600; padding-top: 4px;")
        rc.addWidget(hint)

        right_col.addWidget(ready_card)
        right_col.addStretch()
        root.addLayout(right_col, stretch=2)

        self._controller_state = ControllerState.OFF
        self._camera_active = False
        self._tracking_ready = False
        self.camera_view.set_center_hint("HOLD YOUR WHEEL\nKeep your hands visible and centered")

    # ------------------------------------------------------- readiness
    def _refresh_ready_state(self) -> None:
        if self._controller_state != ControllerState.OFF:
            self.ready_headline.setText("LIVE")
            self.ready_headline.setStyleSheet(
                f"font-size: 16px; font-weight: 800; color: {COLOR_GREEN}; letter-spacing: 1px;")
            self.status_pill.setText("● LIVE")
            self.status_pill.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {COLOR_GREEN}; background-color: #0d1b14; "
                "border-radius: 10px; padding: 4px 12px;")
        elif self._camera_active:
            self.ready_headline.setText("READY TO DRIVE")
            self.ready_headline.setStyleSheet(
                f"font-size: 16px; font-weight: 800; color: {COLOR_GREEN}; letter-spacing: 1px;")
            self.status_pill.setText("● READY")
            self.status_pill.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {COLOR_GREEN}; background-color: #0d1b14; "
                "border-radius: 10px; padding: 4px 12px;")
        else:
            self.ready_headline.setText("NOT READY YET")
            self.ready_headline.setStyleSheet(
                f"font-size: 16px; font-weight: 800; color: {COLOR_TEXT_DIM}; letter-spacing: 1px;")
            self.status_pill.setText("● NOT READY")
            self.status_pill.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {COLOR_RED}; background-color: #1c1414; "
                "border-radius: 10px; padding: 4px 12px;")

    def set_preparing(self, preparing: bool, text: str = "PREPARING...") -> None:
        """Camera/tracker startup (constructing the hand tracker, opening
        the webcam) can take a couple of real seconds, during which the UI
        thread is busy and nothing else visibly changes -- looking frozen.
        This gives immediate, smooth feedback the instant START & PLAY is
        clicked: the button disables, relabels, and gently pulses until
        the underlying camera/controller/game-launch sequence finishes."""
        self.start_and_play_btn.setEnabled(not preparing)
        if preparing:
            self.start_and_play_btn.setText(f"⏳  {text}")
            effect = QGraphicsOpacityEffect(self.start_and_play_btn)
            self.start_and_play_btn.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self.start_and_play_btn)
            anim.setDuration(700)
            anim.setStartValue(1.0)
            anim.setKeyValueAt(0.5, 0.55)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.InOutSine)
            anim.setLoopCount(-1)
            anim.start()
            self._preparing_anim = anim  # keep alive while looping
        else:
            self.start_and_play_btn.setText("▶  START & PLAY")
            anim = getattr(self, "_preparing_anim", None)
            if anim is not None:
                anim.stop()
                self._preparing_anim = None
            self.start_and_play_btn.setGraphicsEffect(None)

    def set_camera_active(self, active: bool) -> None:
        self._camera_active = active
        self.camera_view.set_camera_live(active)
        self.camera_detail_lbl.setText("Active" if active else "Not connected")
        self.camera_detail_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {COLOR_GREEN if active else COLOR_TEXT_DIM};")
        if not active:
            self.camera_view.show_placeholder("CAMERA NOT READY\n\nConnect or select a webcam\nto bring MotionDrive to life.")
            self.camera_view.set_tracking_badge("● HANDS NOT DETECTED", COLOR_RED)
        self._refresh_ready_state()

    def set_controller_state(self, state: ControllerState) -> None:
        self._controller_state = state
        self._refresh_ready_state()

    def set_tracking_state(self, state: TrackingState) -> None:
        color = _TRACKING_COLOR.get(state, COLOR_TEXT_DIM)
        label = TRACKING_LABELS[state]
        both = state == TrackingState.EXCELLENT
        count = "2" if both else ("1" if state in (
            TrackingState.RIGHT_HAND_LOST, TrackingState.LEFT_HAND_LOST, TrackingState.UNSTABLE) else "0")
        badge_text = f"● {count} HAND{'S' if count != '1' else ''} DETECTED" if count != "0" else "● HANDS NOT DETECTED"
        self.camera_view.set_tracking_badge(badge_text, color)
        self.tracking_detail_lbl.setText(label)
        self.tracking_detail_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color};")
        # Once tracking is genuinely good, the center hint has done its job.
        self.camera_view.set_center_hint(
            "" if state == TrackingState.EXCELLENT else
            "HOLD YOUR WHEEL\nKeep your hands visible and centered")

    def set_profile_name(self, name: str) -> None:
        pass  # profile terminology no longer surfaced on Home -- see Settings' Game/Control Preset

    def update_key_states(self, key_states: dict) -> None:
        pass  # key-down/up detail lives on the Controller page only

    def on_frame(self, qimage, tracking, state: SteeringState, pedals: PedalState, fps: float) -> None:
        self.camera_view.update_frame(qimage, tracking)
        self.steering_meter.set_value(state.value)

    def show_hint(self, text: str) -> None:
        pass  # superseded by the camera's own center hint + tracking badge
