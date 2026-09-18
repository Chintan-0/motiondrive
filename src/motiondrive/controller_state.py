"""Controller state vs. tracking state -- deliberately two separate concepts
(spec: "these are two completely different states"). No Qt/engine imports
here, same as `steering`/`pedals`, so the state machine is fully testable
with plain frame-by-frame calls.

The critical rule this module exists to enforce: tracking loss must NEVER
by itself stop the controller. Only an explicit user action (Stop button,
tray Exit, ESC/F8 hotkey) or a fatal error does that -- see engine.py, which
is the only place still allowed to call stop_controller().
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from motiondrive.steering import TrackingQuality


class ControllerState(Enum):
    OFF = "off"
    RUNNING = "running"
    PAUSED = "paused"


class ControllerSource(str, Enum):
    HANDS = "hands"
    PHONE = "phone"


@dataclass
class NormalizedInput:
    steering: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    timestamp: float = 0.0
    sequence: int = 0


class TrackingState(Enum):
    EXCELLENT = "excellent"
    UNSTABLE = "unstable"
    RIGHT_HAND_LOST = "right_hand_lost"
    LEFT_HAND_LOST = "left_hand_lost"
    BOTH_HANDS_LOST = "both_hands_lost"
    RECOVERING = "recovering"          # hands back, still stabilizing


# Human-facing labels -- tracking loss is a normal condition, never "Error".
TRACKING_LABELS = {
    TrackingState.EXCELLENT: "Excellent tracking",
    TrackingState.UNSTABLE: "Tracking unstable",
    TrackingState.RIGHT_HAND_LOST: "Right hand lost — throttle disabled",
    TrackingState.LEFT_HAND_LOST: "Left hand lost — brake disabled",
    TrackingState.BOTH_HANDS_LOST: "Tracking paused — hands not detected",
    TrackingState.RECOVERING: "Tracking recovering...",
}


@dataclass
class _Loss:
    """Tracks how many consecutive frames a hand has been missing, so a
    single dropped landmark doesn't instantly read as 'lost'."""
    right_missing_streak: int = 0
    left_missing_streak: int = 0
    good_streak: int = 0  # consecutive good frames since the last loss


class TrackingStateTracker:
    """Feed it (quality, right_hand_present, left_hand_present) every frame;
    it returns a stable TrackingState with a short grace period on loss and
    a short stabilization period on recovery, both frame-count based
    (assume ~30fps, so ~0.4s recovery = 12 frames)."""

    def __init__(self, loss_grace_frames: int = 6, recovery_frames: int = 12):
        self.loss_grace_frames = loss_grace_frames
        self.recovery_frames = recovery_frames
        self._loss = _Loss()
        self._state = TrackingState.BOTH_HANDS_LOST

    def reset(self) -> None:
        self._loss = _Loss()
        self._state = TrackingState.BOTH_HANDS_LOST

    def update(self, quality: TrackingQuality, right_present: bool, left_present: bool) -> TrackingState:
        loss = self._loss

        loss.right_missing_streak = 0 if right_present else loss.right_missing_streak + 1
        loss.left_missing_streak = 0 if left_present else loss.left_missing_streak + 1

        right_lost = loss.right_missing_streak > self.loss_grace_frames
        left_lost = loss.left_missing_streak > self.loss_grace_frames

        if right_lost and left_lost:
            loss.good_streak = 0
            self._state = TrackingState.BOTH_HANDS_LOST
        elif right_lost:
            loss.good_streak = 0
            self._state = TrackingState.RIGHT_HAND_LOST
        elif left_lost:
            loss.good_streak = 0
            self._state = TrackingState.LEFT_HAND_LOST
        else:
            # Both hands present within the grace window.
            was_lost = self._state in (TrackingState.BOTH_HANDS_LOST,
                                        TrackingState.RIGHT_HAND_LOST,
                                        TrackingState.LEFT_HAND_LOST,
                                        TrackingState.RECOVERING)
            loss.good_streak += 1
            if was_lost and loss.good_streak < self.recovery_frames:
                self._state = TrackingState.RECOVERING
            elif quality == TrackingQuality.UNSTABLE:
                self._state = TrackingState.UNSTABLE
            else:
                self._state = TrackingState.EXCELLENT

        return self._state

    @property
    def state(self) -> TrackingState:
        return self._state
