"""Pure math: a single hand's landmarks -> a normalized 0..1 gesture
"activation intensity". Deliberately separate from `steering` (spec: each
control gets its own detection logic) -- finger gestures must never leak
into the wheel-rotation calculation and vice versa.

No OpenCV/MediaPipe/Qt imports here, same as `steering` -- fully unit
testable with plain floats.
"""
from __future__ import annotations

import math
from typing import Literal

from motiondrive.hand_tracking import HandFrame
from motiondrive.steering import Point2D

WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_TIP = 20

GestureName = Literal["pinch", "index_curl", "fist"]


def _dist(a: Point2D, b: Point2D) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _hand_scale(hand: HandFrame) -> float:
    """Wrist-to-middle-MCP distance: a stable per-frame reference for the
    user's hand size / distance from the camera, so gesture thresholds don't
    depend on how close the user is sitting."""
    scale = _dist(hand.landmarks[WRIST], hand.landmarks[MIDDLE_MCP])
    return max(scale, 1e-4)


def pinch_intensity(hand: HandFrame) -> float:
    """0.0 = fingers fully open/apart, 1.0 = thumb and index touching."""
    scale = _hand_scale(hand)
    raw = _dist(hand.landmarks[THUMB_TIP], hand.landmarks[INDEX_TIP]) / scale
    # Typical open-hand pinch distance is roughly 1.1x the hand scale;
    # closed (touching) is ~0. Clamp+invert to a 0..1 intensity.
    intensity = 1.0 - min(1.0, raw / 1.1)
    return max(0.0, intensity)


def index_curl_intensity(hand: HandFrame) -> float:
    """0.0 = index finger extended straight, 1.0 = curled into the palm."""
    scale = _hand_scale(hand)
    raw = _dist(hand.landmarks[INDEX_TIP], hand.landmarks[WRIST]) / scale
    # Extended index tip sits ~1.6x hand-scale from the wrist; curled it
    # collapses back down toward ~0.6x.
    intensity = (1.6 - raw) / (1.6 - 0.6)
    return max(0.0, min(1.0, intensity))


def fist_intensity(hand: HandFrame) -> float:
    """0.0 = open hand, 1.0 = all four fingertips curled near the palm."""
    scale = _hand_scale(hand)
    tips = (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
    avg = sum(_dist(hand.landmarks[t], hand.landmarks[WRIST]) for t in tips) / (len(tips) * scale)
    intensity = (1.7 - avg) / (1.7 - 0.7)
    return max(0.0, min(1.0, intensity))


_DISPATCH = {
    "pinch": pinch_intensity,
    "index_curl": index_curl_intensity,
    "fist": fist_intensity,
}


def compute_intensity(hand: HandFrame, gesture: GestureName) -> float:
    fn = _DISPATCH.get(gesture, pinch_intensity)
    return fn(hand)
