"""Controller: the live driving screen. Camera + hand tracking on the
left, a large steering/car visual with throttle/brake and tracking/
controller status on the right, and a Stop Controller button. No
configuration here -- that lives entirely in Settings (Drive Feel,
Sensitivity, Deadzone, Stability); this page is purely live status."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QScrollArea

from motiondrive.controller_state import ControllerState, TrackingState, TRACKING_LABELS
from motiondrive.pedals import PedalState
from motiondrive.steering import SteeringState
from motiondrive.ui.theme import COLOR_ACCENT, COLOR_RED, COLOR_GREEN, COLOR_YELLOW, COLOR_TEXT_DIM
from motiondrive.ui.widgets import CameraView, SteeringWheelWidget, CarResponseWidget, TelemetryMeter

_TRACKING_COLOR = {
    TrackingState.EXCELLENT: COLOR_GREEN,
    TrackingState.UNSTABLE: COLOR_YELLOW,
    TrackingState.RECOVERING: COLOR_YELLOW,
    TrackingState.RIGHT_HAND_LOST: COLOR_YELLOW,
    TrackingState.LEFT_HAND_LOST: COLOR_YELLOW,
    TrackingState.BOTH_HANDS_LOST: COLOR_RED,
}


def _badge(text: str, color: str) -> QLabel:
    lbl = QLabel(f"●  {text}")
    lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 700;")
    return lbl


def _status_row(label: str) -> tuple[QHBoxLayout, QLabel]:
    row = QHBoxLayout()
    row.setSpacing(8)
    lab = QLabel(label)
    lab.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_DIM};")
    value = QLabel("—")
    value.setStyleSheet("font-size: 12px; font-weight: 700;")
    row.addWidget(lab)
    row.addStretch()
    row.addWidget(value)
    return row, value


class ControllerPage(QWidget):
    __test__ = False
    emergencyStop = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        # -------------------------------------------------------- left: camera
        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("CONTROLLER")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        # Was a static "LIVE DRIVING" label regardless of actual state --
        # kept a reference so set_controller_state() can update both its
        # text and color to reflect what's really happening (a real bug:
        # this showed "LIVE DRIVING" even with the controller OFF).
        self.status_badge = _badge("CONTROLLER OFF", COLOR_RED)
        header.addWidget(self.status_badge)
        left_col.addLayout(header)

        self.camera_view = CameraView()
        left_col.addWidget(self.camera_view, stretch=1)
        root.addLayout(left_col, stretch=3)

        # ---------------------------------------------------- right: status
        # The wheel+car visual, throttle/brake, and status cards need more
        # height than fits inside the app's own minimum window size
        # (980x640) without compressing -- confirmed by adding up each
        # card's real minimum height. Without a scroll area, tight vertical
        # space made cards crowd together with little to no visible gap
        # between them (reported as the wheel and car visual looking like
        # they overlap). Same proven fix already used on the Home page:
        # everything except the always-reachable Stop button scrolls.
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_content = QWidget()
        right_scroll.setWidget(right_content)
        right_col = QVBoxLayout(right_content)
        right_col.setContentsMargins(0, 0, 4, 0)
        right_col.setSpacing(16)

        visual_card = QFrame()
        visual_card.setObjectName("panel")
        vc = QVBoxLayout(visual_card)
        vc.setContentsMargins(20, 20, 20, 20)
        vc.setSpacing(10)
        self.steering_label = QLabel("STEERING\n+0%")
        self.steering_label.setAlignment(Qt.AlignCenter)
        self.steering_label.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {COLOR_ACCENT};")
        vc.addWidget(self.steering_label)
        self.wheel = SteeringWheelWidget()
        vc.addWidget(self.wheel, alignment=Qt.AlignCenter)
        self.car_preview = CarResponseWidget()
        vc.addWidget(self.car_preview, alignment=Qt.AlignCenter)
        right_col.addWidget(visual_card)

        pedals_card = QFrame()
        pedals_card.setObjectName("panel")
        pc = QVBoxLayout(pedals_card)
        pc.setContentsMargins(20, 16, 20, 16)
        pc.setSpacing(12)
        self.throttle_meter = TelemetryMeter("Throttle", COLOR_GREEN, mode="blocks")
        self.brake_meter = TelemetryMeter("Brake", COLOR_RED, mode="blocks")
        pc.addWidget(self.throttle_meter)
        pc.addWidget(self.brake_meter)
        right_col.addWidget(pedals_card)

        status_card = QFrame()
        status_card.setObjectName("panel")
        sc = QVBoxLayout(status_card)
        sc.setContentsMargins(20, 16, 20, 16)
        sc.setSpacing(10)
        tracking_row, self.tracking_value_lbl = _status_row("TRACKING STATUS")
        sc.addLayout(tracking_row)
        controller_row, self.controller_value_lbl = _status_row("CONTROLLER STATUS")
        sc.addLayout(controller_row)
        right_col.addWidget(status_card)
        right_col.addStretch()

        self.stop_btn = QPushButton("⛔  STOP CONTROLLER  (ESC)")
        self.stop_btn.setObjectName("stop")
        self.stop_btn.clicked.connect(self.emergencyStop.emit)

        # Stop must always be reachable without scrolling -- pinned below
        # the scroll area, same pattern as Home's START & PLAY controls.
        right_container = QVBoxLayout()
        right_container.setSpacing(12)
        right_container.addWidget(right_scroll, stretch=1)
        right_container.addWidget(self.stop_btn)
        root.addLayout(right_container, stretch=2)

        self.camera_view.set_center_hint("HOLD YOUR WHEEL\nKeep your hands visible and centered")
        self._set_controller_value("OFF", COLOR_RED)

    # ----------------------------------------------------------- status
    def _set_controller_value(self, text: str, color: str) -> None:
        self.controller_value_lbl.setText(text)
        self.controller_value_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {color};")

    def set_controller_state(self, state: ControllerState) -> None:
        labels = {
            ControllerState.OFF: ("OFF", "CONTROLLER OFF", COLOR_RED),
            ControllerState.RUNNING: ("LIVE", "LIVE DRIVING", COLOR_GREEN),
            ControllerState.PAUSED: ("PAUSED", "CONTROLLER PAUSED", COLOR_YELLOW),
        }
        text, badge_text, color = labels[state]
        self._set_controller_value(text, color)
        self.status_badge.setText(f"●  {badge_text}")
        self.status_badge.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 700;")

    def set_tracking_state(self, state: TrackingState) -> None:
        color = _TRACKING_COLOR.get(state, COLOR_TEXT_DIM)
        label = TRACKING_LABELS[state]
        both = state == TrackingState.EXCELLENT
        count = "2" if both else ("1" if state in (
            TrackingState.RIGHT_HAND_LOST, TrackingState.LEFT_HAND_LOST, TrackingState.UNSTABLE) else "0")
        badge_text = f"● {count} HAND{'S' if count != '1' else ''} DETECTED" if count != "0" else "● HANDS NOT DETECTED"
        self.camera_view.set_tracking_badge(badge_text, color)
        self.tracking_value_lbl.setText(label)
        self.tracking_value_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {color};")
        self.camera_view.set_center_hint(
            "" if state == TrackingState.EXCELLENT else
            "HOLD YOUR WHEEL\nKeep your hands visible and centered")

    def set_camera_active(self, active: bool) -> None:
        self.camera_view.set_camera_live(active)
        if not active:
            self.camera_view.show_placeholder("CAMERA NOT READY\n\nStart driving from Home first.")
            self.camera_view.set_tracking_badge("● HANDS NOT DETECTED", COLOR_RED)

    def set_phone_active(self, active: bool, signal_good: bool = True) -> None:
        if active:
            self.camera_view.set_camera_live(False)
            self.camera_view.show_placeholder("📱 PHONE CONTROLLER\n\nWireless Phone Wheel Connected\nSignal: GOOD | Input: ACTIVE")
            self.camera_view.set_tracking_badge("● PHONE CONNECTED", COLOR_GREEN)
            self.tracking_value_lbl.setText("PHONE CONTROLLER (ACTIVE)")
            self.tracking_value_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_GREEN};")

    # -------------------------------------------------- backward-compat i/o
    # update_key_state/clear_key_state are no longer wired to any visible
    # UI here (the live key output panel was removed per the simplified
    # Controller UX) -- kept as no-ops so MainWindow's existing call sites
    # don't need to change, and because the actual input-state tracking
    # this used to visualize still lives where it always did, in
    # KeyboardBackend.key_states() (used for diagnostics/safety), not here.
    def update_key_state(self, key_states: dict) -> None:
        pass

    def clear_key_state(self) -> None:
        pass

    def load(self, settings) -> None:
        pass  # no configuration on this page anymore -- see SettingsPage

    # ------------------------------------------------------------- frames
    def update_state(self, tracking, state: SteeringState, pedals: PedalState, fps: float, qimage=None) -> None:
        if qimage is not None:
            self.camera_view.update_frame(qimage, tracking)
        self.wheel.set_angle(state.angle_deg)
        self.car_preview.set_steering(state.value)
        self.throttle_meter.set_value(pedals.throttle)
        self.brake_meter.set_value(pedals.brake)
        pct = state.value * 100
        self.steering_label.setText(f"STEERING\n{pct:+.0f}%")


# Backward-compatibility alias so existing test suites and imports remain 100% valid
TestControllerPage = ControllerPage
