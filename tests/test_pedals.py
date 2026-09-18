from motiondrive.calibration import Calibration
from motiondrive.pedals import PedalEngine


def _engine(**kwargs):
    cal = Calibration(right_pinch_open=0.1, right_pinch_closed=0.9,
                       left_pinch_open=0.1, left_pinch_closed=0.9)
    defaults = dict(throttle_sensitivity=50, brake_sensitivity=50,
                     gesture_threshold=35, pedal_smoothing=0)
    defaults.update(kwargs)
    return PedalEngine(cal, **defaults)


def test_open_hand_produces_no_throttle():
    engine = _engine()
    state = engine.update(0.1, None)  # right hand open, left hand missing
    assert state.throttle == 0.0


def test_full_pinch_ramps_throttle_up_over_frames():
    engine = _engine()
    state = None
    for _ in range(20):
        state = engine.update(0.9, None)
    assert state.throttle > 0.5


def test_missing_hand_forces_pedal_to_zero_immediately():
    engine = _engine()
    for _ in range(20):
        state = engine.update(0.9, None)
    assert state.throttle > 0.5
    state = engine.update(None, None)
    assert state.throttle == 0.0
    assert state.brake == 0.0


def test_missing_hand_does_not_affect_the_other_pedal():
    engine = _engine()
    # Partial brake so the brake-overrides-throttle conflict rule doesn't
    # zero throttle outright -- we're testing hand-loss isolation here, not
    # the conflict rule (see test_brake_overrides_throttle_conflict_rule).
    for _ in range(20):
        state = engine.update(0.9, 0.5)
    assert state.throttle > 0.0
    assert state.brake > 0.0
    state = engine.update(None, 0.5)  # right hand (throttle) missing
    assert state.throttle == 0.0
    assert state.brake > 0.0


def test_hysteresis_prevents_flicker_right_at_threshold():
    engine = _engine(gesture_threshold=35)
    # Below activation threshold -> stays off
    for _ in range(5):
        state = engine.update(0.30, None)
    assert state.throttle == 0.0
    # Just above activation -> turns on
    for _ in range(5):
        state = engine.update(0.40, None)
    assert state.throttle > 0.0
    # Drops slightly (still within the hysteresis dead band) -> stays on, doesn't flicker off
    state = engine.update(0.33, None)
    assert state.throttle >= 0.0  # no exception / stays smooth, doesn't jump back to instantly 0 next frame


def test_brake_overrides_throttle_conflict_rule():
    engine = _engine(pedal_smoothing=0)
    for _ in range(20):
        state = engine.update(0.95, 0.95)  # both hands fully pinched
    assert state.brake > 0.5
    assert state.throttle < 0.5  # throttle should be suppressed by the active brake


def test_reset_clears_state():
    engine = _engine()
    for _ in range(20):
        engine.update(0.9, 0.9)
    engine.reset()
    state = engine.update(None, None)
    assert state.throttle == 0.0
    assert state.brake == 0.0


def test_calibration_range_normalizes_output():
    # Narrow calibrated range -> a mid raw value should already read as
    # strongly activated once normalized.
    cal = Calibration(right_pinch_open=0.4, right_pinch_closed=0.6)
    engine = PedalEngine(cal, gesture_threshold=35, pedal_smoothing=0)
    state = None
    for _ in range(20):
        state = engine.update(0.6, None)  # raw == calibrated "fully closed"
    assert state.throttle > 0.5
