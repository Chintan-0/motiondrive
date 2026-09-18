from motiondrive.controller_state import TrackingState, TrackingStateTracker
from motiondrive.steering import TrackingQuality


def test_starts_both_hands_lost():
    tracker = TrackingStateTracker()
    assert tracker.state == TrackingState.BOTH_HANDS_LOST


def test_both_hands_present_and_excellent_reports_excellent_after_recovery():
    tracker = TrackingStateTracker(recovery_frames=3)
    for _ in range(3):
        state = tracker.update(TrackingQuality.EXCELLENT, True, True)
    assert state == TrackingState.EXCELLENT


def test_brief_single_frame_dropout_does_not_report_loss_within_grace_period():
    tracker = TrackingStateTracker(loss_grace_frames=6, recovery_frames=3)
    for _ in range(10):
        tracker.update(TrackingQuality.EXCELLENT, True, True)
    # One missed frame -- well within grace period, should not read as lost.
    state = tracker.update(TrackingQuality.EXCELLENT, False, True)
    assert state != TrackingState.RIGHT_HAND_LOST


def test_right_hand_missing_past_grace_period_reports_right_hand_lost():
    tracker = TrackingStateTracker(loss_grace_frames=3, recovery_frames=3)
    for _ in range(10):
        tracker.update(TrackingQuality.EXCELLENT, True, True)
    state = None
    for _ in range(6):
        state = tracker.update(TrackingQuality.EXCELLENT, False, True)
    assert state == TrackingState.RIGHT_HAND_LOST


def test_left_hand_missing_past_grace_period_reports_left_hand_lost():
    tracker = TrackingStateTracker(loss_grace_frames=3, recovery_frames=3)
    for _ in range(10):
        tracker.update(TrackingQuality.EXCELLENT, True, True)
    state = None
    for _ in range(6):
        state = tracker.update(TrackingQuality.EXCELLENT, True, False)
    assert state == TrackingState.LEFT_HAND_LOST


def test_both_hands_missing_past_grace_period_reports_both_hands_lost():
    tracker = TrackingStateTracker(loss_grace_frames=3, recovery_frames=3)
    for _ in range(10):
        tracker.update(TrackingQuality.EXCELLENT, True, True)
    state = None
    for _ in range(6):
        state = tracker.update(TrackingQuality.EXCELLENT, False, False)
    assert state == TrackingState.BOTH_HANDS_LOST


def test_recovery_requires_stabilization_period_before_excellent():
    tracker = TrackingStateTracker(loss_grace_frames=2, recovery_frames=10)
    for _ in range(10):
        tracker.update(TrackingQuality.EXCELLENT, False, False)
    assert tracker.state == TrackingState.BOTH_HANDS_LOST
    # Hands come back -- should not instantly jump to EXCELLENT.
    state = tracker.update(TrackingQuality.EXCELLENT, True, True)
    assert state == TrackingState.RECOVERING
    for _ in range(3):
        state = tracker.update(TrackingQuality.EXCELLENT, True, True)
    assert state == TrackingState.RECOVERING  # still stabilizing
    for _ in range(10):
        state = tracker.update(TrackingQuality.EXCELLENT, True, True)
    assert state == TrackingState.EXCELLENT  # fully recovered


def test_unstable_quality_reported_when_both_hands_present_but_low_confidence():
    tracker = TrackingStateTracker(recovery_frames=1)
    for _ in range(3):
        state = tracker.update(TrackingQuality.UNSTABLE, True, True)
    assert state == TrackingState.UNSTABLE


def test_reset_returns_to_initial_state():
    tracker = TrackingStateTracker()
    for _ in range(20):
        tracker.update(TrackingQuality.EXCELLENT, True, True)
    tracker.reset()
    assert tracker.state == TrackingState.BOTH_HANDS_LOST


def test_never_reports_a_bare_error_state():
    tracker = TrackingStateTracker()
    all_states = set(TrackingState)
    for s in all_states:
        assert "error" not in s.value.lower()
