"""MotionDriveEngine.start_camera() failure-path tests -- constructing the
real engine is cheap/safe (no camera is opened until start_camera() is
called, see tests/test_settings_and_profiles.py::test_apply_browser_profiles_to_engine
for the same pattern), so only HandTracker/CameraStream themselves are
faked via monkeypatch on the names engine.py imported them under."""
from motiondrive.camera import CameraUnavailableError


class _FakeHandTracker:
    """Records every instance created and whether it was ever closed, so a
    test can prove a failed camera start doesn't leak one of these."""
    instances = []

    def __init__(self):
        self.closed = False
        _FakeHandTracker.instances.append(self)

    def close(self):
        self.closed = True


class _FailingCameraStream:
    def __init__(self, index, width, height, fps):
        pass

    def start(self, on_frame, on_error=None):
        raise CameraUnavailableError("camera in use by another app")


def test_failed_camera_start_closes_the_just_created_hand_tracker(monkeypatch):
    """Regression test: start_camera() used to construct HandTracker()
    before attempting camera.start(); if that raised CameraUnavailableError,
    the already-constructed tracker was left referenced on self.tracker and
    never closed -- leaking one MediaPipe tracker instance per failed
    attempt (e.g. every retry while the webcam is in use by another app)."""
    import motiondrive.engine as engine_mod
    _FakeHandTracker.instances.clear()
    monkeypatch.setattr(engine_mod, "HandTracker", _FakeHandTracker)
    monkeypatch.setattr(engine_mod, "CameraStream", _FailingCameraStream)

    eng = engine_mod.MotionDriveEngine()
    errors = []
    eng.cameraError.connect(errors.append)

    eng.start_camera()

    assert len(_FakeHandTracker.instances) == 1
    assert _FakeHandTracker.instances[0].closed is True
    assert eng.tracker is None
    assert eng.camera is None
    assert eng.camera_running is False
    assert len(errors) == 1


def test_repeated_failed_camera_starts_do_not_accumulate_open_trackers(monkeypatch):
    import motiondrive.engine as engine_mod
    _FakeHandTracker.instances.clear()
    monkeypatch.setattr(engine_mod, "HandTracker", _FakeHandTracker)
    monkeypatch.setattr(engine_mod, "CameraStream", _FailingCameraStream)

    eng = engine_mod.MotionDriveEngine()
    for _ in range(10):
        eng.start_camera()

    assert len(_FakeHandTracker.instances) == 10
    assert all(t.closed for t in _FakeHandTracker.instances)  # none left dangling
