"""Pure math: two hand positions -> a smoothed, calibrated steering value.

No OpenCV / MediaPipe / Qt imports here on purpose -- this module is fully
unit-testable with plain floats and is the one place steering "feel" lives.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from motiondrive.calibration import Calibration


class TrackingQuality(Enum):
    LOST = "lost"          # 0 or 1 hand missing too long
    UNSTABLE = "unstable"  # both present but low confidence / borderline
    EXCELLENT = "excellent"


@dataclass
class Point2D:
    x: float
    y: float


@dataclass
class SteeringState:
    value: float          # -1.0 .. +1.0, smoothed & calibrated
    angle_deg: float       # calibrated angle in degrees (value * range, roughly)
    raw_angle_deg: float   # uncalibrated raw hand-line angle
    quality: TrackingQuality
    hand_distance: float


def raw_wheel_angle_deg(left: Point2D, right: Point2D) -> float:
    """Angle of the line from the left hand to the right hand, in degrees.
    0 degrees = hands level (horizontal) = wheel centered.
    Positive = right hand lower than left = wheel turned right (clockwise)."""
    dx = right.x - left.x
    dy = right.y - left.y
    return math.degrees(math.atan2(dy, dx))


def hand_distance(left: Point2D, right: Point2D) -> float:
    return math.hypot(right.x - left.x, right.y - left.y)


def _sensitivity_curve(x: float, sensitivity_0_100: int) -> float:
    """x in [-1, 1]. sensitivity 0 = gentle (needs more rotation for same
    output), 100 = aggressive (small rotation -> big output). Implemented as
    a power curve so center stays precise at low sensitivity."""
    s = max(0, min(100, sensitivity_0_100)) / 100.0
    # exponent from 1.6 (low sensitivity, gentle) down to 0.6 (high sensitivity, snappy)
    exponent = 1.6 - (1.0 * s)
    sign = 1.0 if x >= 0 else -1.0
    return sign * (abs(x) ** exponent)


def apply_steering_curve(x: float, gamma: float = 1.4) -> float:
    """output = sign(input) * abs(input) ** gamma (default gamma=1.4)."""
    if abs(x) < 1e-6:
        return 0.0
    sign = 1.0 if x >= 0 else -1.0
    return sign * (abs(x) ** gamma)


def _apply_dead_zone(x: float, dead_zone_percent: int) -> float:
    dz = max(0, min(20, dead_zone_percent)) / 100.0
    if abs(x) <= dz:
        return 0.0
    # rescale remaining range back to [-1, 1] so output still reaches full lock
    sign = 1.0 if x >= 0 else -1.0
    return sign * (abs(x) - dz) / (1.0 - dz)


class SteeringEngine:
    """Stateful engine: feed hand positions (or None if a hand is missing)
    every frame, get back a smoothed SteeringState."""

    def __init__(self, calibration: Calibration, sensitivity: int = 55,
                 smoothing: int = 45, steering_range: int = 60, dead_zone: int = 5,
                 preset: str = "responsive", lost_after_frames: int = 12,
                 min_confidence: float = 0.5, gamma: float = 1.4):
        self.calibration = calibration
        self.sensitivity = sensitivity
        self.smoothing = smoothing
        self.steering_range = steering_range
        self.dead_zone = dead_zone
        self.preset = preset  # "responsive" | "balanced" | "smooth"
        self.lost_after_frames = lost_after_frames
        self.min_confidence = min_confidence
        self.gamma = gamma

        self._smoothed_value = 0.0
        self._missing_streak = 0
        self._last_state = SteeringState(0.0, 0.0, 0.0, TrackingQuality.LOST, 0.0)

    def update_settings(self, *, sensitivity=None, smoothing=None,
                         steering_range=None, dead_zone=None, preset=None, gamma=None):
        if sensitivity is not None:
            self.sensitivity = sensitivity
        if smoothing is not None:
            self.smoothing = smoothing
        if steering_range is not None:
            self.steering_range = steering_range
        if dead_zone is not None:
            self.dead_zone = dead_zone
        if preset is not None:
            self.preset = preset
        if gamma is not None:
            self.gamma = gamma

    def _ema_alpha(self, target_val: float) -> float:
        if self.preset == "responsive":
            # Responsive preset: snappy acceleration & fast direction changes
            delta = abs(target_val - self._smoothed_value)
            if abs(target_val) > abs(self._smoothed_value) or (target_val * self._smoothed_value < 0):
                # Steering acceleration or direction flip -> immediate response
                return max(0.75, min(0.92, 0.70 + delta * 0.4))
            else:
                # Return to center -> medium-fast response (~90 ms)
                return 0.65
        elif self.preset == "balanced":
            return 0.50
        else:
            # Smooth preset -> uses user smoothing slider
            s = max(0, min(100, self.smoothing)) / 100.0
            return 0.9 - (0.82 * s)

    def reset(self) -> None:
        self._smoothed_value = 0.0
        self._missing_streak = 0
        self._last_state = SteeringState(0.0, 0.0, 0.0, TrackingQuality.LOST, 0.0)

    def update(self, left: Optional[Point2D], right: Optional[Point2D],
               confidence: float = 1.0) -> SteeringState:
        if left is None or right is None or confidence < self.min_confidence:
            self._missing_streak += 1
            if self._missing_streak >= self.lost_after_frames:
                # Decay smoothly back to center (~90ms lerp)
                self._smoothed_value *= 0.85
                if abs(self._smoothed_value) < 0.01:
                    self._smoothed_value = 0.0
                quality = TrackingQuality.LOST
            else:
                quality = TrackingQuality.UNSTABLE
            self._last_state = SteeringState(
                value=self._smoothed_value,
                angle_deg=self._last_state.angle_deg,
                raw_angle_deg=self._last_state.raw_angle_deg,
                quality=quality,
                hand_distance=self._last_state.hand_distance,
            )
            return self._last_state

        self._missing_streak = 0
        raw_angle = raw_wheel_angle_deg(left, right)
        dist = hand_distance(left, right)
        cal = self.calibration

        centered = raw_angle - cal.center_angle_deg
        if centered < 0:
            span = max(1.0, cal.left_range_deg)
            normalized = max(-1.0, centered / span)
        else:
            span = max(1.0, cal.right_range_deg)
            normalized = min(1.0, centered / span)

        range_factor = 0.5 + (max(0, min(100, self.steering_range)) / 100.0)
        normalized = max(-1.0, min(1.0, normalized * range_factor))

        normalized = _apply_dead_zone(normalized, self.dead_zone)
        normalized = _sensitivity_curve(normalized, self.sensitivity)
        normalized = apply_steering_curve(normalized, self.gamma)
        normalized = max(-1.0, min(1.0, normalized))

        alpha = self._ema_alpha(normalized)
        self._smoothed_value = (alpha * normalized) + ((1 - alpha) * self._smoothed_value)
        self._smoothed_value = max(-1.0, min(1.0, self._smoothed_value))

        quality = TrackingQuality.EXCELLENT if confidence >= 0.75 else TrackingQuality.UNSTABLE

        self._last_state = SteeringState(
            value=self._smoothed_value,
            angle_deg=self._smoothed_value * max(cal.left_range_deg, cal.right_range_deg),
            raw_angle_deg=raw_angle,
            quality=quality,
            hand_distance=dist,
        )
        return self._last_state
