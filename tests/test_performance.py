import time

from motiondrive.performance import FpsCounter, FrameProfiler, PerformanceMonitor


def test_fps_counter_counts_ticks_within_window():
    counter = FpsCounter(window_seconds=1.0)
    now = 100.0
    for i in range(10):
        counter.tick(now + i * 0.01)  # 10 ticks within 0.09s
    assert counter.tick(now + 0.1) == 11


def test_fps_counter_evicts_old_ticks_outside_window():
    counter = FpsCounter(window_seconds=1.0)
    counter.tick(0.0)
    counter.tick(0.5)
    result = counter.tick(2.0)  # both earlier ticks now outside the 1s window
    assert result == 1.0


def test_frame_profiler_records_stage_times():
    profiler = FrameProfiler()
    profiler.begin()
    time.sleep(0.005)
    profiler.mark("camera_capture")
    time.sleep(0.005)
    profiler.mark("hand_tracking")
    total = profiler.finish()
    assert "camera_capture" in profiler.last_stage_times
    assert "hand_tracking" in profiler.last_stage_times
    assert profiler.last_stage_times["camera_capture"] > 0
    assert profiler.last_stage_times["hand_tracking"] > 0
    assert total >= profiler.last_stage_times["camera_capture"]


def test_performance_monitor_no_regression_before_baseline_settles():
    monitor = PerformanceMonitor(baseline_settle_seconds=1000)  # never settles in test
    assert monitor.update(5.0) is False
    assert monitor.baseline_fps is None


def test_performance_monitor_establishes_baseline_after_settle_period():
    monitor = PerformanceMonitor(baseline_settle_seconds=0.0)
    monitor.update(58.0)
    assert monitor.baseline_fps == 58.0


def test_performance_monitor_detects_sustained_regression():
    monitor = PerformanceMonitor(baseline_settle_seconds=0.0, regression_ratio=0.5)
    monitor.update(58.0)  # establishes baseline
    assert monitor.update(5.0) is False  # 1st low sample -- not yet flagged
    assert monitor.update(5.0) is False  # 2nd low sample
    assert monitor.update(5.0) is True   # 3rd consecutive low sample -- flagged


def test_performance_monitor_recovers_after_regression_clears():
    monitor = PerformanceMonitor(baseline_settle_seconds=0.0, regression_ratio=0.5)
    monitor.update(58.0)
    monitor.update(5.0)
    monitor.update(5.0)
    assert monitor.update(5.0) is True
    assert monitor.update(55.0) is False  # back to normal, streak resets


def test_performance_monitor_reset_clears_baseline():
    monitor = PerformanceMonitor(baseline_settle_seconds=0.0)
    monitor.update(58.0)
    assert monitor.baseline_fps is not None
    monitor.reset()
    assert monitor.baseline_fps is None


def test_try_get_process_stats_never_raises():
    monitor = PerformanceMonitor()
    cpu, ram = monitor.try_get_process_stats()
    # Either both real values or honestly (None, None) -- never a fake number.
    if cpu is None:
        assert ram is None
    else:
        assert isinstance(cpu, float) and isinstance(ram, float)
