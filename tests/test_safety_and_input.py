from motiondrive.input import InputBackend, NullBackend
from motiondrive.safety import SafetyWatchdog


class RecordingBackend(InputBackend):
    name = "recording"

    def __init__(self):
        self.applied = []
        self.released = False

    def apply(self, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False, player_id: int = 1) -> None:
        self.applied.append((steering, throttle, brake, shift))

    def release(self) -> None:
        self.released = True


class ExplodingBackend(InputBackend):
    name = "exploding"

    def apply(self, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False, player_id: int = 1) -> None:
        raise RuntimeError("device unplugged mid-frame")

    def release(self) -> None:
        raise RuntimeError("device already gone")


def test_watchdog_applies_to_armed_backend():
    backend = RecordingBackend()
    wd = SafetyWatchdog()
    wd.arm(backend)
    wd.apply(0.5, 0.2, 0.0, False)
    assert backend.applied == [(0.5, 0.2, 0.0, False)]


def test_watchdog_ignores_apply_when_not_armed():
    backend = RecordingBackend()
    wd = SafetyWatchdog()
    wd.apply(0.5, 0.0, 0.0)  # never armed
    assert backend.applied == []


def test_watchdog_release_all_calls_backend_release_and_disarms():
    backend = RecordingBackend()
    wd = SafetyWatchdog()
    wd.arm(backend)
    wd.release_all("test stop")
    assert backend.released is True
    assert wd.active is False
    wd.apply(1.0, 1.0, 1.0)  # should be ignored now
    assert backend.applied == []


def test_watchdog_release_callback_fires():
    backend = RecordingBackend()
    wd = SafetyWatchdog()
    reasons = []
    wd.set_release_callback(reasons.append)
    wd.arm(backend)
    wd.release_all("ESC pressed")
    assert reasons == ["ESC pressed"]


def test_watchdog_never_raises_when_backend_explodes_on_apply():
    wd = SafetyWatchdog()
    wd.arm(ExplodingBackend())
    wd.apply(0.3, 0.0, 0.0)  # must not raise
    assert wd.active is False  # auto-released for safety


def test_watchdog_never_raises_when_backend_explodes_on_release():
    wd = SafetyWatchdog()
    wd.arm(ExplodingBackend())
    wd.release_all("shutdown")  # must not raise
    assert wd.active is False


def test_null_backend_is_noop():
    backend = NullBackend()
    backend.apply(1.0, 1.0, 1.0)
    backend.release()  # must not raise
