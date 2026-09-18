import math

from motiondrive.calibration import Calibration
from motiondrive.steering import (Point2D, SteeringEngine, TrackingQuality,
                                    hand_distance, raw_wheel_angle_deg, apply_steering_curve)


def test_apply_steering_curve_gamma():
    assert apply_steering_curve(0.0) == 0.0
    assert apply_steering_curve(1.0) == 1.0
    assert apply_steering_curve(-1.0) == -1.0
    half_curved = apply_steering_curve(0.5, gamma=1.4)
    assert 0.0 < half_curved < 0.5


def test_raw_wheel_angle_level_hands_is_zero():
    left = Point2D(0.3, 0.5)
    right = Point2D(0.7, 0.5)
    assert raw_wheel_angle_deg(left, right) == 0.0


def test_raw_wheel_angle_right_hand_lower_is_positive():
    left = Point2D(0.3, 0.5)
    right = Point2D(0.7, 0.65)
    assert raw_wheel_angle_deg(left, right) > 0


def test_raw_wheel_angle_right_hand_higher_is_negative():
    left = Point2D(0.3, 0.5)
    right = Point2D(0.7, 0.35)
    assert raw_wheel_angle_deg(left, right) < 0


def test_hand_distance():
    left = Point2D(0.0, 0.0)
    right = Point2D(3.0, 4.0)
    assert hand_distance(left, right) == 5.0


def _level_points(offset_deg: float, dist=0.35, cx=0.5, cy=0.5):
    rad = math.radians(offset_deg)
    half = dist / 2
    left = Point2D(cx - math.cos(rad) * half, cy - math.sin(rad) * half)
    right = Point2D(cx + math.cos(rad) * half, cy + math.sin(rad) * half)
    return left, right


def test_engine_centers_at_zero_after_calibration():
    cal = Calibration(center_angle_deg=0.0, left_angle_deg=-30.0, right_angle_deg=30.0)
    engine = SteeringEngine(cal, sensitivity=50, smoothing=0, steering_range=50, dead_zone=0)
    left, right = _level_points(0.0)
    for _ in range(5):
        state = engine.update(left, right, confidence=1.0)
    assert abs(state.value) < 0.05
    assert state.quality == TrackingQuality.EXCELLENT


def test_engine_full_right_turn_approaches_positive_one():
    cal = Calibration(center_angle_deg=0.0, left_angle_deg=-30.0, right_angle_deg=30.0)
    engine = SteeringEngine(cal, sensitivity=100, smoothing=0, steering_range=100, dead_zone=0)
    left, right = _level_points(30.0)
    state = None
    for _ in range(20):
        state = engine.update(left, right, confidence=1.0)
    assert state.value > 0.7


def test_engine_full_left_turn_approaches_negative_one():
    cal = Calibration(center_angle_deg=0.0, left_angle_deg=-30.0, right_angle_deg=30.0)
    engine = SteeringEngine(cal, sensitivity=100, smoothing=0, steering_range=100, dead_zone=0)
    left, right = _level_points(-30.0)
    state = None
    for _ in range(20):
        state = engine.update(left, right, confidence=1.0)
    assert state.value < -0.7


def test_engine_dead_zone_suppresses_small_offsets():
    cal = Calibration(center_angle_deg=0.0, left_angle_deg=-30.0, right_angle_deg=30.0)
    engine = SteeringEngine(cal, sensitivity=50, smoothing=0, steering_range=50, dead_zone=20)
    left, right = _level_points(2.0)  # tiny offset, should be inside dead zone
    state = engine.update(left, right, confidence=1.0)
    assert state.value == 0.0


def test_engine_reports_lost_and_decays_to_center_when_hands_missing():
    cal = Calibration.default()
    engine = SteeringEngine(cal, lost_after_frames=3)
    left, right = _level_points(25.0)
    for _ in range(10):
        engine.update(left, right, confidence=1.0)
    assert abs(engine._last_state.value) > 0.05

    state = None
    for _ in range(20):
        state = engine.update(None, None)
    assert state.quality == TrackingQuality.LOST
    assert abs(state.value) < 0.05


def test_engine_low_confidence_marks_unstable_not_lost_immediately():
    cal = Calibration.default()
    engine = SteeringEngine(cal, lost_after_frames=10, min_confidence=0.6)
    left, right = _level_points(0.0)
    state = engine.update(left, right, confidence=0.2)
    assert state.quality == TrackingQuality.UNSTABLE


def test_reset_clears_state():
    cal = Calibration.default()
    engine = SteeringEngine(cal, sensitivity=100, smoothing=0)
    left, right = _level_points(20.0)
    for _ in range(10):
        engine.update(left, right)
    engine.reset()
    assert engine._smoothed_value == 0.0
    assert engine._missing_streak == 0
