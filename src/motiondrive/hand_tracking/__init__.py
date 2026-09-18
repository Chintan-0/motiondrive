"""Wraps MediaPipe Hands so the rest of the app never imports mediapipe
directly. Emits plain dataclasses (HandFrame) that `steering` and `ui`
consume."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from motiondrive.logging_ import get_logger
from motiondrive.steering import Point2D

log = get_logger(__name__)

# MediaPipe landmark indices we treat as "stable" (avoid fingertips, which
# jitter the most).
WRIST = 0
INDEX_MCP = 5
PINKY_MCP = 17


@dataclass
class HandFrame:
    handedness: str          # "Left" or "Right" (as seen by the camera, mirror-corrected)
    palm_center: Point2D     # normalized [0,1] image coords, averaged stable landmarks
    confidence: float
    landmarks: list          # list[Point2D], all 21 points, for overlay drawing


@dataclass
class TrackingResult:
    left: Optional[HandFrame]
    right: Optional[HandFrame]

    @property
    def both_present(self) -> bool:
        return self.left is not None and self.right is not None

    @property
    def confidence(self) -> float:
        vals = [h.confidence for h in (self.left, self.right) if h is not None]
        return min(vals) if vals else 0.0


class HandTracker:
    """Thin, lazily-imported MediaPipe Hands wrapper. Import of `mediapipe`
    is deferred to __init__ so unit tests / non-CV modules never need it
    installed."""

    def __init__(self, min_detection_confidence: float = 0.6,
                 min_tracking_confidence: float = 0.5, model_complexity: int = 0):
        import mediapipe as mp  # deferred import

        self._mp = mp
        self._hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            model_complexity=model_complexity,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        log.info("MediaPipe Hands initialized")

    def process(self, rgb_frame) -> TrackingResult:
        """rgb_frame: HxWx3 numpy array, RGB order, already mirrored so the
        user's right hand appears on the right side of the frame."""
        results = self._hands.process(rgb_frame)
        left: Optional[HandFrame] = None
        right: Optional[HandFrame] = None

        if results.multi_hand_landmarks and results.multi_handedness:
            for lm_set, handedness in zip(results.multi_hand_landmarks, results.multi_handedness):
                label = handedness.classification[0].label  # "Left"/"Right"
                score = handedness.classification[0].score
                pts = [Point2D(p.x, p.y) for p in lm_set.landmark]
                cx = (pts[WRIST].x + pts[INDEX_MCP].x + pts[PINKY_MCP].x) / 3.0
                cy = (pts[WRIST].y + pts[INDEX_MCP].y + pts[PINKY_MCP].y) / 3.0
                frame = HandFrame(handedness=label, palm_center=Point2D(cx, cy),
                                   confidence=score, landmarks=pts)
                # MediaPipe's label is computed on the *unmirrored* input; since
                # we feed it an already-mirrored (selfie-view) frame, its
                # "Left"/"Right" already matches what the user sees on screen.
                if label == "Left":
                    left = frame
                else:
                    right = frame

        return TrackingResult(left=left, right=right)

    def close(self) -> None:
        self._hands.close()

    HAND_CONNECTIONS_INDEX_PAIRS = [
        (0, 1), (1, 2), (2, 3), (3, 4),           # thumb
        (0, 5), (5, 6), (6, 7), (7, 8),           # index
        (5, 9), (9, 10), (10, 11), (11, 12),      # middle
        (9, 13), (13, 14), (14, 15), (15, 16),    # ring
        (13, 17), (17, 18), (18, 19), (19, 20),   # pinky
        (0, 17),                                   # palm base
    ]
