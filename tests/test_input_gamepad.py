"""VirtualGamepadBackend tests using a faked-out vgamepad module, same
technique tests/test_input_keyboard.py uses for pydirectinput -- these run
without an actual ViGEmBus driver/virtual controller."""
import sys
import types

import pytest


class _FakeGamepad:
    def __init__(self, fail_calls=()):
        self.calls = []
        self._fail_calls = fail_calls

    def _maybe_fail(self, name):
        if name in self._fail_calls:
            raise RuntimeError(f"simulated ViGEm failure in {name}")

    def left_joystick(self, x_value, y_value):
        self.calls.append(("left_joystick", x_value, y_value))
        self._maybe_fail("left_joystick")

    def right_trigger_float(self, value_float):
        self.calls.append(("right_trigger_float", value_float))
        self._maybe_fail("right_trigger_float")

    def left_trigger_float(self, value_float):
        self.calls.append(("left_trigger_float", value_float))
        self._maybe_fail("left_trigger_float")

    def press_button(self, button):
        self.calls.append(("press_button", button))
        self._maybe_fail("press_button")

    def release_button(self, button):
        self.calls.append(("release_button", button))
        self._maybe_fail("release_button")

    def update(self):
        self.calls.append(("update",))
        self._maybe_fail("update")


def _install_fake_vgamepad(fail_calls=()):
    fake_gamepad = _FakeGamepad(fail_calls=fail_calls)
    fake_module = types.ModuleType("vgamepad")
    fake_module.VX360Gamepad = lambda: fake_gamepad
    fake_module.XUSB_BUTTON = types.SimpleNamespace(XUSB_GAMEPAD_A="A_BUTTON")
    sys.modules["vgamepad"] = fake_module
    return fake_gamepad


def _backend():
    sys.modules.pop("motiondrive.input", None)
    from motiondrive.input import VirtualGamepadBackend
    return VirtualGamepadBackend()


def test_apply_drives_stick_and_triggers():
    gp = _install_fake_vgamepad()
    backend = _backend()
    backend.apply(steering=0.5, throttle=0.8, brake=0.0)
    assert ("right_trigger_float", 0.8) in gp.calls
    assert ("update",) in gp.calls


def test_release_zeroes_everything_and_flushes():
    gp = _install_fake_vgamepad()
    backend = _backend()
    backend.apply(steering=1.0, throttle=1.0, brake=1.0)
    gp.calls.clear()
    backend.release()
    assert ("left_joystick", 0, 0) in gp.calls
    assert ("right_trigger_float", 0.0) in gp.calls
    assert ("left_trigger_float", 0.0) in gp.calls
    assert ("update",) in gp.calls


def test_release_still_flushes_update_when_an_earlier_axis_clear_raises():
    """Regression test: release() used to wrap all four calls (three axis
    clears + the final update() that actually sends them to the virtual
    device) in one try/except. If the FIRST call raised, update() never
    ran at all -- meaning whatever non-neutral axis values were already
    active on the real virtual controller from the last apply() were never
    actually flushed to neutral. Each step must be independently guarded
    so update() always still runs."""
    gp = _install_fake_vgamepad()
    backend = _backend()
    backend.apply(steering=1.0, throttle=1.0, brake=1.0)
    gp.calls.clear()
    gp._fail_calls = {"left_joystick"}  # only start failing once release() runs

    backend.release()  # must not raise despite left_joystick failing

    assert ("right_trigger_float", 0.0) in gp.calls
    assert ("left_trigger_float", 0.0) in gp.calls
    assert ("update",) in gp.calls  # the critical assertion: still flushed


def test_release_still_flushes_update_when_a_middle_axis_clear_raises():
    gp = _install_fake_vgamepad()
    backend = _backend()
    backend.apply(steering=1.0, throttle=1.0, brake=1.0)
    gp.calls.clear()
    gp._fail_calls = {"right_trigger_float"}

    backend.release()  # must not raise

    assert ("left_trigger_float", 0.0) in gp.calls
    assert ("update",) in gp.calls
