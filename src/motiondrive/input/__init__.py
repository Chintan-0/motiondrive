"""Swappable game-input backends. Every backend implements the same tiny
interface so the rest of the app never cares whether it's driving a virtual
Xbox controller or pressing keys."""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from motiondrive.logging_ import get_logger

log = get_logger(__name__)


class InputBackendError(RuntimeError):
    pass


class InputMode(str, Enum):
    GAMEPAD = "gamepad"
    KEYBOARD = "keyboard"
    NONE = "none"


class InputBackend(ABC):
    name: str = "base"

    @abstractmethod
    def apply(self, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False, player_id: int = 1) -> None:
        """steering: -1.0 (full left) .. 0.0 (center) .. +1.0 (full right).
        throttle/brake: 0.0 (released) .. 1.0 (fully pressed).
        shift: True (pressed) / False (released).
        player_id: 1 or 2."""

    def release_player(self, player_id: int) -> None:
        """Immediately release inputs for a specific player slot without affecting others."""
        self.release()

    @abstractmethod
    def release(self) -> None:
        """Immediately return to a neutral/no-input state for all players. Must never raise."""

    def close(self) -> None:
        self.release()


class NullBackend(InputBackend):
    """Used when input is stopped/disabled -- guarantees nothing is ever sent."""
    name = "none"

    def apply(self, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False, player_id: int = 1) -> None:
        pass

    def release_player(self, player_id: int) -> None:
        pass

    def release(self) -> None:
        pass


