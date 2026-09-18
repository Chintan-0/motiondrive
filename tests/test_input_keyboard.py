"""KeyboardBackend tests using a faked-out pydirectinput module, same
technique tests/test_hand_tracking.py uses for mediapipe -- these run
without an actual keyboard/display."""
import sys
import types

import pytest


def _install_fake_pydirectinput(fail_keys=()):
    """fail_keys: logical keys whose keyDown/keyUp should return False,
    matching the real pydirectinput's `insertedEvents == expectedEvents`
    contract -- so tests can prove MotionDrive propagates a real failure
    instead of assuming success."""
    fake = types.ModuleType("pydirectinput")
    fake.PAUSE = 0
    fake.FAILSAFE = False
    fake.events = []

    def keyDown(key):
        fake.events.append(("down", key))
        return key not in fail_keys

    def keyUp(key):
        fake.events.append(("up", key))
        return key not in fail_keys

    fake.keyDown = keyDown
    fake.keyUp = keyUp
    sys.modules["pydirectinput"] = fake
    return fake


def _backend():
    sys.modules.pop("motiondrive.input", None)
    from motiondrive.input import KeyboardBackend
    return KeyboardBackend()


def test_default_key_map_used_when_none_given():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=-1.0, throttle=0.0, brake=0.0)
    assert ("down", "a") in fake.events


def test_configurable_key_map():
    fake = _install_fake_pydirectinput()
    from motiondrive.input import KeyboardBackend
    backend = KeyboardBackend(key_map={"left": "left", "right": "right",
                                        "accelerate": "up", "brake": "down"})
    backend.apply(steering=-1.0, throttle=0.0, brake=0.0)
    assert ("down", "left") in fake.events


def test_steering_left_holds_key_down_without_repeating():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    for _ in range(10):
        backend.apply(steering=-0.9, throttle=0.0, brake=0.0)
    down_events = [e for e in fake.events if e == ("down", "a")]
    assert len(down_events) == 1  # pressed once, held -- never re-pressed


def test_steering_hysteresis_activate_and_release():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=-0.20, throttle=0.0, brake=0.0)  # above activate (0.15)
    assert backend.key_states()["left"] == ("a", True)
    backend.apply(steering=-0.10, throttle=0.0, brake=0.0)  # between release/activate -- stays down
    assert backend.key_states()["left"] == ("a", True)
    backend.apply(steering=-0.05, throttle=0.0, brake=0.0)  # below release (0.08) -- releases
    assert backend.key_states()["left"] == ("a", False)


def test_steering_never_presses_both_left_and_right():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=-0.9, throttle=0.0, brake=0.0)
    backend.apply(steering=0.9, throttle=0.0, brake=0.0)
    states = backend.key_states()
    assert not (states["left"][1] and states["right"][1])


def test_pedal_hysteresis_activate_and_release():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=0.0, throttle=0.25, brake=0.0)  # above activate (0.20)
    assert backend.key_states()["accelerate"] == ("w", True)
    backend.apply(steering=0.0, throttle=0.05, brake=0.0)  # below release (0.10)
    assert backend.key_states()["accelerate"] == ("w", False)


def test_brake_overrides_accelerate_releases_w_same_frame():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=0.0, throttle=0.9, brake=0.0)
    assert backend.key_states()["accelerate"] == ("w", True)
    backend.apply(steering=0.0, throttle=0.9, brake=0.9)
    states = backend.key_states()
    assert states["accelerate"] == ("w", False)
    assert states["brake"] == ("s", True)


def test_never_presses_both_accelerate_and_brake():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    for _ in range(5):
        backend.apply(steering=0.0, throttle=0.9, brake=0.9)
    states = backend.key_states()
    assert not (states["accelerate"][1] and states["brake"][1])


def test_release_clears_all_keys_regardless_of_prior_state():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=-0.9, throttle=0.9, brake=0.0)
    assert backend.key_states()["left"][1] and backend.key_states()["accelerate"][1]
    backend.release()
    states = backend.key_states()
    assert not any(down for _, down in states.values())
    assert ("up", "a") in fake.events
    assert ("up", "w") in fake.events


def test_release_is_a_noop_when_nothing_was_pressed():
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.release()  # must not raise, must not send spurious events
    assert fake.events == []


def test_key_states_reports_all_four_logical_controls():
    _install_fake_pydirectinput()
    backend = _backend()
    states = backend.key_states()
    assert {"left", "right", "accelerate", "brake", "shift"}.issubset(states.keys())


def test_send_result_reflects_real_sendinput_failure_not_assumed_success():
    # "a" (the left key) is configured to fail -- MotionDrive must report
    # that honestly rather than treating "no exception" as success.
    _install_fake_pydirectinput(fail_keys={"a"})
    backend = _backend()
    backend.apply(steering=-0.9, throttle=0.0, brake=0.0)  # activates "left" -> presses "a"
    assert backend.send_result("left") is False


def test_send_result_reflects_real_sendinput_success():
    _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=-0.9, throttle=0.0, brake=0.0)
    assert backend.send_result("left") is True


def test_send_result_is_none_before_any_transition():
    _install_fake_pydirectinput()
    backend = _backend()
    assert backend.send_result("left") is None


def test_manual_send_bypasses_hysteresis_and_reports_real_result():
    fake = _install_fake_pydirectinput(fail_keys={"w"})
    backend = _backend()
    ok = backend.manual_send("accelerate", True)
    assert ok is False
    assert ("down", "w") in fake.events
    assert backend.key_states()["accelerate"] == ("w", True)  # state still tracked even on a failed send


def test_release_of_one_key_raising_does_not_block_the_others():
    """Regression test: release() used to wrap its whole for-loop in a
    single try/except, so an exception releasing one key aborted the loop
    entirely -- leaving every key after it in iteration order still
    physically held down despite this being the app's one "never leave a
    key stuck" safety path. Each key's release must be independently
    guarded."""
    fake = _install_fake_pydirectinput()
    backend = _backend()
    backend.apply(steering=-0.9, throttle=0.9, brake=0.0)  # presses "a" (left) and "w" (accelerate)
    assert backend.key_states()["left"][1] and backend.key_states()["accelerate"][1]

    # Make releasing "a" (the first key in iteration order) raise.
    real_key_up = fake.keyUp

    def flaky_key_up(key):
        if key == "a":
            raise RuntimeError("simulated SendInput failure")
        return real_key_up(key)

    fake.keyUp = flaky_key_up

    backend.release()  # must not raise, and must still release "w"

    assert backend.key_states()["accelerate"] == ("w", False)
