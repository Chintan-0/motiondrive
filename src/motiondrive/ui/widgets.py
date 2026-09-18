"""Custom-painted widgets: camera preview + hand/wheel overlay, the steering
wheel visualization, and the tracking-quality indicator."""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QImage, QPixmap, QFont
from PySide6.QtWidgets import QWidget, QLabel, QSizePolicy, QGraphicsOpacityEffect

from motiondrive import __version__
from motiondrive.hand_tracking import HandTracker, TrackingResult
from motiondrive.steering import TrackingQuality
from motiondrive.ui.theme import COLOR_ACCENT, COLOR_GREEN, COLOR_YELLOW, COLOR_RED, COLOR_TEXT_DIM

_HAND_COLOR = {"Left": QColor("#3ddc84"), "Right": QColor("#3b9dff")}


class CameraView(QLabel):
    """Displays the live (already-mirrored) camera frame with hand landmarks,
    connections, a mini wheel-line overlay, viewfinder corner brackets, and
    two small status badges (live/tracking) drawn as child widgets on top --
    the camera is MotionDrive's visual hero, not a plain video rectangle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(440, 280)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("""
            CameraView {
                background-color: #11161d;
                border: 1px solid #21262d;
                border-radius: 16px;
                color: #8b96a5;
                font-size: 14px;
            }
        """)
        self._tracking: Optional[TrackingResult] = None

        _badge_css = ("QLabel { background-color: rgba(5,8,12,190); border-radius: 8px; "
                       "padding: 4px 10px; font-size: 10px; font-weight: 700; }")
        self._live_badge = QLabel("○ CAMERA OFF", self)
        self._live_badge.setStyleSheet(_badge_css + f"QLabel {{ color: {COLOR_TEXT_DIM}; }}")
        self._live_badge.adjustSize()
        self._tracking_badge = QLabel("● HANDS NOT DETECTED", self)
        self._tracking_badge.setStyleSheet(_badge_css + f"QLabel {{ color: {COLOR_RED}; }}")
        self._tracking_badge.adjustSize()
        self._hint_label = QLabel("", self)
        self._hint_label.setAlignment(Qt.AlignCenter)
        self._hint_label.setStyleSheet(
            "QLabel { background-color: rgba(5,8,12,170); border-radius: 10px; padding: 8px 16px; "
            f"color: {COLOR_TEXT_DIM}; font-size: 12px; font-weight: 600; }}")
        self._hint_label.setVisible(False)
        self._position_badges()

        # "CAMERA READY" was a self-contradictory default -- paired with
        # "connect or select a webcam" it told the user the opposite of
        # what's true before a camera is actually running. DashboardPage
        # happened to override this immediately after construction, which
        # masked it there, but any other page using CameraView (e.g.
        # Calibration) showed the wrong text.
        self.show_placeholder("📷  CAMERA NOT READY\n\nConnect or select a webcam to bring MotionDrive to life.")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_badges()

    def _position_badges(self) -> None:
        margin = 14
        self._live_badge.move(margin, margin)
        self._tracking_badge.adjustSize()
        self._tracking_badge.move(self.width() - self._tracking_badge.width() - margin, margin)
        self._hint_label.adjustSize()
        self._hint_label.move((self.width() - self._hint_label.width()) // 2, self.height() - 56)

    def set_camera_live(self, live: bool) -> None:
        if live:
            self._live_badge.setText("● CAMERA LIVE")
            self._live_badge.setStyleSheet(
                "QLabel { background-color: rgba(5,8,12,190); border-radius: 8px; padding: 4px 10px; "
                f"font-size: 10px; font-weight: 700; color: {COLOR_GREEN}; }}")
        else:
            self._live_badge.setText("○ CAMERA OFF")
            self._live_badge.setStyleSheet(
                "QLabel { background-color: rgba(5,8,12,190); border-radius: 8px; padding: 4px 10px; "
                f"font-size: 10px; font-weight: 700; color: {COLOR_TEXT_DIM}; }}")
        self._live_badge.adjustSize()
        self._position_badges()

    def set_tracking_badge(self, text: str, color: str) -> None:
        self._tracking_badge.setText(text)
        self._tracking_badge.setStyleSheet(
            "QLabel { background-color: rgba(5,8,12,190); border-radius: 8px; padding: 4px 10px; "
            f"font-size: 10px; font-weight: 700; color: {color}; }}")
        self._position_badges()

    def set_center_hint(self, text: str) -> None:
        self._hint_label.setText(text)
        self._hint_label.setVisible(bool(text))
        self._position_badges()

    def update_frame(self, qimage: QImage, tracking: Optional[TrackingResult]) -> None:
        self._tracking = tracking
        pixmap = QPixmap.fromImage(qimage)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        if tracking:
            w, h = pixmap.width(), pixmap.height()
            for hand in (tracking.left, tracking.right):
                if hand is None:
                    continue
                color = _HAND_COLOR.get(hand.handedness, QColor(COLOR_ACCENT))
                pen = QPen(color, 2)
                painter.setPen(pen)
                pts = [QPointF(p.x * w, p.y * h) for p in hand.landmarks]
                for a, b in HandTracker.HAND_CONNECTIONS_INDEX_PAIRS:
                    painter.drawLine(pts[a], pts[b])
                painter.setBrush(QBrush(color))
                for p in pts:
                    painter.drawEllipse(p, 3, 3)
                cx, cy = hand.palm_center.x * w, hand.palm_center.y * h
                painter.setBrush(QBrush(color.lighter(140)))
                painter.drawEllipse(QPointF(cx, cy), 6, 6)

            if tracking.both_present:
                lp = tracking.left.palm_center
                rp = tracking.right.palm_center
                pen = QPen(QColor(COLOR_ACCENT), 2, Qt.DashLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(lp.x * w, lp.y * h), QPointF(rp.x * w, rp.y * h))
        painter.end()
        self.setPixmap(pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def show_placeholder(self, text: str) -> None:
        self.setPixmap(QPixmap())
        self.setText(text)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # Quiet viewfinder-style corner brackets -- purely decorative,
        # drawn once per repaint (cheap: 8 short lines, no extra timers).
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(COLOR_ACCENT).lighter(120), 2, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        L = 18
        m = 10
        w, h = self.width(), self.height()
        corners = [(m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)]
        for x, y, dx, dy in corners:
            painter.drawLine(QPointF(x, y), QPointF(x + L * dx, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + L * dy))
        painter.end()


class SteeringWheelWidget(QWidget):
    """A smoothly-rotating virtual steering wheel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self._angle_deg = 0.0

    def set_angle(self, angle_deg: float) -> None:
        self._angle_deg = max(-540.0, min(540.0, angle_deg))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 12
        cx, cy = self.width() / 2, self.height() / 2
        radius = side / 2

        painter.setPen(QPen(QColor("#2a3240"), 3))
        painter.setBrush(QBrush(QColor("#161b22")))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        # A quiet motion arc communicating "how far from center, which way"
        # at a glance -- purely a static angle->sweep mapping recomputed
        # from the current value each paint, no extra timers/state.
        turn_fraction = max(-1.0, min(1.0, self._angle_deg / 90.0))
        if abs(turn_fraction) > 0.01:
            arc_pen = QPen(QColor(COLOR_ACCENT).lighter(130), 4, Qt.SolidLine, Qt.RoundCap)
            arc_pen.setColor(QColor(COLOR_ACCENT) if turn_fraction < 0 else QColor("#3ddc84"))
            painter.setPen(arc_pen)
            painter.setBrush(Qt.NoBrush)
            arc_rect = QRectF(cx - radius - 6, cy - radius - 6, (radius + 6) * 2, (radius + 6) * 2)
            start_angle = 90 * 16  # top, Qt angles in 1/16th degrees, counter-clockwise positive
            span_angle = int(-turn_fraction * 120 * 16)
            painter.drawArc(arc_rect, start_angle, span_angle)

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self._angle_deg)

        rim_pen = QPen(QColor(COLOR_ACCENT), 10, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(rim_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), radius - 8, radius - 8)

        spoke_pen = QPen(QColor("#8fbcf0"), 6, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(spoke_pen)
        for spoke_angle in (90, 210, 330):
            rad = math.radians(spoke_angle)
            painter.drawLine(QPointF(0, 0), QPointF(math.cos(rad) * (radius - 14),
                                                       math.sin(rad) * (radius - 14)))
        painter.setBrush(QBrush(QColor("#0d1117")))
        painter.setPen(QPen(QColor(COLOR_ACCENT), 3))
        painter.drawEllipse(QPointF(0, 0), 16, 16)

        # A tiny "C" in the hub -- a quiet, easy-to-overlook signature mark.
        painter.setPen(QPen(QColor(COLOR_ACCENT).lighter(160), 1.4))
        painter.setFont(QFont(painter.font().family(), 9, QFont.DemiBold))
        painter.drawText(QRectF(-8, -8, 16, 16), Qt.AlignCenter, "C")
        painter.restore()

        # Center tick (fixed reference)
        painter.setPen(QPen(QColor(COLOR_TEXT_DIM), 2))
        painter.drawLine(QPointF(cx, cy - radius - 4), QPointF(cx, cy - radius + 10))
        painter.end()


class TrackingIndicator(QWidget):
    """Colored dot + text label, never relying on color alone (accessibility)."""

    LABELS = {
        TrackingQuality.EXCELLENT: ("Excellent tracking", COLOR_GREEN, "\U0001F7E2"),
        TrackingQuality.UNSTABLE: ("Tracking unstable", COLOR_YELLOW, "\U0001F7E1"),
        TrackingQuality.LOST: ("Hands not detected", COLOR_RED, "\U0001F534"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel()
        self._label.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self._label)
        self._effect = QGraphicsOpacityEffect(self._label)
        self._effect.setOpacity(1.0)
        self._label.setGraphicsEffect(self._effect)
        self._current_quality = None
        self.set_quality(TrackingQuality.LOST)

    def set_quality(self, quality: TrackingQuality) -> None:
        changed = quality != self._current_quality
        self._current_quality = quality
        text, color, emoji = self.LABELS[quality]
        self._label.setText(f"{emoji}  {text.upper()}")
        self._label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {color};")
        if changed:
            # A quiet dip-and-recover instead of an instant color snap --
            # never an aggressive flash, per spec.
            anim = QPropertyAnimation(self._effect, b"opacity", self)
            anim.setDuration(260)
            anim.setKeyValueAt(0.0, 0.35)
            anim.setKeyValueAt(1.0, 1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start(QPropertyAnimation.DeleteWhenStopped)
            self._pulse_anim = anim  # keep a reference alive


class _SmoothedValueMixin:
    """Adds a light widget-level glide toward the latest set_value() target
    (the engine already sends EMA-smoothed values every frame -- this just
    keeps the *rendered* position from visually snapping between paint
    calls, giving it a touch of momentum without any bounce). Runs a small
    timer only while actually converging, so it costs nothing at rest."""

    def _init_smoothing(self) -> None:
        self._display_value = 0.0
        self._target_value = 0.0
        self._smooth_timer = QTimer(self)
        self._smooth_timer.setInterval(16)
        self._smooth_timer.timeout.connect(self._tick_smoothing)

    def _set_target(self, value: float) -> None:
        self._target_value = value
        if not self._smooth_timer.isActive():
            self._smooth_timer.start()

    def _tick_smoothing(self) -> None:
        delta = self._target_value - self._display_value
        if abs(delta) < 0.003:
            self._display_value = self._target_value
            self._smooth_timer.stop()
        else:
            self._display_value += delta * 0.35  # critically-damped glide, no overshoot
        self.update()


class SteeringBar(QWidget, _SmoothedValueMixin):
    """LEFT <----o----> RIGHT bar used on the dashboard + test screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self._value = 0.0
        self._init_smoothing()

    def set_value(self, value: float) -> None:
        self._value = max(-1.0, min(1.0, value))
        self._set_target(self._value)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = 20
        y = self.height() / 2
        w = self.width() - 2 * margin

        painter.setPen(QPen(QColor("#2a3240"), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(margin, y), QPointF(margin + w, y))

        center_x = margin + w / 2
        painter.setPen(QPen(QColor("#8b96a5"), 2))
        painter.drawLine(QPointF(center_x, y - 10), QPointF(center_x, y + 10))

        knob_x = center_x + (self._display_value * (w / 2))
        color = QColor(COLOR_ACCENT)
        painter.setPen(QPen(color, 0))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(knob_x, y), 12, 12)
        painter.end()


class TelemetryMeter(QWidget, _SmoothedValueMixin):
    """A telemetry card row: label, a big colored live number, and either a
    centered slider bar (steering: -100%..0..+100%) or a segmented block bar
    (throttle/brake: 0..100%). This is the Home page's signature readout --
    large numbers instead of small text, matching a real instrument panel."""

    def __init__(self, label: str, color: str, mode: str = "blocks", parent=None):
        super().__init__(parent)
        self._label_text = label
        self._color = QColor(color)
        self._mode = mode  # "slider" | "blocks"
        self.setMinimumHeight(70)
        self._init_smoothing()

    def set_value(self, value: float) -> None:
        if self._mode == "slider":
            v = max(-1.0, min(1.0, value))
        else:
            v = max(0.0, min(1.0, value))
        self._set_target(v)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(QPen(QColor(COLOR_TEXT_DIM)))
        painter.setFont(QFont(painter.font().family(), 9, QFont.DemiBold))
        painter.drawText(0, 0, self.width(), 16, Qt.AlignLeft, self._label_text.upper())

        pct = int(round(self._display_value * 100))
        number_text = f"{pct:+d}%" if self._mode == "slider" else f"{pct}%"
        painter.setPen(QPen(self._color))
        painter.setFont(QFont(painter.font().family(), 22, QFont.Bold))
        painter.drawText(0, 16, self.width(), 32, Qt.AlignLeft, number_text)

        bar_y = 54
        bar_h = 10
        if self._mode == "slider":
            painter.setPen(QPen(QColor("#2a3240"), 6, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(2, bar_y + bar_h / 2), QPointF(self.width() - 2, bar_y + bar_h / 2))
            center_x = self.width() / 2
            knob_x = center_x + self._display_value * (self.width() / 2 - 8)
            painter.setPen(QPen(self._color, 0))
            painter.setBrush(QBrush(self._color))
            painter.drawEllipse(QPointF(knob_x, bar_y + bar_h / 2), 7, 7)
        else:
            n = 12
            gap = 3.0
            block_w = (self.width() - gap * (n - 1)) / n
            filled = round(self._display_value * n)
            painter.setPen(Qt.NoPen)
            for i in range(n):
                x = i * (block_w + gap)
                rect = QRectF(x, bar_y, block_w, bar_h)
                color = self._color if i < filled else QColor("#1c2330")
                painter.setBrush(QBrush(color))
                painter.drawRoundedRect(rect, 2, 2)
        painter.end()


class PedalBar(QWidget, _SmoothedValueMixin):
    """A labeled horizontal fill bar for throttle (accent) or brake (red),
    0.0..1.0, with a percent readout and key state status."""

    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        self._label_text = label
        self._color = QColor(color)
        self._value = 0.0
        self._key_str = ""
        self._is_down = False
        self.setMinimumHeight(46)
        self._init_smoothing()

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self._set_target(self._value)

    def set_key_status(self, key_str: str, is_down: bool) -> None:
        self._key_str = key_str.upper()
        self._is_down = is_down
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        label_h = 16
        bar_h = self.height() - label_h - 4
        bar_y = label_h + 4

        painter.setPen(QPen(QColor(COLOR_TEXT_DIM)))
        painter.setFont(QFont(painter.font().family(), 9, QFont.DemiBold))
        painter.drawText(0, 0, self.width(), label_h, Qt.AlignLeft, self._label_text.upper())
        
        pct_text = f"{int(round(self._display_value * 100))}%"
        if self._key_str:
            indicator = "● DOWN" if self._is_down else "○ UP"
            pct_text = f"{pct_text}   {self._key_str} {indicator}"
        painter.drawText(0, 0, self.width(), label_h, Qt.AlignRight, pct_text)

        track_rect = QRectF(0, bar_y, self.width(), bar_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#1c2330")))
        painter.drawRoundedRect(track_rect, bar_h / 2, bar_h / 2)

        if self._display_value > 0.001:
            fill_w = max(bar_h, self.width() * self._display_value)
            fill_rect = QRectF(0, bar_y, fill_w, bar_h)
            painter.setBrush(QBrush(self._color))
            painter.drawRoundedRect(fill_rect, bar_h / 2, bar_h / 2)
        painter.end()


class BrandFooter(QLabel):
    """A very quiet version/brand footer -- small, muted, bottom-right. Must
    never compete visually with any control; version comes from
    `motiondrive.__version__` so it's never hardcoded in more than one place."""

    def __init__(self, parent=None):
        super().__init__(f"v{__version__}  ·  MotionDrive", parent)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_DIM}; padding: 2px 4px;")

        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        self._fade_in = QPropertyAnimation(effect, b"opacity", self)
        self._fade_in.setDuration(400)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(0.55)  # deliberately low-contrast at rest
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)
        QTimer.singleShot(150, self._fade_in.start)


class CarResponseWidget(QWidget):
    """Top-down stylized car preview widget: front wheels turn live with steering value."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 190)
        self._steering_value = 0.0

    def set_steering(self, value: float) -> None:
        self._steering_value = max(-1.0, min(1.0, value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        # Background track surface
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#0d1117")))
        painter.drawRoundedRect(QRectF(0, 0, w, h), 12, 12)

        # Grid/Lane lines
        painter.setPen(QPen(QColor("#161b22"), 1, Qt.DashLine))
        painter.drawLine(QPointF(cx, 10), QPointF(cx, h - 10))

        # Car body dims -- slightly larger than the original 60x110/10x22
        # (item: "increase car size slightly, tire size slightly", without
        # letting it dominate the page).
        car_w, car_h = 72, 128
        car_x = cx - car_w / 2
        car_y = cy - car_h / 2

        # Draw rear wheels (fixed angle)
        wheel_w, wheel_h = 13, 26
        painter.setBrush(QBrush(QColor("#30363d")))
        painter.setPen(QPen(QColor("#484f58"), 1))
        painter.drawRoundedRect(QRectF(car_x - wheel_w + 3, car_y + car_h - 38, wheel_w, wheel_h), 2, 2)
        painter.drawRoundedRect(QRectF(car_x + car_w - 3, car_y + car_h - 38, wheel_w, wheel_h), 2, 2)

        # Draw front wheels (rotated by steering angle)
        steer_angle = self._steering_value * 35.0

        # Front Left Wheel
        painter.save()
        fl_cx, fl_cy = car_x + 3, car_y + 21
        painter.translate(fl_cx, fl_cy)
        painter.rotate(steer_angle)
        painter.drawRoundedRect(QRectF(-wheel_w / 2, -wheel_h / 2, wheel_w, wheel_h), 2, 2)
        painter.restore()

        # Front Right Wheel
        painter.save()
        fr_cx, fr_cy = car_x + car_w - 3, car_y + 21
        painter.translate(fr_cx, fr_cy)
        painter.rotate(steer_angle)
        painter.drawRoundedRect(QRectF(-wheel_w / 2, -wheel_h / 2, wheel_w, wheel_h), 2, 2)
        painter.restore()

        # Car main chassis body
        chassis_rect = QRectF(car_x, car_y, car_w, car_h)
        painter.setBrush(QBrush(QColor("#1f2937")))
        painter.setPen(QPen(QColor(COLOR_ACCENT), 2))
        painter.drawRoundedRect(chassis_rect, 14, 14)

        # Windshield
        painter.setBrush(QBrush(QColor("#374151")))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(car_x + 12, car_y + 35, car_w - 24, 28), 4, 4)

        # Center line accent
        painter.setPen(QPen(QColor(COLOR_ACCENT), 2))
        painter.drawLine(QPointF(cx, car_y + 12), QPointF(cx, car_y + car_h - 12))
        painter.end()
