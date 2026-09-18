"""Settings screen: exactly 4 sections (Controls, Steering, Camera,
Application) in a 2-column layout, each control paired with a one-line
plain-English caption. Steering feel controls are shared with the
Controller page via motiondrive.ui.drive_feel so the two can never drift
out of sync. Autosaves on every change -- no explicit Save button."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
                                 QPushButton, QFrame, QComboBox, QCheckBox,
                                 QGraphicsOpacityEffect, QScrollArea)

_HOTKEY_OPTIONS = [f"F{i}" for i in range(1, 13)]
_QT_KEY_TO_NAME = {
    Qt.Key_Left: "left", Qt.Key_Right: "right", Qt.Key_Up: "up", Qt.Key_Down: "down",
    Qt.Key_Space: "space", Qt.Key_Shift: "shift", Qt.Key_Control: "ctrl",
}
_KEY_GLYPHS = {"left": "←", "right": "→", "up": "↑", "down": "↓"}

from motiondrive.camera import RESOLUTIONS, FPS_OPTIONS, list_cameras
from motiondrive.profiles import Profile, ProfileStore
from motiondrive.settings import KEY_PRESETS, Settings
from motiondrive.ui.drive_feel import DRIVE_FEEL_PRESETS, matches_preset
from motiondrive.ui.theme import COLOR_ACCENT, COLOR_TEXT_DIM, COLOR_GREEN

# "Quality" is a friendlier relabel of the real tracking_performance preset
# (frame-skip / inference-resolution tradeoff) -- not a new/fake setting.
_QUALITY_OPTIONS = [("Auto (recommended)", "balanced"), ("Performance", "performance"), ("Quality", "high")]


def _key_event_to_name(event) -> str | None:
    key = event.key()
    if key in _QT_KEY_TO_NAME:
        return _QT_KEY_TO_NAME[key]
    text = event.text()
    if text and text.isprintable() and text.strip():
        return text.lower()
    return None


class KeyCaptureButton(QPushButton):
    """Click, then press any key on the physical keyboard to assign it."""

    keyAssigned = Signal(str)

    def __init__(self, key_name: str, parent=None):
        super().__init__(key_name.upper(), parent)
        self._listening = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self._start_listening)

    def _start_listening(self) -> None:
        self._listening = True
        self.setText("Press a key…")
        self.setFocus(Qt.OtherFocusReason)

    def set_key(self, key_name: str) -> None:
        self.setText(key_name.upper())

    def keyPressEvent(self, event) -> None:
        if not self._listening:
            super().keyPressEvent(event)
            return
        name = _key_event_to_name(event)
        self._listening = False
        if name:
            self.setText(name.upper())
            self.keyAssigned.emit(name)
        else:
            self.setText(self.text())
        event.accept()


def _section_frame(icon: str, title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout]:
    panel = QFrame()
    panel.setObjectName("panel")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(14)

    head = QHBoxLayout()
    head.setSpacing(10)
    icon_lbl = QLabel(icon)
    icon_lbl.setStyleSheet("font-size: 16px; background-color: #16233a; border-radius: 8px; "
                            "min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;")
    icon_lbl.setAlignment(Qt.AlignCenter)
    head.addWidget(icon_lbl)
    title_col = QVBoxLayout()
    title_col.setSpacing(1)
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
    title_col.addWidget(title_lbl)
    sub_lbl = QLabel(subtitle)
    sub_lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_DIM};")
    title_col.addWidget(sub_lbl)
    head.addLayout(title_col, stretch=1)
    layout.addLayout(head)
    return panel, layout


def _labeled_combo(label: str) -> tuple[QVBoxLayout, QComboBox]:
    col = QVBoxLayout()
    col.setSpacing(4)
    lab = QLabel(label)
    lab.setStyleSheet("font-size: 11px; font-weight: 600;")
    col.addWidget(lab)
    combo = QComboBox()
    col.addWidget(combo)
    return col, combo


def _labeled_slider(label: str, caption: str, minimum: int, maximum: int, value: int, suffix: str = "%"):
    row = QVBoxLayout()
    row.setSpacing(3)
    top = QHBoxLayout()
    lab = QLabel(label)
    lab.setStyleSheet("font-size: 12px; font-weight: 600;")
    top.addWidget(lab)
    top.addStretch()
    value_lbl = QLabel(f"{value}{suffix}")
    value_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_ACCENT};")
    top.addWidget(value_lbl)
    row.addLayout(top)
    cap = QLabel(caption)
    cap.setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_DIM};")
    row.addWidget(cap)
    slider = QSlider(Qt.Horizontal)
    slider.setMinimum(minimum)
    slider.setMaximum(maximum)
    slider.setValue(value)
    row.addWidget(slider)
    return row, slider, value_lbl


def _toggle_row(title: str, caption: str) -> tuple[QHBoxLayout, QCheckBox]:
    row = QHBoxLayout()
    row.setSpacing(10)
    text_col = QVBoxLayout()
    text_col.setSpacing(1)
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
    text_col.addWidget(title_lbl)
    cap_lbl = QLabel(caption)
    cap_lbl.setWordWrap(True)
    cap_lbl.setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_DIM};")
    text_col.addWidget(cap_lbl)
    row.addLayout(text_col, stretch=1)
    check = QCheckBox()
    check.setObjectName("toggleSwitch")
    row.addWidget(check, alignment=Qt.AlignVCenter)
    return row, check


class SettingsPage(QWidget):
    settingsChanged = Signal(Settings)
    profileChangeRequested = Signal(Profile)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = Settings()
        self._profile_store = ProfileStore()
        self._suspend_signals = False

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title = QLabel("SETTINGS")
        title.setObjectName("sectionTitle")
        header_col.addWidget(title)
        subtitle = QLabel("Configure MotionDrive to match your setup.")
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_DIM};")
        header_col.addWidget(subtitle)
        layout.addLayout(header_col)

        # Two independent columns (not a QGridLayout) -- a grid forces each
        # row's cells to match heights, which left the shorter Controls/
        # Camera panels with large dead space next to the taller Steering
        # panel. Each column now sizes to its own content instead.
        columns_row = QHBoxLayout()
        columns_row.setSpacing(16)
        left_col = QVBoxLayout()
        left_col.setSpacing(16)
        left_col.addWidget(self._build_controls_section())
        left_col.addWidget(self._build_camera_section())
        right_col = QVBoxLayout()
        right_col.setSpacing(16)
        right_col.addWidget(self._build_steering_section())
        right_col.addWidget(self._build_application_section())
        columns_row.addLayout(left_col, stretch=1)
        columns_row.addLayout(right_col, stretch=1)
        layout.addLayout(columns_row)

        # Autosave confirmation -- fades in briefly after each real change,
        # never a blocking dialog, never requires an explicit Save click.
        self._saved_lbl = QLabel("✓  Settings saved automatically\nAll changes are applied in real time.")
        self._saved_lbl.setAlignment(Qt.AlignCenter)
        self._saved_lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_GREEN};")
        self._saved_effect = QGraphicsOpacityEffect(self._saved_lbl)
        self._saved_effect.setOpacity(0.0)
        self._saved_lbl.setGraphicsEffect(self._saved_effect)
        layout.addWidget(self._saved_lbl)
        self._saved_hide_timer = QTimer(self)
        self._saved_hide_timer.setSingleShot(True)
        self._saved_hide_timer.timeout.connect(self._fade_out_saved)

        from motiondrive.ui.widgets import BrandFooter
        layout.addWidget(BrandFooter())

    # ----------------------------------------------------------- CONTROLS
    def _build_controls_section(self) -> QFrame:
        panel, layout = _section_frame("🎮", "CONTROLS", "Choose your control preset and input mode.")

        row = QHBoxLayout()
        row.setSpacing(16)
        col, self.profile_combo = _labeled_combo("Game / Control Preset")
        self.profile_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        row.addLayout(col, stretch=1)
        col, self.input_mode_combo = _labeled_combo("Input Mode")
        self.input_mode_combo.addItem("Keyboard", "keyboard")
        self.input_mode_combo.addItem("Virtual Gamepad", "gamepad")
        self.input_mode_combo.currentIndexChanged.connect(self._on_input_mode_changed)
        row.addLayout(col, stretch=1)
        layout.addLayout(row)

        self._keymap_section = QWidget()
        km = QVBoxLayout(self._keymap_section)
        km.setContentsMargins(0, 4, 0, 0)
        km.setSpacing(10)

        km_head = QHBoxLayout()
        self._keymap_title_lbl = QLabel("Key Mapping")
        self._keymap_title_lbl.setStyleSheet("font-size: 11px; font-weight: 700;")
        km_head.addWidget(self._keymap_title_lbl)
        km_head.addStretch()
        self._configure_btn = QPushButton("Configure  ›")
        self._configure_btn.setStyleSheet("background: transparent; border: none; "
                                            f"color: {COLOR_ACCENT}; font-size: 11px; font-weight: 700;")
        self._configure_btn.clicked.connect(self._toggle_configure)
        km_head.addWidget(self._configure_btn)
        km.addLayout(km_head)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(12)
        self._key_badges: dict[str, tuple[QLabel, QLabel]] = {}
        for name, label in (("key_left", "LEFT"), ("key_right", "RIGHT"),
                             ("key_accelerate", "THROTTLE"), ("key_brake", "BRAKE")):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            glyph = QLabel("-")
            glyph.setAlignment(Qt.AlignCenter)
            glyph.setFixedSize(56, 44)
            glyph.setStyleSheet("background-color: #1c2330; border: 1px solid #2a3240; border-radius: 8px; "
                                  "font-size: 16px; font-weight: 800;")
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"font-size: 9px; font-weight: 700; color: {COLOR_TEXT_DIM};")
            cell.addWidget(glyph, alignment=Qt.AlignCenter)
            cell.addWidget(lbl)
            badge_row.addLayout(cell)
            self._key_badges[name] = (glyph, lbl)
        badge_row.addStretch()
        km.addLayout(badge_row)

        # Hidden until "Configure" is clicked -- real key-capture controls.
        self._configure_body = QWidget()
        cb = QVBoxLayout(self._configure_body)
        cb.setContentsMargins(0, 8, 0, 0)
        cb.setSpacing(10)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Game preset"))
        self.key_preset_combo = QComboBox()
        self.key_preset_combo.addItem("Generic Racing (WASD)", "wasd")
        self.key_preset_combo.addItem("Arrow-Key Racing", "arrows")
        self.key_preset_combo.addItem("Custom", "custom")
        self.key_preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.key_preset_combo, stretch=1)
        cb.addLayout(preset_row)

        keys_row = QHBoxLayout()
        self.key_left_btn = KeyCaptureButton("a")
        self.key_left_btn.keyAssigned.connect(lambda k: self._on_key_assigned("key_left", k))
        self.key_right_btn = KeyCaptureButton("d")
        self.key_right_btn.keyAssigned.connect(lambda k: self._on_key_assigned("key_right", k))
        self.key_accelerate_btn = KeyCaptureButton("w")
        self.key_accelerate_btn.keyAssigned.connect(lambda k: self._on_key_assigned("key_accelerate", k))
        self.key_brake_btn = KeyCaptureButton("s")
        self.key_brake_btn.keyAssigned.connect(lambda k: self._on_key_assigned("key_brake", k))
        for lbl, btn in (("Left", self.key_left_btn), ("Right", self.key_right_btn),
                          ("Accel", self.key_accelerate_btn), ("Brake", self.key_brake_btn)):
            keys_row.addWidget(QLabel(lbl))
            keys_row.addWidget(btn)
        cb.addLayout(keys_row)

        self._key_warning_lbl = QLabel("")
        self._key_warning_lbl.setStyleSheet("color: #ff7b72; font-size: 11px; font-weight: 600;")
        self._key_warning_lbl.setVisible(False)
        cb.addWidget(self._key_warning_lbl)

        self._configure_body.setVisible(False)
        km.addWidget(self._configure_body)
        layout.addWidget(self._keymap_section)

        return panel

    # ----------------------------------------------------------- STEERING
    def _build_steering_section(self) -> QFrame:
        panel, layout = _section_frame("🎡", "STEERING", "Adjust how MotionDrive responds to your hand movement.")

        feel_label = QLabel("Drive Feel")
        feel_label.setStyleSheet("font-size: 11px; font-weight: 700;")
        layout.addWidget(feel_label)
        feel_row = QHBoxLayout()
        feel_row.setSpacing(8)
        self._feel_buttons: dict[str, QPushButton] = {}
        from PySide6.QtWidgets import QButtonGroup
        feel_group = QButtonGroup(self)
        feel_group.setExclusive(True)
        for name, preset in DRIVE_FEEL_PRESETS.items():
            btn = QPushButton(f"{preset['icon']}  {name.title()}\n{preset['caption']}")
            btn.setCheckable(True)
            btn.setObjectName("driveFeel")
            btn.clicked.connect(lambda _c=False, n=name: self._apply_drive_feel(n))
            feel_row.addWidget(btn)
            feel_group.addButton(btn)
            self._feel_buttons[name] = btn
        layout.addLayout(feel_row)

        row, self.sensitivity_slider, self._sensitivity_lbl = _labeled_slider(
            "Sensitivity", "How strongly your hand movement affects steering.", 0, 100, self._settings.sensitivity)
        layout.addLayout(row)
        row, self.deadzone_slider, self._deadzone_lbl = _labeled_slider(
            "Deadzone", "Ignore tiny accidental movements.", 0, 20, self._settings.dead_zone)
        layout.addLayout(row)
        row, self.stability_slider, self._stability_lbl = _labeled_slider(
            "Stability", "Reduce unwanted steering shake.", 0, 100, self._settings.smoothing)
        layout.addLayout(row)
        # No separate Response control: it backed the exact same
        # SteeringEngine.preset value the Drive Feel buttons above already
        # set (see drive_feel.py) -- a second slider for the same concept
        # was redundant and confusing. steering_preset is still tracked
        # internally on self._settings, just never exposed as its own row.
        for s in (self.sensitivity_slider, self.deadzone_slider, self.stability_slider):
            s.valueChanged.connect(self._on_slider_changed)

        reset_btn = QPushButton("↻  Reset to Recommended")
        reset_btn.clicked.connect(self._reset_recommended)
        layout.addWidget(reset_btn)
        layout.addStretch()

        return panel

    # ------------------------------------------------------------- CAMERA
    def _build_camera_section(self) -> QFrame:
        panel, layout = _section_frame("📷", "CAMERA", "Configure your camera.")

        col, self.camera_combo = _labeled_combo("Camera")
        self.camera_combo.currentIndexChanged.connect(self._emit_changed)
        combo_row = QHBoxLayout()
        combo_row.setSpacing(6)
        combo_row.addLayout(col, stretch=1)
        refresh_btn = QPushButton("🔄")
        refresh_btn.setToolTip("Detect cameras (briefly activates each one to check it)")
        refresh_btn.setFixedWidth(36)
        refresh_btn.clicked.connect(self.refresh_cameras)
        combo_row.addWidget(refresh_btn, alignment=Qt.AlignBottom)
        layout.addLayout(combo_row)

        col, self.quality_combo = _labeled_combo("Quality")
        for label, value in _QUALITY_OPTIONS:
            self.quality_combo.addItem(label, value)
        self.quality_combo.currentIndexChanged.connect(self._emit_changed)
        layout.addLayout(col)

        layout.addStretch()
        return panel

    # -------------------------------------------------------- APPLICATION
    def _build_application_section(self) -> QFrame:
        panel, layout = _section_frame("⚙", "APPLICATION", "General application preferences.")

        row, self.start_with_windows_check = _toggle_row(
            "Start with Windows", "Launch MotionDrive automatically when Windows starts.")
        self.start_with_windows_check.toggled.connect(self._on_start_with_windows_toggled)
        layout.addLayout(row)

        row, self.start_minimized_check = _toggle_row(
            "Start Minimized", "Open MotionDrive minimized to the taskbar.")
        self.start_minimized_check.toggled.connect(self._emit_changed)
        layout.addLayout(row)

        row, self.remember_position_check = _toggle_row(
            "Remember Window Position", "Restore the previous window position and size.")
        self.remember_position_check.toggled.connect(self._emit_changed)
        layout.addLayout(row)

        layout.addStretch()
        return panel

    # -------------------------------------------------------------- helpers
    def refresh_cameras(self) -> None:
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        for dev in list_cameras():
            self.camera_combo.addItem(dev.name, dev.index)
        if self.camera_combo.count() == 0:
            self.camera_combo.addItem("No camera detected", -1)
        self.camera_combo.blockSignals(False)

    def _refresh_profiles(self, active_name: str) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self._profile_store.list_profiles():
            self.profile_combo.addItem(f"{profile.icon}  {profile.name}", profile.name)
        idx = self.profile_combo.findData(active_name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

    def _toggle_configure(self) -> None:
        visible = not self._configure_body.isVisible()
        self._configure_body.setVisible(visible)
        self._configure_btn.setText("Configure  ⌃" if visible else "Configure  ›")

    def _update_key_badges(self) -> None:
        pairs = (("key_left", self._settings.key_left), ("key_right", self._settings.key_right),
                  ("key_accelerate", self._settings.key_accelerate), ("key_brake", self._settings.key_brake))
        for name, key in pairs:
            glyph_lbl, _ = self._key_badges[name]
            glyph_lbl.setText(_KEY_GLYPHS.get(key, key.upper()))
        self._keymap_title_lbl.setText(f"Key Mapping ({self._settings.active_profile})")

    def load(self, settings: Settings) -> None:
        self._suspend_signals = True
        self._settings = copy.deepcopy(settings)
        self._refresh_profiles(settings.active_profile)

        idx = self.input_mode_combo.findData(settings.input_mode)
        if idx >= 0:
            self.input_mode_combo.setCurrentIndex(idx)
        self._keymap_section.setVisible(settings.input_mode == "keyboard")

        self.key_left_btn.set_key(settings.key_left)
        self.key_right_btn.set_key(settings.key_right)
        self.key_accelerate_btn.set_key(settings.key_accelerate)
        self.key_brake_btn.set_key(settings.key_brake)
        idx = self.key_preset_combo.findData(settings.key_preset)
        if idx >= 0:
            self.key_preset_combo.setCurrentIndex(idx)
        self._update_key_badges()

        self.sensitivity_slider.setValue(settings.sensitivity)
        self.deadzone_slider.setValue(settings.dead_zone)
        self.stability_slider.setValue(settings.smoothing)
        self._sync_slider_labels()
        self._sync_feel_buttons()

        if self.camera_combo.count() == 0:
            # Deliberately does NOT call refresh_cameras() here -- that
            # briefly opens and reads from every physical camera device to
            # detect it, which visibly (and surprisingly) turns the camera
            # light on with no explicit user action, just from this page
            # loading (at app startup, or whenever settings resync between
            # pages). A placeholder entry for the saved camera index keeps
            # the dropdown showing something sensible; the 🔄 button next to
            # it is the only thing that actually probes hardware now.
            self.camera_combo.blockSignals(True)
            self.camera_combo.addItem(
                f"Camera {settings.camera_index} (click 🔄 to detect)", settings.camera_index)
            self.camera_combo.blockSignals(False)
        idx = self.camera_combo.findData(settings.camera_index)
        if idx >= 0:
            self.camera_combo.setCurrentIndex(idx)
        idx = self.quality_combo.findData(settings.tracking_performance)
        if idx >= 0:
            self.quality_combo.setCurrentIndex(idx)

        self.start_with_windows_check.setChecked(settings.start_with_windows)
        self.start_minimized_check.setChecked(settings.start_minimized)
        self.remember_position_check.setChecked(settings.remember_window_position)

        self._suspend_signals = False

    def _sync_slider_labels(self) -> None:
        self._sensitivity_lbl.setText(f"{self.sensitivity_slider.value()}%")
        self._deadzone_lbl.setText(f"{self.deadzone_slider.value()}%")
        self._stability_lbl.setText(f"{self.stability_slider.value()}%")

    def _sync_feel_buttons(self) -> None:
        for name, preset in DRIVE_FEEL_PRESETS.items():
            self._feel_buttons[name].setChecked(matches_preset(self._settings, preset))

    def current_settings(self) -> Settings:
        return Settings(
            sensitivity=self.sensitivity_slider.value(),
            smoothing=self.stability_slider.value(),
            steering_range=self._settings.steering_range,
            dead_zone=self.deadzone_slider.value(),
            steering_preset=self._settings.steering_preset,
            steering_curve=self._settings.steering_curve,
            splash_enabled=self._settings.splash_enabled,
            keyboard_injection_mode=self._settings.keyboard_injection_mode,
            throttle_sensitivity=self._settings.throttle_sensitivity,
            brake_sensitivity=self._settings.brake_sensitivity,
            gesture_threshold=self._settings.gesture_threshold,
            pedal_smoothing=self._settings.pedal_smoothing,
            accelerate_gesture=self._settings.accelerate_gesture,
            brake_gesture=self._settings.brake_gesture,
            overlay_mode=self._settings.overlay_mode,
            overlay_opacity=self._settings.overlay_opacity,
            overlay_show_landmarks=self._settings.overlay_show_landmarks,
            overlay_show_labels=self._settings.overlay_show_labels,
            overlay_show_fps=self._settings.overlay_show_fps,
            overlay_show_wheel=self._settings.overlay_show_wheel,
            input_mode=self.input_mode_combo.currentData() or "gamepad",
            camera_index=self.camera_combo.currentData() if self.camera_combo.currentData() is not None else 0,
            camera_width=self._settings.camera_width,
            camera_height=self._settings.camera_height,
            camera_fps=self._settings.camera_fps,
            close_to_tray=self._settings.close_to_tray,
            keep_controller_active_when_hidden=self._settings.keep_controller_active_when_hidden,
            auto_start_controller=self._settings.auto_start_controller,
            hotkey_stop=self._settings.hotkey_stop,
            hotkey_pause=self._settings.hotkey_pause,
            game_exe_path=self._settings.game_exe_path,
            tracking_performance=self.quality_combo.currentData() or "balanced",
            game_mode_optimization=self._settings.game_mode_optimization,
            key_left=self._btn_key(self.key_left_btn, self._settings.key_left),
            key_right=self._btn_key(self.key_right_btn, self._settings.key_right),
            key_accelerate=self._btn_key(self.key_accelerate_btn, self._settings.key_accelerate),
            key_brake=self._btn_key(self.key_brake_btn, self._settings.key_brake),
            key_preset=self.key_preset_combo.currentData() or "custom",
            active_profile=self._settings.active_profile,
            start_with_windows=self.start_with_windows_check.isChecked(),
            start_minimized=self.start_minimized_check.isChecked(),
            remember_window_position=self.remember_position_check.isChecked(),
            # Window/overlay geometry isn't editable on this page -- always
            # carry it over so a change here never resets the saved window
            # position/size.
            window_x=self._settings.window_x, window_y=self._settings.window_y,
            window_width=self._settings.window_width, window_height=self._settings.window_height,
            window_maximized=self._settings.window_maximized,
            overlay_x=self._settings.overlay_x, overlay_y=self._settings.overlay_y,
            overlay_width=self._settings.overlay_width, overlay_height=self._settings.overlay_height,
            overlay_compact=self._settings.overlay_compact,
        )

    def sync_geometry_fields(self, settings: Settings) -> None:
        self._settings.window_x = settings.window_x
        self._settings.window_y = settings.window_y
        self._settings.window_width = settings.window_width
        self._settings.window_height = settings.window_height
        self._settings.window_maximized = settings.window_maximized
        self._settings.overlay_x = settings.overlay_x
        self._settings.overlay_y = settings.overlay_y
        self._settings.overlay_width = settings.overlay_width
        self._settings.overlay_height = settings.overlay_height
        self._settings.overlay_compact = settings.overlay_compact

    @staticmethod
    def _btn_key(btn: "KeyCaptureButton", fallback: str) -> str:
        text = btn.text().lower()
        return text if text and "…" not in text and " " not in text else fallback

    def _on_input_mode_changed(self, _index: int) -> None:
        self._keymap_section.setVisible(self.input_mode_combo.currentData() == "keyboard")
        self._emit_changed()

    def _on_preset_changed(self, _index: int) -> None:
        preset = self.key_preset_combo.currentData()
        if preset in KEY_PRESETS:
            keys = KEY_PRESETS[preset]
            self.key_left_btn.set_key(keys["key_left"])
            self.key_right_btn.set_key(keys["key_right"])
            self.key_accelerate_btn.set_key(keys["key_accelerate"])
            self.key_brake_btn.set_key(keys["key_brake"])
        self._emit_changed()

    def _check_key_collisions(self) -> None:
        keys = [self._btn_key(self.key_left_btn, "a"), self._btn_key(self.key_right_btn, "d"),
                self._btn_key(self.key_accelerate_btn, "w"), self._btn_key(self.key_brake_btn, "s")]
        if len(keys) != len(set(keys)):
            self._key_warning_lbl.setText("⚠️ Multiple controls are mapped to the same key.")
            self._key_warning_lbl.setVisible(True)
        else:
            self._key_warning_lbl.setVisible(False)

    def _on_key_assigned(self, _field: str, _key: str) -> None:
        self._suspend_signals = True
        idx = self.key_preset_combo.findData("custom")
        if idx >= 0:
            self.key_preset_combo.setCurrentIndex(idx)
        self._suspend_signals = False
        self._check_key_collisions()
        self._emit_changed()

    def _on_profile_combo_changed(self, _index: int) -> None:
        if self._suspend_signals:
            return
        name = self.profile_combo.currentData()
        profile = self._profile_store.get(name) if name else None
        if profile is not None:
            self.profileChangeRequested.emit(profile)

    def _apply_drive_feel(self, name: str) -> None:
        preset = DRIVE_FEEL_PRESETS[name]
        self._settings.sensitivity = preset["sensitivity"]
        self._settings.smoothing = preset["smoothing"]
        self._settings.dead_zone = preset["dead_zone"]
        self._settings.steering_preset = preset["steering_preset"]
        self.load(self._settings)
        self._emit_changed()

    def _reset_recommended(self) -> None:
        from motiondrive.settings import RECOMMENDED
        self._settings.sensitivity = RECOMMENDED["sensitivity"]
        self._settings.smoothing = RECOMMENDED["smoothing"]
        self._settings.dead_zone = RECOMMENDED["dead_zone"]
        self._settings.steering_range = RECOMMENDED["steering_range"]
        self._settings.steering_preset = RECOMMENDED["steering_preset"]
        self.load(self._settings)
        self._emit_changed()

    def _on_slider_changed(self, *_args) -> None:
        if self._suspend_signals:
            return
        self._sync_slider_labels()
        self._settings.sensitivity = self.sensitivity_slider.value()
        self._settings.dead_zone = self.deadzone_slider.value()
        self._settings.smoothing = self.stability_slider.value()
        self._sync_feel_buttons()
        self._emit_changed()

    def _on_start_with_windows_toggled(self, checked: bool) -> None:
        if not self._suspend_signals:
            from motiondrive import startup
            ok = startup.set_enabled(checked)
            if not ok:
                # Real failure (e.g. no registry access) -- reflect it
                # honestly rather than showing a toggle that silently lied.
                self._suspend_signals = True
                self.start_with_windows_check.setChecked(not checked)
                self._suspend_signals = False
                return
        self._emit_changed()

    def _emit_changed(self, *_args) -> None:
        if self._suspend_signals:
            return
        self.settingsChanged.emit(self.current_settings())
        self._show_saved_confirmation()

    def _show_saved_confirmation(self) -> None:
        anim = QPropertyAnimation(self._saved_effect, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(self._saved_effect.opacity())
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._saved_fade_in = anim
        self._saved_hide_timer.start(1800)

    def _fade_out_saved(self) -> None:
        anim = QPropertyAnimation(self._saved_effect, b"opacity", self)
        anim.setDuration(400)
        anim.setStartValue(self._saved_effect.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._saved_fade_out = anim

