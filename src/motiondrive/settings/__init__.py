"""Beginner-friendly app settings: sensitivity, smoothing, range, dead zone,
camera choice, input mode. No raw math constants are ever exposed to the UI --
sliders are 0-100 and get mapped to real coefficients inside `steering`."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

from motiondrive.logging_ import get_logger
from motiondrive.paths import SETTINGS_FILE, ensure_dirs

log = get_logger(__name__)

InputMode = Literal["gamepad", "keyboard"]
GestureName = Literal["pinch", "index_curl", "fist"]
OverlayMode = Literal["always", "active_only", "hidden"]

# Recommended defaults, tuned for a typical desk-distance webcam setup.
RECOMMENDED = dict(
    sensitivity=55,   # 0-100, low..high
    smoothing=45,      # 0-100, fast..smooth (higher = more EMA smoothing)
    steering_range=60,  # 0-100, small..large physical rotation for full lock
    dead_zone=5,       # 0-20 percent (default 5%)
    steering_preset="responsive",   # "responsive" | "balanced" | "smooth"
    steering_curve="responsive",    # "responsive" | "linear" | "smooth"
    splash_enabled=True,
    input_mode="gamepad",
    camera_index=0,
    camera_width=1280,
    camera_height=720,
    camera_fps=30,
    close_to_tray=False,
    keep_controller_active_when_hidden=True,
    active_profile="Generic WASD Racing",
    auto_start_controller=False,
    hotkey_stop="F8",
    hotkey_pause="F7",
    hotkey_mute="Ctrl+Shift+M",
    game_exe_path="",
    game_url="",
    window_x=-1, window_y=-1, window_width=1180, window_height=860, window_maximized=False,
    overlay_x=-1, overlay_y=-1, overlay_width=260, overlay_height=190, overlay_compact=False,
    throttle_sensitivity=55,
    brake_sensitivity=55,
    gesture_threshold=35,   # 0-100, low..high pinch depth needed to activate
    pedal_smoothing=40,
    accelerate_gesture="pinch",
    brake_gesture="pinch",
    overlay_mode="active_only",
    overlay_opacity=90,       # 20-100 percent
    overlay_show_landmarks=True,
    overlay_show_labels=True,
    overlay_show_fps=True,
    overlay_show_wheel=True,
    key_left="a", key_right="d", key_accelerate="w", key_brake="s", key_shift="shift",
    key_p2_left="left", key_p2_right="right", key_p2_accelerate="up", key_p2_brake="down", key_p2_shift="space",
    player_mode="single",        # "single" | "two"
    key_preset="wasd",
    tracking_performance="balanced",   # "high" | "balanced" | "performance"
    game_mode_optimization=True,
    keyboard_injection_mode="scancode",   # "scancode" | "virtual_key" -- Advanced Diagnostics only
    start_with_windows=False,       # registers/unregisters a real Windows startup entry
    start_minimized=False,          # app launches showMinimized() instead of shown normally
    remember_window_position=True,  # gates window geometry restore/save
    onboarding_completed=False,  # gates the one-time first-launch HOW TO PLAY modal
    controller_source="hands",    # "hands" | "phone"
)

# tracking_performance -> (process every Nth camera frame, inference resize scale)
TRACKING_PERFORMANCE_PRESETS = {
    "high": dict(frame_skip=1, inference_scale=1.0),
    "balanced": dict(frame_skip=1, inference_scale=0.75),
    "performance": dict(frame_skip=2, inference_scale=0.6),
}

KEY_PRESETS = {
    "wasd": dict(key_left="a", key_right="d", key_accelerate="w", key_brake="s"),
    "arrows": dict(key_left="left", key_right="right", key_accelerate="up", key_brake="down"),
}


@dataclass
class Settings:
    sensitivity: int = RECOMMENDED["sensitivity"]
    smoothing: int = RECOMMENDED["smoothing"]
    steering_range: int = RECOMMENDED["steering_range"]
    dead_zone: int = RECOMMENDED["dead_zone"]
    steering_preset: str = RECOMMENDED["steering_preset"]
    steering_curve: str = RECOMMENDED["steering_curve"]
    splash_enabled: bool = RECOMMENDED["splash_enabled"]
    input_mode: InputMode = RECOMMENDED["input_mode"]
    camera_index: int = RECOMMENDED["camera_index"]
    camera_width: int = RECOMMENDED["camera_width"]
    camera_height: int = RECOMMENDED["camera_height"]
    camera_fps: int = RECOMMENDED["camera_fps"]
    close_to_tray: bool = RECOMMENDED["close_to_tray"]
    keep_controller_active_when_hidden: bool = RECOMMENDED["keep_controller_active_when_hidden"]
    active_profile: str = RECOMMENDED["active_profile"]
    auto_start_controller: bool = RECOMMENDED["auto_start_controller"]
    hotkey_stop: str = RECOMMENDED["hotkey_stop"]
    hotkey_pause: str = RECOMMENDED["hotkey_pause"]
    hotkey_mute: str = RECOMMENDED["hotkey_mute"]
    game_exe_path: str = RECOMMENDED["game_exe_path"]
    game_url: str = RECOMMENDED["game_url"]

    window_x: int = RECOMMENDED["window_x"]
    window_y: int = RECOMMENDED["window_y"]
    window_width: int = RECOMMENDED["window_width"]
    window_height: int = RECOMMENDED["window_height"]
    window_maximized: bool = RECOMMENDED["window_maximized"]

    overlay_x: int = RECOMMENDED["overlay_x"]
    overlay_y: int = RECOMMENDED["overlay_y"]
    overlay_width: int = RECOMMENDED["overlay_width"]
    overlay_height: int = RECOMMENDED["overlay_height"]
    overlay_compact: bool = RECOMMENDED["overlay_compact"]

    throttle_sensitivity: int = RECOMMENDED["throttle_sensitivity"]
    brake_sensitivity: int = RECOMMENDED["brake_sensitivity"]
    gesture_threshold: int = RECOMMENDED["gesture_threshold"]
    pedal_smoothing: int = RECOMMENDED["pedal_smoothing"]
    accelerate_gesture: GestureName = RECOMMENDED["accelerate_gesture"]
    brake_gesture: GestureName = RECOMMENDED["brake_gesture"]

    overlay_mode: OverlayMode = RECOMMENDED["overlay_mode"]
    overlay_opacity: int = RECOMMENDED["overlay_opacity"]
    overlay_show_landmarks: bool = RECOMMENDED["overlay_show_landmarks"]
    overlay_show_labels: bool = RECOMMENDED["overlay_show_labels"]
    overlay_show_fps: bool = RECOMMENDED["overlay_show_fps"]
    overlay_show_wheel: bool = RECOMMENDED["overlay_show_wheel"]

    key_left: str = RECOMMENDED["key_left"]
    key_right: str = RECOMMENDED["key_right"]
    key_accelerate: str = RECOMMENDED["key_accelerate"]
    key_brake: str = RECOMMENDED["key_brake"]
    key_p2_left: str = RECOMMENDED["key_p2_left"]
    key_p2_right: str = RECOMMENDED["key_p2_right"]
    key_p2_accelerate: str = RECOMMENDED["key_p2_accelerate"]
    key_p2_brake: str = RECOMMENDED["key_p2_brake"]
    key_p2_shift: str = RECOMMENDED["key_p2_shift"]
    player_mode: str = RECOMMENDED["player_mode"]
    key_preset: str = RECOMMENDED["key_preset"]

    tracking_performance: str = RECOMMENDED["tracking_performance"]
    game_mode_optimization: bool = RECOMMENDED["game_mode_optimization"]
    keyboard_injection_mode: str = RECOMMENDED["keyboard_injection_mode"]

    start_with_windows: bool = RECOMMENDED["start_with_windows"]
    start_minimized: bool = RECOMMENDED["start_minimized"]
    remember_window_position: bool = RECOMMENDED["remember_window_position"]
    onboarding_completed: bool = RECOMMENDED["onboarding_completed"]
    controller_source: str = RECOMMENDED["controller_source"]

    def clamp(self) -> "Settings":
        self.sensitivity = max(0, min(100, self.sensitivity))
        self.smoothing = max(0, min(100, self.smoothing))
        self.steering_range = max(0, min(100, self.steering_range))
        self.dead_zone = max(0, min(20, self.dead_zone))
        self.throttle_sensitivity = max(0, min(100, self.throttle_sensitivity))
        self.brake_sensitivity = max(0, min(100, self.brake_sensitivity))
        self.gesture_threshold = max(5, min(90, self.gesture_threshold))
        self.pedal_smoothing = max(0, min(100, self.pedal_smoothing))
        self.overlay_opacity = max(20, min(100, self.overlay_opacity))
        self.window_width = max(980, self.window_width)
        self.window_height = max(640, self.window_height)
        self.overlay_width = max(140, self.overlay_width)
        self.overlay_height = max(90, self.overlay_height)
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def recommended() -> "Settings":
        return Settings()


class SettingsStore:
    """Loads/saves Settings as JSON under %LOCALAPPDATA%\\MotionDrive\\config."""

    def __init__(self, path=None):
        self.path = path or SETTINGS_FILE

    def load(self) -> Settings:
        ensure_dirs()
        if not self.path.exists():
            s = Settings.recommended()
            self.save(s)
            return s
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            known = {f: data[f] for f in Settings.__dataclass_fields__ if f in data}
            return Settings(**known).clamp()
        except Exception:
            log.exception("Failed to load settings, falling back to recommended")
            return Settings.recommended()

    def save(self, settings: Settings) -> None:
        ensure_dirs()
        settings.clamp()
        self.path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
        log.info("Settings saved")