class VirtualGamepadBackend(InputBackend):
    """Drives virtual Xbox 360 controllers via vgamepad (backed by the
    ViGEmBus driver): Left Stick X for steering, Right Trigger for throttle,
    Left Trigger for brake, A button for shift. Supports Player 1 and Player 2."""
    name = "gamepad"

    def __init__(self):
        try:
            import vgamepad as vg
        except Exception as e:
            raise InputBackendError(
                "Virtual gamepad support isn't available. Install the ViGEmBus "
                "driver (bundled with the MotionDrive installer) or switch to Keyboard mode."
            ) from e
        self._vg = vg
        self._gamepads: dict[int, object] = {}
        try:
            self._gamepads[1] = vg.VX360Gamepad()
        except Exception as e:
            raise InputBackendError(
                "Couldn't create a virtual controller. Make sure the ViGEmBus "
                "driver is installed, then restart MotionDrive."
            ) from e
        log.info("Virtual gamepad backend ready (Player 1)")

    def _get_gamepad(self, player_id: int):
        if player_id not in self._gamepads:
            try:
                self._gamepads[player_id] = self._vg.VX360Gamepad()
                log.info("Virtual gamepad backend ready (Player %d)", player_id)
            except Exception:
                log.exception("Failed to create virtual gamepad for Player %d", player_id)
                return None
        return self._gamepads[player_id]

    def apply(self, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False, player_id: int = 1) -> None:
        gp = self._get_gamepad(player_id)
        if gp is None:
            return
        steering = max(-1.0, min(1.0, steering))
        throttle = max(0.0, min(1.0, throttle))
        brake = max(0.0, min(1.0, brake))
        axis_value = int(steering * 32767)
        gp.left_joystick(x_value=axis_value, y_value=0)
        gp.right_trigger_float(value_float=throttle)
        gp.left_trigger_float(value_float=brake)
        if shift:
            gp.press_button(button=self._vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
        else:
            gp.release_button(button=self._vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
        gp.update()

    def release_player(self, player_id: int) -> None:
        gp = self._gamepads.get(player_id)
        if gp is None:
            return
        for fn in (
            lambda: gp.left_joystick(x_value=0, y_value=0),
            lambda: gp.right_trigger_float(value_float=0.0),
            lambda: gp.left_trigger_float(value_float=0.0),
            lambda: gp.release_button(button=self._vg.XUSB_BUTTON.XUSB_GAMEPAD_A),
            lambda: gp.update(),
        ):
            try:
                fn()
            except Exception:
                pass

    def release(self) -> None:
        for pid in list(self._gamepads):
            self.release_player(pid)


DEFAULT_KEY_MAP = {"left": "a", "right": "d", "accelerate": "w", "brake": "s", "shift": "shift"}


class _HysteresisKey:
    """One logical control -> one physical key, with separate activate/
    release thresholds so it doesn't chatter right at the boundary (spec:
    steering activates at 0.15/releases at 0.08, pedals 0.20/0.10).

    Reports the REAL SendInput result, not an assumption: pydirectinput's
    keyDown()/keyUp() already return `insertedEvents == expectedEvents`
    (the actual Win32 return value), which was previously being discarded
    -- "no Python exception" is not the same thing as "the OS accepted the
    event", and this class now never conflates the two."""

    def __init__(self, logical_name: str, key: str, activate: float, release: float,
                 on_transition=None):
        self.logical_name = logical_name
        self.key = key
        self.activate = activate
        self.release = release
        self.is_down = False
        self.last_send_ok: bool | None = None
        self._on_transition = on_transition

    def _report(self, message: str, send_ok: bool, count: int = 1, error_code: int = 0) -> None:
        import time
        import ctypes
        self.last_send_ok = send_ok
        ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        if send_ok:
            status_str = f"SUCCESS (count={count})"
        else:
            err = error_code or ctypes.GetLastError()
            status_str = f"FAILED (count={count}, win32_err={err})"
        full = f"[{ts}] {message}  [SendInput: {status_str}]"
        log.info(full)
        if self._on_transition:
            self._on_transition(full)

    def update(self, magnitude: float, pdi) -> None:
        """magnitude: the *unsigned* strength of this control (e.g. abs(steering)
        for a directional key, or throttle/brake directly)."""
        if not self.is_down and magnitude >= self.activate:
            ok = bool(pdi.keyDown(self.key))
            count = 1 if ok else 0
            self.is_down = True
            self._report(f"{self.key.upper()} -> KEY DOWN ({self.logical_name})", ok, count=count)
        elif self.is_down and magnitude <= self.release:
            ok = bool(pdi.keyUp(self.key))
            count = 1 if ok else 0
            self.is_down = False
            self._report(f"{self.key.upper()} -> KEY UP ({self.logical_name})", ok, count=count)

    def force_up(self, pdi, reason: str = "") -> None:
        if self.is_down:
            ok = bool(pdi.keyUp(self.key))
            count = 1 if ok else 0
            self.is_down = False
            self._report(f"{self.key.upper()} -> KEY UP ({self.logical_name}{', ' + reason if reason else ''})", ok, count=count)



class _VirtualKeyAdapter:
    """Matches pydirectinput's keyDown(key)/keyUp(key) -> bool interface,
    backed by windows_input.send_virtual_key() (VK-code injection instead
    of scan-code). Advanced-diagnostics-only alternative -- scan-code via
    pydirectinput is the proven, tested default."""

    def keyDown(self, key: str) -> bool:
        from motiondrive.windows_input import send_virtual_key
        return send_virtual_key(key, True)

    def keyUp(self, key: str) -> bool:
        from motiondrive.windows_input import send_virtual_key
        return send_virtual_key(key, False)


import threading
import time


class _PwmSteeringController:
    """Managed worker thread for analog-to-digital PWM steering emulation.
    Magnitude maps to duty cycle over fixed period (default 100ms). Mutual
    exclusion guarantees LEFT and RIGHT are never held simultaneously.
    Zero steering or stop immediately cancels cycle and releases keys."""

    def __init__(self, left_key: _HysteresisKey, right_key: _HysteresisKey, pdi, period_sec: float = 0.100):
        self._left = left_key
        self._right = right_key
        self._pdi = pdi
        self.period_sec = period_sec
        self.steering = 0.0
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def set_steering(self, steering: float) -> None:
        with self._lock:
            self.steering = max(-1.0, min(1.0, steering))
            if abs(self.steering) < 0.05:
                self.steering = 0.0
                self._left.force_up(self._pdi, reason="PWM zero steering")
                self._right.force_up(self._pdi, reason="PWM zero steering")

    def stop(self) -> None:
        self._running = False
        with self._lock:
            self.steering = 0.0
            self._left.force_up(self._pdi, reason="PWM stop")
            self._right.force_up(self._pdi, reason="PWM stop")
        if self._thread.is_alive():
            self._thread.join(timeout=0.15)

    def _worker_loop(self) -> None:
        while self._running:
            with self._lock:
                steer = self.steering

            if abs(steer) < 0.05:
                time.sleep(0.015)
                continue

            duty = abs(steer)
            active_dur = max(0.005, self.period_sec * duty)
            idle_dur = max(0.005, self.period_sec * (1.0 - duty))

            target_key = self._left if steer < 0 else self._right
            other_key = self._right if steer < 0 else self._left

            # Mutual exclusion: force other key UP immediately
            other_key.force_up(self._pdi, reason="PWM mutual exclusion")

            # Press target key for active duration
            if not target_key.is_down:
                try:
                    self._pdi.keyDown(target_key.key)
                    target_key.is_down = True
                except Exception:
                    pass

            time.sleep(active_dur)

            # Release target key for idle duration if steering is not full 1.0 lock
            if duty < 0.98 and target_key.is_down:
                try:
                    self._pdi.keyUp(target_key.key)
                    target_key.is_down = False
                except Exception:
                    pass

            time.sleep(idle_dur)


DEFAULT_KEY_MAP_P2 = {"left": "left", "right": "right", "accelerate": "up", "brake": "down", "shift": "space"}


class KeyboardBackend(InputBackend):
    """Browser/keyboard input using SendInput (via pydirectinput, scan-code
    mode by default) -- real Windows-level key events that reach whatever
    window currently has focus (the browser game), not just this
    application. Configurable key mapping, proper KEY DOWN/UP state (never
    re-presses a held key), independent activate/release thresholds
    (hysteresis) for steering and each pedal, and a hard mutual-exclusion
    rule: braking always releases the throttle key first, same frame. Supports
    independent Player 1 and Player 2 key mappings and optional PWM steering emulation."""
    name = "keyboard"

    def __init__(self, key_map: dict | None = None, key_map_p2: dict | None = None,
                 steer_activate: float = 0.15, steer_release: float = 0.08,
                 pedal_activate: float = 0.20, pedal_release: float = 0.10,
                 on_transition=None, injection_mode: str = "scancode",
                 use_pwm: bool = False, pwm_period_sec: float = 0.100):
        if injection_mode == "virtual_key":
            self._pdi = _VirtualKeyAdapter()
        else:
            try:
                import pydirectinput as pdi
            except ImportError as e:
                raise InputBackendError(
                    "Keyboard input support isn't available on this system."
                ) from e
            pdi.PAUSE = 0
            pdi.FAILSAFE = False
            self._pdi = pdi
        self.injection_mode = injection_mode
        self.use_pwm = use_pwm

        km1 = {**DEFAULT_KEY_MAP, **(key_map or {})}
        km2 = {**DEFAULT_KEY_MAP_P2, **(key_map_p2 or {})}

        self._player_keys = {
            1: {
                "left": _HysteresisKey("left", km1["left"], steer_activate, steer_release, on_transition),
                "right": _HysteresisKey("right", km1["right"], steer_activate, steer_release, on_transition),
                "accelerate": _HysteresisKey("accelerate", km1["accelerate"], pedal_activate, pedal_release, on_transition),
                "brake": _HysteresisKey("brake", km1["brake"], pedal_activate, pedal_release, on_transition),
                "shift": _HysteresisKey("shift", km1.get("shift", "shift"), 0.5, 0.2, on_transition),
            },
            2: {
                "left": _HysteresisKey("p2_left", km2["left"], steer_activate, steer_release, on_transition),
                "right": _HysteresisKey("p2_right", km2["right"], steer_activate, steer_release, on_transition),
                "accelerate": _HysteresisKey("p2_accelerate", km2["accelerate"], pedal_activate, pedal_release, on_transition),
                "brake": _HysteresisKey("p2_brake", km2["brake"], pedal_activate, pedal_release, on_transition),
                "shift": _HysteresisKey("p2_shift", km2.get("shift", "space"), 0.5, 0.2, on_transition),
            }
        }
        self._keys = tuple(k for pdict in self._player_keys.values() for k in pdict.values())

        self._pwm_controllers: dict[int, _PwmSteeringController] = {}
        if use_pwm:
            for pid, pdict in self._player_keys.items():
                self._pwm_controllers[pid] = _PwmSteeringController(
                    pdict["left"], pdict["right"], self._pdi, period_sec=pwm_period_sec
                )

        log.info("Keyboard backend ready (P1: %s, P2: %s, use_pwm=%s)", km1, km2, use_pwm)

    def apply(self, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False, player_id: int = 1) -> None:
        pdict = self._player_keys.get(player_id)
        if not pdict:
            return
        pwm = self._pwm_controllers.get(player_id)
        if pwm:
            pwm.set_steering(steering)
        else:
            left_mag = max(0.0, -steering)
            right_mag = max(0.0, steering)
            pdict["left"].update(left_mag, self._pdi)
            pdict["right"].update(right_mag, self._pdi)

        pdict["brake"].update(brake, self._pdi)
        if pdict["brake"].is_down:
            pdict["accelerate"].force_up(self._pdi, reason="overridden by brake")
        else:
            pdict["accelerate"].update(throttle, self._pdi)

        shift_mag = 1.0 if shift else 0.0
        pdict["shift"].update(shift_mag, self._pdi)

    def key_states(self) -> dict:
        """Current pressed/released state, keyed by logical control name."""
        return {k.logical_name: (k.key, k.is_down) for k in self._keys}

    def send_result(self, logical_name: str) -> bool | None:
        for k in self._keys:
            if k.logical_name == logical_name:
                return k.last_send_ok
        return None

    def manual_send(self, logical_name: str, key_down: bool) -> bool:
        key_by_name = {k.logical_name: k for k in self._keys}
        channel = key_by_name.get(logical_name)
        if channel is None:
            return False
        ok = bool(self._pdi.keyDown(channel.key) if key_down else self._pdi.keyUp(channel.key))
        channel.is_down = key_down
        channel._report(f"{channel.key.upper()} -> {'KEY DOWN' if key_down else 'KEY UP'} "
                          f"({logical_name}, manual)", ok)
        return ok

    def release_player(self, player_id: int) -> None:
        pwm = self._pwm_controllers.get(player_id)
        if pwm:
            pwm.stop()
        pdict = self._player_keys.get(player_id)
        if pdict:
            for k in pdict.values():
                try:
                    k.force_up(self._pdi, reason=f"release player {player_id}")
                except Exception:
                    log.exception("Error releasing key %s (ignored)", k.logical_name)

    def release(self) -> None:
        for pid in list(self._player_keys):
            self.release_player(pid)

    def close(self) -> None:
        self.release()


def create_backend(mode: InputMode, key_map: dict | None = None, key_map_p2: dict | None = None, on_transition=None,
                    injection_mode: str = "scancode", use_pwm: bool = False) -> InputBackend:
    if mode == InputMode.GAMEPAD:
        return VirtualGamepadBackend()
    if mode == InputMode.KEYBOARD:
        return KeyboardBackend(key_map=key_map, key_map_p2=key_map_p2, on_transition=on_transition, injection_mode=injection_mode, use_pwm=use_pwm)
    return NullBackend()

