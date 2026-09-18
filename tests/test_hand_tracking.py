"""Tests the HandTracker's geometry/labeling logic using a faked-out
mediapipe module, so these run even in environments without the real
mediapipe wheel installed."""
import sys
import types
from unittest.mock import MagicMock

import pytest


def _install_fake_mediapipe():
    fake_mp = types.ModuleType("mediapipe")
    fake_solutions = types.ModuleType("mediapipe.solutions")
    fake_hands_mod = types.ModuleType("mediapipe.solutions.hands")

    class FakeHands:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._next_result = None

        def process(self, rgb_frame):
            return self._next_result

        def close(self):
            pass

    fake_hands_mod.Hands = FakeHands
    fake_solutions.hands = fake_hands_mod
    fake_mp.solutions = fake_solutions
    sys.modules["mediapipe"] = fake_mp
    sys.modules["mediapipe.solutions"] = fake_solutions
    sys.modules["mediapipe.solutions.hands"] = fake_hands_mod
    return fake_mp


def _make_landmark_set(coords):
    class LM:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    class LMSet:
        def __init__(self, points):
            self.landmark = [LM(x, y) for x, y in points]

    return LMSet(coords)


def test_palm_center_and_handedness_labeling(monkeypatch):
    _install_fake_mediapipe()
    # reload to make sure our HandTracker module picks up the fake mediapipe
    sys.modules.pop("motiondrive.hand_tracking", None)
    from motiondrive.hand_tracking import HandTracker, WRIST, INDEX_MCP, PINKY_MCP

    tracker = HandTracker.__new__(HandTracker)  # bypass __init__ (avoids real mediapipe)
    import mediapipe as mp
    tracker._mp = mp
    tracker._hands = mp.solutions.hands.Hands()

    coords = [(0.5, 0.5)] * 21
    coords[WRIST] = (0.40, 0.60)
    coords[INDEX_MCP] = (0.44, 0.50)
    coords[PINKY_MCP] = (0.42, 0.55)
    landmark_set = _make_landmark_set(coords)

    classification = MagicMock()
    classification.label = "Left"
    classification.score = 0.91
    handedness = MagicMock()
    handedness.classification = [classification]

    result = MagicMock()
    result.multi_hand_landmarks = [landmark_set]
    result.multi_handedness = [handedness]
    tracker._hands._next_result = result

    tracking = tracker.process(rgb_frame=None)

    assert tracking.left is not None
    assert tracking.right is None
    assert tracking.left.handedness == "Left"
    assert tracking.left.confidence == pytest.approx(0.91)
    expected_x = (0.40 + 0.44 + 0.42) / 3.0
    expected_y = (0.60 + 0.50 + 0.55) / 3.0
    assert tracking.left.palm_center.x == pytest.approx(expected_x)
    assert tracking.left.palm_center.y == pytest.approx(expected_y)
    assert len(tracking.left.landmarks) == 21


def test_no_hands_detected_returns_empty_result():
    _install_fake_mediapipe()
    sys.modules.pop("motiondrive.hand_tracking", None)
    from motiondrive.hand_tracking import HandTracker
    import mediapipe as mp

    tracker = HandTracker.__new__(HandTracker)
    tracker._mp = mp
    tracker._hands = mp.solutions.hands.Hands()

    result = MagicMock()
    result.multi_hand_landmarks = None
    result.multi_handedness = None
    tracker._hands._next_result = result

    tracking = tracker.process(rgb_frame=None)
    assert tracking.left is None
    assert tracking.right is None
    assert tracking.both_present is False
    assert tracking.confidence == 0.0
