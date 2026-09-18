from motiondrive.gestures import (WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP,
                                    MIDDLE_TIP, RING_TIP, PINKY_TIP, compute_intensity,
                                    fist_intensity, index_curl_intensity, pinch_intensity)
from motiondrive.hand_tracking import HandFrame
from motiondrive.steering import Point2D


def _hand(overrides: dict) -> HandFrame:
    """Builds a 21-point HandFrame with all landmarks at a neutral spread-out
    position, then overrides specific indices for the gesture under test."""
    points = [Point2D(0.5, 0.5) for _ in range(21)]
    points[WRIST] = Point2D(0.5, 0.7)
    points[MIDDLE_MCP] = Point2D(0.5, 0.5)  # hand scale = 0.2 (wrist->middle_mcp)
    for idx, pt in overrides.items():
        points[idx] = pt
    return HandFrame(handedness="Right", palm_center=Point2D(0.5, 0.55),
                      confidence=0.9, landmarks=points)


def test_pinch_open_hand_is_low_intensity():
    hand = _hand({
        THUMB_TIP: Point2D(0.3, 0.5),
        INDEX_TIP: Point2D(0.7, 0.5),  # far apart relative to hand scale
    })
    assert pinch_intensity(hand) < 0.1


def test_pinch_touching_fingers_is_high_intensity():
    hand = _hand({
        THUMB_TIP: Point2D(0.5, 0.5),
        INDEX_TIP: Point2D(0.5, 0.5),  # touching
    })
    assert pinch_intensity(hand) > 0.9


def test_pinch_intensity_is_clamped_to_0_1():
    hand = _hand({
        THUMB_TIP: Point2D(0.0, 0.0),
        INDEX_TIP: Point2D(1.0, 1.0),  # absurdly far -> still clamps
    })
    v = pinch_intensity(hand)
    assert 0.0 <= v <= 1.0


def test_index_curl_extended_is_low_intensity():
    hand = _hand({INDEX_TIP: Point2D(0.5, 1.02)})  # far from wrist -> extended
    assert index_curl_intensity(hand) < 0.3


def test_index_curl_curled_is_high_intensity():
    hand = _hand({INDEX_TIP: Point2D(0.5, 0.65)})  # close to wrist -> curled
    assert index_curl_intensity(hand) > 0.7


def test_fist_open_hand_is_low_intensity():
    hand = _hand({
        INDEX_TIP: Point2D(0.5, 1.05),
        MIDDLE_TIP: Point2D(0.5, 1.05),
        RING_TIP: Point2D(0.5, 1.05),
        PINKY_TIP: Point2D(0.5, 1.05),
    })
    assert fist_intensity(hand) < 0.3


def test_fist_closed_hand_is_high_intensity():
    hand = _hand({
        INDEX_TIP: Point2D(0.5, 0.68),
        MIDDLE_TIP: Point2D(0.5, 0.68),
        RING_TIP: Point2D(0.5, 0.68),
        PINKY_TIP: Point2D(0.5, 0.68),
    })
    assert fist_intensity(hand) > 0.6


def test_compute_intensity_dispatches_by_name():
    hand = _hand({THUMB_TIP: Point2D(0.5, 0.5), INDEX_TIP: Point2D(0.5, 0.5)})
    assert compute_intensity(hand, "pinch") == pinch_intensity(hand)


def test_compute_intensity_falls_back_to_pinch_for_unknown_name():
    hand = _hand({THUMB_TIP: Point2D(0.5, 0.5), INDEX_TIP: Point2D(0.5, 0.5)})
    assert compute_intensity(hand, "unknown_gesture") == pinch_intensity(hand)
