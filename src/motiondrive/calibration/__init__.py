"""Calibration wizard state machine + persisted calibration data.

Wheel steps (unchanged from the original steering-only flow):
    1. centered  - user holds hands centered -> capture center_angle_deg, hand_distance_baseline
    2. left      - user rotates fully left  -> capture left_angle_deg
    3. right     - user rotates fully right -> capture right_angle_deg

Pedal steps (appended after the wheel steps):
    4. right hand relaxed/open -> capture right_pinch_open
    5. right hand pinched      -> capture right_pinch_closed
    6. left hand relaxed/open  -> capture left_pinch_open
    7. left hand pinched       -> capture left_pinch_closed
    8. done

The captured wheel values are raw degrees from `steering.raw_wheel_angle_deg`;
the captured pinch values are raw 0..1 intensities from
`gestures.pinch_intensity` (or whichever gesture is configured).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from enum import Enum, auto
from typing import Optional

from motiondrive.logging_ import get_logger
from motiondrive.paths import CALIBRATION_FILE, ensure_dirs

log = get_logger(__name__)

MIN_LOCK_DEGREES = 12.0  # guard against a degenerate (near-zero range) calibration
MIN_PINCH_SPAN = 0.15    # guard against a degenerate (barely-moved) pinch calibration


@dataclass
class Calibration:
    center_angle_deg: float = 0.0
    left_angle_deg: float = -35.0
    right_angle_deg: float = 35.0
    hand_distance_baseline: float = 0.35  # normalized frame units
    calibrated: bool = False

    right_pinch_open: float = 0.15
    right_pinch_closed: float = 0.85
    left_pinch_open: float = 0.15
    left_pinch_closed: float = 0.85
    pedals_calibrated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def left_range_deg(self) -> float:
        return abs(self.center_angle_deg - self.left_angle_deg)

    @property
    def right_range_deg(self) -> float:
        return abs(self.right_angle_deg - self.center_angle_deg)

    @staticmethod
    def default() -> "Calibration":
        return Calibration()


class CalibrationStore:
    def __init__(self, path=None):
        self.path = path or CALIBRATION_FILE

    def load(self) -> Calibration:
        ensure_dirs()
        if not self.path.exists():
            return Calibration.default()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            known = {f: data[f] for f in Calibration.__dataclass_fields__ if f in data}
            return Calibration(**known)
        except Exception:
            log.exception("Failed to load calibration, using defaults")
            return Calibration.default()

    def save(self, cal: Calibration) -> None:
        ensure_dirs()
        self.path.write_text(json.dumps(cal.to_dict(), indent=2), encoding="utf-8")
        log.info("Calibration saved: center=%.1f left=%.1f right=%.1f pedals_calibrated=%s",
                  cal.center_angle_deg, cal.left_angle_deg, cal.right_angle_deg, cal.pedals_calibrated)


class CalibrationStep(Enum):
    INTRO = auto()
    CENTER = auto()
    ROTATE_LEFT = auto()
    ROTATE_RIGHT = auto()
    RIGHT_PINCH_OPEN = auto()
    RIGHT_PINCH_CLOSED = auto()
    LEFT_PINCH_OPEN = auto()
    LEFT_PINCH_CLOSED = auto()
    DONE = auto()


STEP_PROMPTS = {
    CalibrationStep.INTRO: "Place your hands on your imaginary steering wheel.",
    CalibrationStep.CENTER: "Keep your hands centered, then press Capture.",
    CalibrationStep.ROTATE_LEFT: "Rotate fully left, then press Capture.",
    CalibrationStep.ROTATE_RIGHT: "Rotate fully right, then press Capture.",
    CalibrationStep.RIGHT_PINCH_OPEN: "Relax your RIGHT hand (fingers open), then press Capture.",
    CalibrationStep.RIGHT_PINCH_CLOSED: "Pinch your RIGHT hand (thumb + index together), then press Capture.",
    CalibrationStep.LEFT_PINCH_OPEN: "Relax your LEFT hand (fingers open), then press Capture.",
    CalibrationStep.LEFT_PINCH_CLOSED: "Pinch your LEFT hand (thumb + index together), then press Capture.",
    CalibrationStep.DONE: "Calibration complete!",
}

# Steps in flow order, used for progress display ("Step N of len(...)").
STEP_ORDER = [
    CalibrationStep.INTRO, CalibrationStep.CENTER,
    CalibrationStep.ROTATE_LEFT, CalibrationStep.ROTATE_RIGHT,
    CalibrationStep.RIGHT_PINCH_OPEN, CalibrationStep.RIGHT_PINCH_CLOSED,
    CalibrationStep.LEFT_PINCH_OPEN, CalibrationStep.LEFT_PINCH_CLOSED,
    CalibrationStep.DONE,
]


class CalibrationWizard:
    """Drives the full wheel + pedal calibration flow. UI feeds it live
    samples every frame; wizard captures an averaged sample on each
    `capture()` call, for whichever quantity the current step needs."""

    def __init__(self):
        self.step = CalibrationStep.INTRO
        self._samples: list[float] = []
        self._distance_samples: list[float] = []
        self.result = Calibration.default()

    @property
    def prompt(self) -> str:
        return STEP_PROMPTS[self.step]

    def feed_sample(self, raw_angle_deg: float, hand_distance: float) -> None:
        """Call every frame while a wheel step (CENTER/ROTATE_LEFT/
        ROTATE_RIGHT) is active."""
        self._samples.append(raw_angle_deg)
        self._distance_samples.append(hand_distance)
        # keep only the most recent ~1.5s at 30fps
        self._samples = self._samples[-45:]
        self._distance_samples = self._distance_samples[-45:]

    def feed_pinch_sample(self, intensity: float) -> None:
        """Call every frame while a pinch step (RIGHT/LEFT_PINCH_OPEN/CLOSED)
        is active, with that hand's current gesture intensity."""
        self._samples.append(intensity)
        self._samples = self._samples[-45:]

    def _averaged(self) -> float:
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    def _averaged_distance(self) -> float:
        if not self._distance_samples:
            return 0.35
        return sum(self._distance_samples) / len(self._distance_samples)

    def capture(self) -> Optional[str]:
        """Capture the current step. Returns an error message if the capture
        was rejected (e.g. left/right too close to center), else None and
        advances to the next step."""
        value = self._averaged()
        if self.step == CalibrationStep.INTRO:
            self.step = CalibrationStep.CENTER
            self._samples.clear()
            return None
        if self.step == CalibrationStep.CENTER:
            self.result.center_angle_deg = value
            self.result.hand_distance_baseline = self._averaged_distance()
            self.step = CalibrationStep.ROTATE_LEFT
            self._samples.clear()
            return None
        if self.step == CalibrationStep.ROTATE_LEFT:
            if abs(value - self.result.center_angle_deg) < MIN_LOCK_DEGREES:
                return "That didn't look like a full left turn. Rotate your hands further left and try again."
            self.result.left_angle_deg = value
            self.step = CalibrationStep.ROTATE_RIGHT
            self._samples.clear()
            return None
        if self.step == CalibrationStep.ROTATE_RIGHT:
            if abs(value - self.result.center_angle_deg) < MIN_LOCK_DEGREES:
                return "That didn't look like a full right turn. Rotate your hands further right and try again."
            same_side = (value - self.result.center_angle_deg) * (
                self.result.left_angle_deg - self.result.center_angle_deg
            ) > 0
            if same_side:
                return "Right turn looked the same direction as left. Rotate the other way and try again."
            self.result.right_angle_deg = value
            self.result.calibrated = True
            self.step = CalibrationStep.RIGHT_PINCH_OPEN
            self._samples.clear()
            return None
        if self.step == CalibrationStep.RIGHT_PINCH_OPEN:
            self.result.right_pinch_open = value
            self.step = CalibrationStep.RIGHT_PINCH_CLOSED
            self._samples.clear()
            return None
        if self.step == CalibrationStep.RIGHT_PINCH_CLOSED:
            if value - self.result.right_pinch_open < MIN_PINCH_SPAN:
                return "That didn't look like a full pinch. Bring your right thumb and index finger closer and try again."
            self.result.right_pinch_closed = value
            self.step = CalibrationStep.LEFT_PINCH_OPEN
            self._samples.clear()
            return None
        if self.step == CalibrationStep.LEFT_PINCH_OPEN:
            self.result.left_pinch_open = value
            self.step = CalibrationStep.LEFT_PINCH_CLOSED
            self._samples.clear()
            return None
        if self.step == CalibrationStep.LEFT_PINCH_CLOSED:
            if value - self.result.left_pinch_open < MIN_PINCH_SPAN:
                return "That didn't look like a full pinch. Bring your left thumb and index finger closer and try again."
            self.result.left_pinch_closed = value
            self.result.pedals_calibrated = True
            self.step = CalibrationStep.DONE
            self._samples.clear()
            return None
        return None

    def is_done(self) -> bool:
        return self.step == CalibrationStep.DONE
