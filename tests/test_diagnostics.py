from motiondrive.diagnostics import EventLog


def test_event_log_starts_empty():
    log = EventLog()
    assert len(log) == 0
    assert log.snapshot() == []


def test_event_log_add_returns_timestamped_entry():
    log = EventLog()
    entry = log.add("RIGHT HAND DETECTED")
    assert entry[1] == "RIGHT HAND DETECTED"
    assert ":" in entry[0]  # looks like a timestamp


def test_event_log_snapshot_preserves_order():
    log = EventLog()
    log.add("first")
    log.add("second")
    log.add("third")
    messages = [m for _, m in log.snapshot()]
    assert messages == ["first", "second", "third"]


def test_event_log_evicts_oldest_beyond_maxlen():
    log = EventLog(maxlen=3)
    for i in range(5):
        log.add(f"event-{i}")
    messages = [m for _, m in log.snapshot()]
    assert messages == ["event-2", "event-3", "event-4"]
    assert len(log) == 3


def test_event_log_clear():
    log = EventLog()
    log.add("something")
    log.clear()
    assert len(log) == 0
    assert log.snapshot() == []
