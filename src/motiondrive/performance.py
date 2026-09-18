"""Frame-time profiling + FPS/CPU/RAM sampling for the diagnostics
Performance panel. Pure logic (no Qt), same pattern as controller_state.py/
diagnostics.py, so it's testable without a display and cheap enough to run
on the camera/tracking thread without becoming part of the problem itself
(that mistake -- unthrottled per-frame work on the hot path -- is exactly
what this feature exists to catch)."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

STAGE_NAMES = ["camera_capture", "image_conversion", "hand_tracking",
               "gesture_processing", "input_processing", "ui_handoff"]


class FpsCounter:
    """Rolling 1-second-window FPS counter -- call tick() once per event."""

    def __init__(self, window_seconds: float = 1.0):
        self._window = window_seconds
        self._timestamps: deque = deque()

    def tick(self, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        self._timestamps.append(now)
        cutoff = now - self._window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        return float(len(self._timestamps))

    @property
    def fps(self) -> float:
        if not self._timestamps:
            return 0.0
        now = time.time()
        cutoff = now - self._window
        return float(sum(1 for t in self._timestamps if t >= cutoff))


class FrameProfiler:
    """Measures wall-clock time spent in each named pipeline stage for one
    frame at a time. Use as: profiler.begin(); ...work...;
    profiler.mark('hand_tracking'); ...more work...; profiler.mark('gesture_processing')
    Each mark() records the elapsed time *since the previous mark/begin*."""

    def __init__(self):
        self._start = 0.0
        self._last = 0.0
        self.last_stage_times: dict = {}
        self._current: dict = {}

    def begin(self) -> None:
        self._start = time.perf_counter()
        self._last = self._start
        self._current = {}

    def mark(self, stage: str) -> None:
        now = time.perf_counter()
        self._current[stage] = (now - self._last) * 1000.0  # ms
        self._last = now

    def finish(self) -> float:
        """Call after the last mark(); returns total frame time in ms and
        stores the per-stage breakdown in `last_stage_times`."""
        total_ms = (time.perf_counter() - self._start) * 1000.0
        self.last_stage_times = dict(self._current)
        self.last_stage_times["total"] = total_ms
        return total_ms


@dataclass
class PerformanceSample:
    camera_fps: float = 0.0
    tracking_fps: float = 0.0
    stage_times_ms: dict = field(default_factory=dict)
    cpu_percent: Optional[float] = None    # None = unavailable, never faked
    ram_mb: Optional[float] = None
    gpu_percent: Optional[float] = None    # always None -- no reliable per-process GPU API used
    baseline_fps: Optional[float] = None
    regression_detected: bool = False


class PerformanceMonitor:
    """Establishes a baseline tracking FPS a few seconds after startup, then
    flags a regression if the current FPS drops well below it -- surfaced
    only in Developer Diagnostics, never a normal-user-facing warning
    (except the overlay's very small 'Low tracking performance' hint, which
    main_window wires separately)."""

    def __init__(self, baseline_settle_seconds: float = 5.0, regression_ratio: float = 0.5):
        self._baseline_settle_seconds = baseline_settle_seconds
        self._regression_ratio = regression_ratio
        self._started_at = time.time()
        self.baseline_fps: Optional[float] = None
        self._low_streak = 0
        self._psutil_process = None  # created lazily, kept alive: cpu_percent()
        # needs a persistent Process object to compute a delta between calls --
        # a fresh Process() every call always reports 0%.

    def reset(self) -> None:
        self._started_at = time.time()
        self.baseline_fps = None
        self._low_streak = 0

    def update(self, current_fps: float) -> bool:
        """Feed the latest tracking FPS; returns whether a regression is
        currently flagged. Requires a few consecutive low samples before
        flagging, so one slow frame doesn't trigger a false alarm."""
        elapsed = time.time() - self._started_at
        if self.baseline_fps is None:
            if elapsed >= self._baseline_settle_seconds and current_fps > 0:
                self.baseline_fps = current_fps
            return False

        if current_fps < self.baseline_fps * self._regression_ratio:
            self._low_streak += 1
        else:
            self._low_streak = 0
        return self._low_streak >= 3

    def try_get_process_stats(self):
        """Returns (cpu_percent, ram_mb) for this process, or (None, None)
        if psutil isn't available -- never invents a value. The first call
        after the process object is created always reads 0% (psutil needs
        one prior sample to compute a delta); it becomes meaningful from
        the second call onward, which is fine since this is only sampled
        every ~500ms, not read once and trusted immediately."""
        try:
            import psutil
            if self._psutil_process is None:
                self._psutil_process = psutil.Process()
                self._psutil_process.cpu_percent(interval=None)  # prime the delta baseline
                return None, None
            cpu = self._psutil_process.cpu_percent(interval=None)
            ram = self._psutil_process.memory_info().rss / (1024 * 1024)
            return cpu, ram
        except Exception:
            return None, None
