"""Pure math: per-hand gesture intensity -> smoothed, calibrated throttle/brake.

Mirrors the shape of `steering.SteeringEngine` deliberately (separate
detection system, same lifecycle: update_settings/reset/update), but pedals
have their own rules the wheel doesn't: hysteresis (avoid accidental
activation + flicker), a hard zero on a missing hand (no gradual decay --
pedals are safety-sensitive, never leave one "stuck" mid-value), and a
brake-overrides-throttle conflict rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from motiondrive.calibration import Calibration


@dataclass
class PedalState:
    throttle: float  # 0.0 .. 1.0
    brake: float      # 0.0 .. 1.0


def _sensitivity_curve(x: float, sensitivity_0_100: int) -> float:
    """x in [0, 1]. Same shape as steering's curve: low sensitivity needs
    more gesture depth for the same output, high sensitivity is snappier."""
    s = max(0, min(100, sensitivity_0_100)) / 100.0
    exponent = 1.6 - (1.0 * s)
    return max(0.0, min(1.0, x)) ** exponent


class _PedalChannel:
    """One pedal's hysteresis + smoothing state machine."""

    def __init__(self, open_value: float, closed_value: float,
                 sensitivity: int, threshold_0_100: int, smoothing_0_100: int):
        self.open_value = open_value
        self.closed_value = closed_value
        self.sensitivity = sensitivity
        self.threshold_0_100 = threshold_0_100
        self.smoothing_0_100 = smoothing_0_100
        self._active = False
        self._smoothed = 0.0

    def _ema_alpha(self) -> float:
        s = max(0, min(100, self.smoothing_0_100)) / 100.0
        return 0.9 - (0.82 * s)

    def _normalize(self, raw: float) -> float:
        span = self.closed_value - self.open_value
        if abs(span) < 1e-4:
            return max(0.0, min(1.0, raw))
        return max(0.0, min(1.0, (raw - self.open_value) / span))

    def update(self, raw_intensity: Optional[float]) -> float:
        if raw_intensity is None:
            # Missing hand: never leave a pedal stuck -- hard reset, no decay.
            self._active = False
            self._smoothed = 0.0
            return 0.0

        norm = self._normalize(raw_intensity)
        activation = max(0.05, min(0.9, self.threshold_0_100 / 100.0))
        release = max(0.0, activation - 0.10)  # dead band between release..activation

        if not self._active and norm >= activation:
            self._active = True
        elif self._active and norm <= release:
            self._active = False

        if not self._active:
            target = 0.0
        else:
            # Rescale so output starts at 0 right at the activation point
            # and reaches 1.0 at full closure, instead of jumping straight
            # to `activation`'s worth of output.
            target = max(0.0, (norm - activation) / max(1e-4, 1.0 - activation))
            target = _sensitivity_curve(target, self.sensitivity)

        alpha = self._ema_alpha()
        self._smoothed = (alpha * target) + ((1 - alpha) * self._smoothed)
        self._smoothed = max(0.0, min(1.0, self._smoothed))
        return self._smoothed

    def reset(self) -> None:
        self._active = False
        self._smoothed = 0.0


class PedalEngine:
    def __init__(self, calibration: Calibration, throttle_sensitivity: int = 55,
                 brake_sensitivity: int = 55, gesture_threshold: int = 35,
                 pedal_smoothing: int = 40):
        self.calibration = calibration
        self._throttle = _PedalChannel(
            calibration.right_pinch_open, calibration.right_pinch_closed,
            throttle_sensitivity, gesture_threshold, pedal_smoothing)
        self._brake = _PedalChannel(
            calibration.left_pinch_open, calibration.left_pinch_closed,
            brake_sensitivity, gesture_threshold, pedal_smoothing)

    def update_settings(self, *, throttle_sensitivity=None, brake_sensitivity=None,
                         gesture_threshold=None, pedal_smoothing=None):
        if throttle_sensitivity is not None:
            self._throttle.sensitivity = throttle_sensitivity
        if brake_sensitivity is not None:
            self._brake.sensitivity = brake_sensitivity
        if gesture_threshold is not None:
            self._throttle.threshold_0_100 = gesture_threshold
            self._brake.threshold_0_100 = gesture_threshold
        if pedal_smoothing is not None:
            self._throttle.smoothing_0_100 = pedal_smoothing
            self._brake.smoothing_0_100 = pedal_smoothing

    def update_calibration(self, calibration: Calibration) -> None:
        self.calibration = calibration
        self._throttle.open_value = calibration.right_pinch_open
        self._throttle.closed_value = calibration.right_pinch_closed
        self._brake.open_value = calibration.left_pinch_open
        self._brake.closed_value = calibration.left_pinch_closed

    def reset(self) -> None:
        self._throttle.reset()
        self._brake.reset()

    def update(self, right_intensity: Optional[float], left_intensity: Optional[float]) -> PedalState:
        throttle = self._throttle.update(right_intensity)
        brake = self._brake.update(left_intensity)

        # Conflict rule: braking smoothly overrides throttle rather than
        # letting a game see both pedals pressed at once.
        if brake > 0.0:
            throttle *= max(0.0, 1.0 - brake)

        return PedalState(throttle=throttle, brake=brake)
