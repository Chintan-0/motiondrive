"""Composition root: wires camera -> hand_tracking -> steering -> safety/input
together and exposes Qt signals so the UI thread can react safely (frame
callbacks arrive on a background capture thread; Qt queues the signal
delivery onto the UI thread automatically for cross-thread connections)."""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from motiondrive.calibration import Calibration, CalibrationStore
from motiondrive.camera import CameraStream, CameraUnavailableError
from motiondrive.controller_state import ControllerState, TrackingState, TrackingStateTracker
from motiondrive.diagnostics import EventLog
from motiondrive.gestures import compute_intensity
from motiondrive.hand_tracking import HandTracker, TrackingResult
from motiondrive.input import InputBackendError, InputMode, create_backend
from motiondrive.logging_ import get_logger
from motiondrive.pedals import PedalEngine, PedalState
from motiondrive.performance import FpsCounter, FrameProfiler, PerformanceMonitor, PerformanceSample
from motiondrive.profiles import Profile
from motiondrive.safety import SafetyWatchdog
from motiondrive.settings import Settings, SettingsStore
from motiondrive.steering import Point2D, SteeringEngine, SteeringState, TrackingQuality
from motiondrive.phone.server import PhoneControllerServer

log = get_logger(__name__)


class MotionDriveEngine(QObject):
    # image, TrackingResult, SteeringState, PedalState, fps
    frameReady = Signal(QImage, object, object, object, float)
    cameraError = Signal(str)
    controllerStateChanged = Signal(object, str)            # ControllerState, reason
    trackingStateChanged = Signal(object)                     # TrackingState
    trackingHintChanged = Signal(str)
    testSweepValue = Signal(float, float, float)             # steering, throttle, brake
    keyStateChanged = Signal(object)                          # {logical_name: (key, is_down)}
    diagnosticEvent = Signal(str, str)                          # timestamp, message
    keyTestResult = Signal(str, str, bool, bool)                 # name, key, down_ok, up_ok
    holdTestResult = Signal(str, str, str)                        # key, down_ts, up_ts
    performanceSample = Signal(object)                             # PerformanceSample
    performanceRegressionChanged = Signal(bool)                     # for the small overlay hint only
    notepadTestResult = Signal(bool)                                 # True if all 4 keys sent successfully
    mutedStateChanged = Signal(bool)                                 # is_muted

    def __init__(self):
        super().__init__()
        self._muted = False
        self.settings_store = SettingsStore()
        self.calibration_store = CalibrationStore()
        self.settings: Settings = self.settings_store.load()
        self.calibration: Calibration = self.calibration_store.load()

        self.camera: Optional[CameraStream] = None
        self.tracker: Optional[HandTracker] = None
        self.steering_engine = SteeringEngine(
            self.calibration,
            sensitivity=self.settings.sensitivity,
            smoothing=self.settings.smoothing,
            steering_range=self.settings.steering_range,
            dead_zone=self.settings.dead_zone,
        )
        self.pedal_engine = PedalEngine(
            self.calibration,
            throttle_sensitivity=self.settings.throttle_sensitivity,
            brake_sensitivity=self.settings.brake_sensitivity,
            gesture_threshold=self.settings.gesture_threshold,
            pedal_smoothing=self.settings.pedal_smoothing,
        )
        self.tracking_tracker = TrackingStateTracker()
        self.event_log = EventLog()
        self.last_sendinput_test_ok: Optional[bool] = None
        self._last_logged_tracking_state: Optional[TrackingState] = None
        self.safety = SafetyWatchdog()
        self.safety.set_release_callback(
            lambda reason: self.controllerStateChanged.emit(ControllerState.OFF, reason))

        self.phone_server = PhoneControllerServer(self)
        self.phone_server.input_received.connect(self._on_phone_input_received)
        self.phone_server.connection_lost.connect(self._on_phone_connection_lost)

        self.camera_running = False
        self.controller_state = ControllerState.OFF
        self._low_conf_streak = 0
        self._last_hint_time = 0.0
        self._sweep_timer: Optional[QTimer] = None
        self._sweep_started_backend = False

        # Performance: separate camera-capture-FPS from tracking-FPS (spec:
        # never conflate the two -- a drop in one without the other tells
        # you exactly which stage is the bottleneck), frame-skip so we don't
        # run MediaPipe inference on every single camera frame, and a
        # profiler that's itself throttled so measuring performance never
        # becomes part of the performance problem.
        self.tracking_fps_counter = FpsCounter()
        self.frame_profiler = FrameProfiler()
        self.performance_monitor = PerformanceMonitor()
        self._frame_counter = 0
        self._last_perf_emit = 0.0
        self._last_tracking_result = None
        self._last_steering_state: Optional[SteeringState] = None
        self._last_pedal_state: Optional[PedalState] = None
        self._last_regression_state = False
        self.game_mode_active = False

    @property
    def controller_running(self) -> bool:
        """True whenever a driving session is active (RUNNING or PAUSED) --
        kept as a simple bool for call sites that only care 'is a session
        active', distinct from the tri-state `controller_state`."""
        return self.controller_state != ControllerState.OFF

    def _key_map(self) -> dict:
        s = self.settings
        return {"left": s.key_left, "right": s.key_right,
                "accelerate": s.key_accelerate, "brake": s.key_brake}

    def _log_event(self, message: str) -> None:
        """Feeds both the in-memory diagnostics EventLog and the regular
        log file -- state changes / key transitions only, never per-frame
        raw values (spec: don't spam)."""
        ts, msg = self.event_log.add(message)
        self.diagnosticEvent.emit(ts, msg)

    # ---------------------------------------------------------------- camera
    def start_camera(self) -> None:
        if self.camera_running:
            return
        try:
            self.tracker = HandTracker()
        except Exception as e:
            log.exception("Hand tracker init failed")
            self.cameraError.emit("Hand tracking couldn't start. Try restarting MotionDrive.")
            return
        self.camera = CameraStream(
            index=self.settings.camera_index,
            width=self.settings.camera_width,
            height=self.settings.camera_height,
            fps=self.settings.camera_fps,
        )
        try:
            self.camera.start(self._on_frame, self._on_camera_error)
            self.camera_running = True
            self.tracking_tracker.reset()
            self.tracking_fps_counter = FpsCounter()
            self.performance_monitor.reset()
            self._log_event("Camera started")
            self._log_event("Hand tracker initialized")
        except Exception:
            # Broadened beyond just CameraUnavailableError: cv2.VideoCapture
            # construction/configuration can raise other exception types
            # too (e.g. a malformed backend/index edge case), and those
            # used to propagate straight out of start_camera() uncaught --
            # violating "never show a raw traceback" for what's ultimately
            # still just a camera problem, not an application bug.
            log.exception("Camera failed to start")
            # Without this cleanup, self.tracker (already constructed just
            # above) and self.camera stayed referenced despite the failed
            # start -- camera_running correctly stays False, but a
            # subsequent start_camera() retry would construct a brand new
            # HandTracker() on top of the still-alive, never-closed one
            # from this failed attempt. Repeated failed starts (e.g. the
            # webcam is in use by another app) would leak one MediaPipe
            # tracker instance per retry.
            if self.tracker:
                try:
                    self.tracker.close()
                except Exception:
                    log.exception("Error closing hand tracker after failed camera start (ignored)")
                self.tracker = None
            self.camera = None
            self.cameraError.emit(
                "Camera couldn't be started. Close other apps using your webcam, "
                "select another camera, or restart MotionDrive."
            )

    def stop_camera(self) -> None:
        self.stop_controller("Camera stopped")
        if self.camera:
            self.camera.stop()
            self.camera = None
        if self.tracker:
            self.tracker.close()
            self.tracker = None
        self.camera_running = False

    def _on_camera_error(self, message: str) -> None:
        self.camera_running = False
        self.stop_controller("Camera disconnected")
        self.cameraError.emit(message)

    # ------------------------------------------------------------ controller
    def start_controller(self) -> None:
        if self.controller_state == ControllerState.RUNNING:
            self.stop_controller("Switching controller source")

        source = getattr(self.settings, "controller_source", "hands")
        if source == "hands" and not self.camera_running:
            self.cameraError.emit("Start the camera before starting the controller.")
            return
        try:
            mode = InputMode(self.settings.input_mode)
            backend = create_backend(mode, key_map=self._key_map(), key_map_p2=self._key_map_p2(), on_transition=self._log_event, injection_mode=self.settings.keyboard_injection_mode)
        except InputBackendError as e:
            self.controllerStateChanged.emit(ControllerState.OFF, str(e))
            return
        self.safety.arm(backend)
        self.steering_engine.reset()
        self.pedal_engine.reset()
        self.tracking_tracker.reset()
        self.controller_state = ControllerState.RUNNING

        if source == "phone":
            self.phone_server.start()
            self._log_event(f"Phone Controller active (P1: {self.phone_server.get_local_url(1)}, P2: {self.phone_server.get_local_url(2)})")

        self.controllerStateChanged.emit(ControllerState.RUNNING, f"Controller started ({source.upper()})")
        log.info("Controller started with source=%s backend=%s", source, backend.name)
        profile_name = self.settings.active_profile
        if mode == InputMode.KEYBOARD:
            km = self._key_map()
            self._log_event(f"Game profile: {profile_name}")
            self._log_event(f"Input mode: BROWSER KEYBOARD")
            self._log_event(f"Keyboard API: Windows SendInput")
            self._log_event(f"Steering: {km['left'].upper()} / {km['right'].upper()}")
            self._log_event(f"Throttle: {km['accelerate'].upper()}")
            self._log_event(f"Brake: {km['brake'].upper()}")
        else:
            self._log_event(f"Game profile: {profile_name}")
            self._log_event(f"Input mode: VIRTUAL GAMEPAD")
            self._log_event("Steering: Left Stick X")
            self._log_event("Throttle: Right Trigger (RT)")
            self._log_event("Brake: Left Trigger (LT)")

    def stop_controller(self, reason: str = "Stopped by user") -> None:
        """The only path allowed to fully end a driving session."""
        if self.controller_state == ControllerState.OFF:
            return
        self.controller_state = ControllerState.OFF
        if self.phone_server.running:
            self.phone_server.stop()
        self.safety.release_all(reason)
        self._log_event(f"Controller stopped: {reason}")

    @property
    def is_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        if self._muted == muted:
            return
        self._muted = muted
        if self._muted:
            self.safety.release_all("Global Emergency Mute")
            self._log_event("Global Emergency Mute: ACTIVE")
        else:
            if self.controller_state == ControllerState.RUNNING:
                try:
                    mode = InputMode(self.settings.input_mode)
                    backend = create_backend(mode, key_map=self._key_map(), key_map_p2=self._key_map_p2(), on_transition=self._log_event, injection_mode=self.settings.keyboard_injection_mode)
                    self.safety.arm(backend)
                except Exception as e:
                    log.exception("Could not re-arm backend on unmute: %s", e)
            self._log_event("Global Emergency Mute: OFF")
        self.mutedStateChanged.emit(self._muted)

    def toggle_mute(self) -> bool:
        self.set_muted(not self._muted)
        return self._muted

    def _key_map_p2(self) -> dict:
        s = self.settings
        return {
            "left": getattr(s, "key_p2_left", "left"),
            "right": getattr(s, "key_p2_right", "right"),
            "accelerate": getattr(s, "key_p2_accelerate", "up"),
            "brake": getattr(s, "key_p2_brake", "down"),
            "shift": getattr(s, "key_p2_shift", "space"),
        }

    def _on_phone_input_received(self, player_id: int, steering: float, throttle: float, brake: float, shift: bool, timestamp: float) -> None:
        source = getattr(self.settings, "controller_source", "hands")
        if self._muted or source != "phone" or self.controller_state != ControllerState.RUNNING:
            return
        self.safety.apply_player(player_id, steering, throttle, brake, shift)

    def _on_phone_connection_lost(self, player_id: int) -> None:
        source = getattr(self.settings, "controller_source", "hands")
        if source == "phone":
            self.safety.release_player(player_id, f"Player {player_id} connection lost")
            self._log_event(f"Player {player_id} phone connection lost — inputs neutralized")

    def emergency_stop(self) -> None:
        self.stop_controller("Emergency stop")

    def pause_controller(self) -> None:
        if self.controller_state != ControllerState.RUNNING:
            return
        self.controller_state = ControllerState.PAUSED
        self.safety.apply(0.0, 0.0, 0.0)
        self.controllerStateChanged.emit(ControllerState.PAUSED, "Controller paused")
        log.info("Controller paused")
        self._log_event("Controller paused")

    def resume_controller(self) -> None:
        if self.controller_state != ControllerState.PAUSED:
            return
        self.controller_state = ControllerState.RUNNING
        self.controllerStateChanged.emit(ControllerState.RUNNING, "Controller resumed")
        log.info("Controller resumed")
        self._log_event("Controller resumed")

    def toggle_pause(self) -> None:
        if self.controller_state == ControllerState.RUNNING:
            self.pause_controller()
        elif self.controller_state == ControllerState.PAUSED:
            self.resume_controller()

    # ------------------------------------------------------------ game mode
    def enable_game_mode(self) -> None:
        """Spec's 'Game Mode Optimization': automatically switch to the
        lightest tracking preset the instant the user commits to actually
        playing (START & PLAY), regardless of their normal Settings choice.
        The UI layer (main_window) separately dials back overlay landmark
        rendering and diagnostics repaint rate for the same reason."""
        if self.game_mode_active or not self.settings.game_mode_optimization:
            return
        self.game_mode_active = True
        self.performance_monitor.reset()  # new baseline under the lighter preset
        self._log_event("Game Mode Optimization: ON")

    def disable_game_mode(self) -> None:
        if not self.game_mode_active:
            return
        self.game_mode_active = False
        self.performance_monitor.reset()
        self._log_event("Game Mode Optimization: OFF")

    # ---------------------------------------------------------------- frames
    def _on_frame(self, bgr_frame: np.ndarray) -> None:
        try:
            import cv2
            from motiondrive.settings import TRACKING_PERFORMANCE_PRESETS

            self.frame_profiler.begin()
            mirrored = cv2.flip(bgr_frame, 1)
            rgb = cv2.cvtColor(mirrored, cv2.COLOR_BGR2RGB)
            self.frame_profiler.mark("image_conversion")

            # Adaptive tracking FPS (spec: don't process every camera frame,
            # and resize before inference rather than running the full-res
            # frame through MediaPipe) -- landmarks are already normalized
            # 0..1 by MediaPipe, so a resized inference input needs no
            # coordinate rescaling on the way back out.
            perf_key = "performance" if self.game_mode_active else self.settings.tracking_performance
            preset = TRACKING_PERFORMANCE_PRESETS.get(perf_key, TRACKING_PERFORMANCE_PRESETS["balanced"])
            self._frame_counter += 1
            run_inference = preset["frame_skip"] <= 1 or (self._frame_counter % preset["frame_skip"] == 0)

            if run_inference:
                inference_frame = rgb
                if preset["inference_scale"] < 1.0:
                    inference_frame = cv2.resize(
                        rgb, None, fx=preset["inference_scale"], fy=preset["inference_scale"],
                        interpolation=cv2.INTER_LINEAR)
                tracking: TrackingResult = self.tracker.process(inference_frame)
                self._last_tracking_result = tracking
                self.tracking_fps_counter.tick()
            else:
                tracking = self._last_tracking_result or TrackingResult(left=None, right=None)
            self.frame_profiler.mark("hand_tracking")

            left = tracking.left.palm_center if tracking.left else None
            right = tracking.right.palm_center if tracking.right else None
            state: SteeringState = self.steering_engine.update(left, right, tracking.confidence)

            # Throttle/brake are their own detection system (spec: never
            # derive every control from one measurement) -- right hand's
            # gesture drives throttle, left hand's drives brake, each
            # independently zeroed the instant its hand is missing.
            right_intensity = (compute_intensity(tracking.right, self.settings.accelerate_gesture)
                                if tracking.right else None)
            left_intensity = (compute_intensity(tracking.left, self.settings.brake_gesture)
                               if tracking.left else None)
            pedals: PedalState = self.pedal_engine.update(right_intensity, left_intensity)
            # NOTE: no per-frame log.debug() here on purpose -- this runs on
            # the camera/tracking thread at up to 60fps, and even DEBUG-level
            # logging costs real string-formatting + file-I/O time on that
            # thread, directly competing with inference for CPU. State
            # changes/transitions are logged elsewhere (_log_event, key
            # transitions in input/__init__.py); raw values are only ever
            # sampled at low frequency for the diagnostics UI (see
            # _maybe_emit_performance_sample below), never logged every frame.
            self.frame_profiler.mark("gesture_processing")

            tracking_state: TrackingState = self.tracking_tracker.update(
                state.quality, tracking.right is not None, tracking.left is not None)
            self.trackingStateChanged.emit(tracking_state)
            if tracking_state != self._last_logged_tracking_state:
                self._log_event(f"Tracking: {tracking_state.value}")
                self._last_logged_tracking_state = tracking_state

            # Tracking loss NEVER stops the controller (spec's core safety
            # rule) -- SteeringEngine/PedalEngine already neutralize their
            # own output on missing hands, so RUNNING just keeps applying
            # whatever they compute, safely. Only PAUSED forces an explicit
            # neutral regardless of what the engines see.
            if self._muted:
                self.safety.apply(0.0, 0.0, 0.0)
            elif self.controller_state == ControllerState.RUNNING:
                self.safety.apply(state.value, pedals.throttle, pedals.brake)
            elif self.controller_state == ControllerState.PAUSED:
                self.safety.apply(0.0, 0.0, 0.0)

            if self.settings.input_mode == "keyboard":
                backend = getattr(self.safety, "_backend", None)
                if backend is not None and hasattr(backend, "key_states"):
                    self.keyStateChanged.emit(backend.key_states())
            self.frame_profiler.mark("input_processing")

            h, w, _ = rgb.shape
            qimage = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
            fps = self.camera.actual_fps if self.camera else 0.0
            self.frameReady.emit(qimage, tracking, state, pedals, fps)
            self._maybe_emit_hint(tracking, w, h)
            self.frame_profiler.mark("ui_handoff")
            self.frame_profiler.finish()
            self._maybe_emit_performance_sample(fps)
        except Exception:
            log.exception("Frame processing error; stopping controller for safety")
            self.stop_controller("Processing error")

    def _maybe_emit_performance_sample(self, camera_fps: float) -> None:
        """Throttled to ~2/sec -- sampling performance must never itself
        become a source of the performance problem (spec's own warning)."""
        now = time.time()
        if now - self._last_perf_emit < 0.5:
            return
        self._last_perf_emit = now

        tracking_fps = self.tracking_fps_counter.fps
        cpu, ram = self.performance_monitor.try_get_process_stats()
        regressed = self.performance_monitor.update(tracking_fps)
        if regressed != getattr(self, "_last_regression_state", False):
            self._last_regression_state = regressed
            self.performanceRegressionChanged.emit(regressed)
            if regressed:
                self._log_event(
                    f"⚠ Performance drop detected: baseline "
                    f"{self.performance_monitor.baseline_fps:.0f} FPS, current {tracking_fps:.0f} FPS")

        sample = PerformanceSample(
            camera_fps=camera_fps,
            tracking_fps=tracking_fps,
            stage_times_ms=dict(self.frame_profiler.last_stage_times),
            cpu_percent=cpu,
            ram_mb=ram,
            gpu_percent=None,
            baseline_fps=self.performance_monitor.baseline_fps,
            regression_detected=regressed,
        )
        self.performanceSample.emit(sample)

    def _maybe_emit_hint(self, tracking: TrackingResult, w: int, h: int) -> None:
        now = time.time()
        if now - self._last_hint_time < 2.5:
            return
        hint = None
        if tracking.left is None and tracking.right is None:
            hint = "Make sure both hands are visible."
        elif tracking.left is None or tracking.right is None:
            hint = "Show your other hand to the camera."
        elif tracking.confidence < 0.6:
            hint = "Improve lighting or move slightly closer to the camera."
        if hint:
            self._last_hint_time = now
            self.trackingHintChanged.emit(hint)

    # --------------------------------------------------------------- helpers
    def apply_settings(self, settings: Settings) -> None:
        old_key_map = self._key_map()
        old_mode = self.settings.input_mode
        old_perf = self.settings.tracking_performance
        self.settings = settings
        self.settings_store.save(settings)
        if settings.tracking_performance != old_perf:
            self.performance_monitor.reset()  # new preset invalidates the old FPS baseline

        # Hot-rebind the keyboard backend if the user edits the key mapping
        # (or flips input mode) while a session is already RUNNING/PAUSED --
        # otherwise the live backend keeps using whichever keys were mapped
        # when the controller was started, silently ignoring later edits.
        backend_changed = settings.input_mode != old_mode
        keymap_changed = settings.input_mode == "keyboard" and self._key_map() != old_key_map
        if self.controller_running and (backend_changed or keymap_changed):
            self._rebind_backend()

        self.steering_engine.update_settings(
            sensitivity=settings.sensitivity,
            smoothing=settings.smoothing,
            steering_range=settings.steering_range,
            dead_zone=settings.dead_zone,
            preset=settings.steering_preset,
        )
        self.pedal_engine.update_settings(
            throttle_sensitivity=settings.throttle_sensitivity,
            brake_sensitivity=settings.brake_sensitivity,
            gesture_threshold=settings.gesture_threshold,
            pedal_smoothing=settings.pedal_smoothing,
        )

    def _rebind_backend(self) -> None:
        """Swap the live input backend for one matching the current
        settings, without ending the driving session or leaving a key/axis
        stuck: release the old backend's inputs first, then arm the new one."""
        try:
            mode = InputMode(self.settings.input_mode)
            new_backend = create_backend(mode, key_map=self._key_map(), key_map_p2=self._key_map_p2(), on_transition=self._log_event, injection_mode=self.settings.keyboard_injection_mode)
        except InputBackendError as e:
            log.warning("Could not rebind input backend after settings change: %s", e)
            return
        try:
            self.safety._backend.release()  # release the OLD backend's keys/axes first
        except Exception:
            log.exception("Error releasing previous backend during rebind (ignored)")
        self.safety.arm(new_backend)  # no OFF flicker -- arm() alone keeps controllerStateChanged quiet
        log.info("Input backend rebound to %s", new_backend.name)

    def apply_calibration(self, calibration: Calibration) -> None:
        self.calibration = calibration
        self.calibration_store.save(calibration)
        self.steering_engine.calibration = calibration
        self.pedal_engine.update_calibration(calibration)

    def apply_profile(self, profile: Profile) -> None:
        self.settings.input_mode = profile.input_mode
        self.settings.sensitivity = profile.sensitivity
        self.settings.smoothing = profile.smoothing
        self.settings.steering_range = profile.steering_range
        self.settings.dead_zone = profile.dead_zone
        self.settings.throttle_sensitivity = profile.throttle_sensitivity
        self.settings.brake_sensitivity = profile.brake_sensitivity
        self.settings.gesture_threshold = profile.gesture_threshold
        self.settings.pedal_smoothing = profile.pedal_smoothing
        self.settings.accelerate_gesture = profile.accelerate_gesture
        self.settings.brake_gesture = profile.brake_gesture
        self.settings.key_left = getattr(profile, "key_left", "a")
        self.settings.key_right = getattr(profile, "key_right", "d")
        self.settings.key_accelerate = getattr(profile, "key_accelerate", "w")
        self.settings.key_brake = getattr(profile, "key_brake", "s")
        self.settings.active_profile = profile.name
        # If a specific game_exe_path is already configured, that stays the
        # launch target regardless of preset (a real installed game beats a
        # browser URL); otherwise START & PLAY opens this preset's browser
        # game, if it has one (built-in presets do, CUSTOM doesn't).
        self.settings.game_url = getattr(profile, "game_url", "")
        self.apply_settings(self.settings)

    def shutdown(self) -> None:
        self.stop_camera()

    # --------------------------------------------------------- gamepad test
    def run_output_sweep(self) -> None:
        """Independent of the camera/hands: drives the current input backend
        through a short steering/throttle/brake sweep so the user can
        confirm the OS-level axes actually move (e.g. in Windows' "Set up
        USB game controllers" panel) without needing a game or hands in
        frame. Reuses the existing backend if the controller is already
        running; otherwise creates and releases a temporary one."""
        if self._sweep_timer is not None:
            return  # sweep already in progress

        self._sweep_started_backend = False
        if not self.safety.active:
            try:
                mode = InputMode(self.settings.input_mode)
                backend = create_backend(mode, key_map=self._key_map(), on_transition=self._log_event, injection_mode=self.settings.keyboard_injection_mode)
            except InputBackendError as e:
                self.controllerStateChanged.emit(ControllerState.OFF, str(e))
                return
            self.safety.arm(backend)
            self._sweep_started_backend = True

        steps = [
            (-1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0), (0.0, 0.0, 0.0),
        ]
        self._sweep_steps = list(steps)
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(450)
        self._sweep_timer.timeout.connect(self._advance_sweep)
        self._sweep_timer.start()
        self._advance_sweep()

    def _advance_sweep(self) -> None:
        if not self._sweep_steps:
            self._sweep_timer.stop()
            self._sweep_timer = None
            self.testSweepValue.emit(0.0, 0.0, 0.0)
            if self._sweep_started_backend:
                self.safety.release_all("Gamepad test finished")
            return
        steering, throttle, brake = self._sweep_steps.pop(0)
        self.safety.apply(steering, throttle, brake)
        self.testSweepValue.emit(steering, throttle, brake)

    # ---------------------------------------------------- input diagnostics
    def _raw_pdi(self):
        """Returns an object with keyDown(key)/keyUp(key) -> bool, honoring
        the configured injection mode (scancode via pydirectinput, the
        proven default; virtual_key via windows_input.py). Every diagnostic
        test path funnels through this one place so "which injection mode"
        is answered consistently everywhere, not just in real driving."""
        if self.settings.keyboard_injection_mode == "virtual_key":
            from motiondrive.input import _VirtualKeyAdapter
            return _VirtualKeyAdapter()
        import pydirectinput as pdi
        pdi.PAUSE = 0
        pdi.FAILSAFE = False
        return pdi

    def _send_raw_key(self, key: str, key_down: bool) -> bool:
        """The one place that calls the real Windows key-injection API for
        diagnostics, and reports its ACTUAL return value -- never assumes
        success just because no Python exception was raised (that was the
        bug: pydirectinput's keyDown/keyUp already return a real
        `insertedEvents == expectedEvents` boolean from SendInput(), it was
        just being discarded everywhere it was called)."""
        try:
            pdi = self._raw_pdi()
            ok = bool(pdi.keyDown(key) if key_down else pdi.keyUp(key))
        except Exception:
            log.exception("Raw key send failed (%s %s)", key, "DOWN" if key_down else "UP")
            ok = False
        self._log_event(f"{key.upper()} -> KEY {'DOWN' if key_down else 'UP'}  "
                          f"[SendInput: {'SUCCESS' if ok else 'FAILED'}]")
        return ok

    def test_physical_key(self, logical_name: str) -> None:
        """Spec-mandated distinction: this reports whether the Windows
        SendInput *call itself* succeeded, never whether a game reacted to
        it -- that can't be known from here."""
        key = self._key_map().get(logical_name, "?")
        down_ok = self._send_raw_key(key, True)

        def _release():
            up_ok = self._send_raw_key(key, False)
            self.keyTestResult.emit(logical_name, key, down_ok, up_ok)

        QTimer.singleShot(150, _release)

    def hold_key_test(self, logical_name: str, duration_ms: int) -> None:
        key = self._key_map().get(logical_name, "?")
        down_ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        self._send_raw_key(key, True)

        def _release():
            up_ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
            self._send_raw_key(key, False)
            self.holdTestResult.emit(key, down_ts, up_ts)

        QTimer.singleShot(duration_ms, _release)

    def manual_key_down(self, logical_name: str) -> bool:
        """Diagnostics 'HOLD X' button: sends a real KEY DOWN and leaves it
        held until manual_key_up() -- completely bypasses gesture/steering/
        pedal computation, so this is the test that proves (or disproves)
        the input-delivery layer independent of hand-tracking."""
        key = self._key_map().get(logical_name) or {
            "handbrake": "space"}.get(logical_name, logical_name)
        return self._send_raw_key(key, True)

    def manual_key_up(self, logical_name: str) -> bool:
        key = self._key_map().get(logical_name) or {
            "handbrake": "space"}.get(logical_name, logical_name)
        return self._send_raw_key(key, False)

    def run_notepad_test(self) -> None:
        """Opens Notepad and types A D W S through the same key-injection
        call MotionDrive uses for real driving, isolating "does Windows
        input work at all" from "does the browser/game respond to it"."""
        import subprocess
        try:
            subprocess.Popen(["notepad.exe"])
            self._log_event("Notepad test: launching Notepad")
        except Exception:
            log.exception("Could not launch Notepad for diagnostic test")
            self._log_event("Notepad test: FAILED to launch Notepad")
            self.notepadTestResult.emit(False)
            return

        def _type_sequence():
            results = []
            for i, key in enumerate(("a", "d", "w", "s")):
                def _press(k=key):
                    down_ok = self._send_raw_key(k, True)
                    up_ok = self._send_raw_key(k, False)
                    results.append(down_ok and up_ok)
                    self._log_event(f"Notepad test: sent {k.upper()}")
                    if len(results) == 4:
                        self.notepadTestResult.emit(all(results))
                QTimer.singleShot(i * 200, _press)

        QTimer.singleShot(900, _type_sequence)
