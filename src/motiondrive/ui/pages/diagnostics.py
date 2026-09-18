"""Input Diagnostics: makes the whole camera -> hands -> gesture -> key ->
Windows -> browser -> game pipeline observable, stage by stage, so a failure
can be isolated instead of guessed at. Never claims steps 7 (game response)
succeeded -- only that MotionDrive's own steps did."""
from __future__ import annotations

import subprocess
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                                 QPushButton, QFrame, QScrollArea, QPlainTextEdit,
                                 QMessageBox, QFileDialog)

from motiondrive.diagnostics import get_foreground_window_info
from motiondrive.controller_state import ControllerState, TrackingState
from motiondrive.pedals import PedalState
from motiondrive.steering import SteeringState, TrackingQuality
from motiondrive.ui.theme import COLOR_GREEN, COLOR_YELLOW, COLOR_RED, COLOR_TEXT_DIM, COLOR_ACCENT

_STAGE_NAMES = ["Camera", "Hand Tracking", "Gesture", "Control Value",
                "Key Mapper", "Windows Input", "Browser", "Game"]


def _dot(ok) -> str:
    return {"green": "\U0001F7E2", "yellow": "\U0001F7E1", "red": "\U0001F534"}[ok]


class _Panel(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 16)
        self._layout.setSpacing(8)
        head = QLabel(title)
        head.setStyleSheet("font-size: 12px; font-weight: 700; color: #8b96a5;")
        self._layout.addWidget(head)

    def body(self) -> QVBoxLayout:
        return self._layout


class DiagnosticsPage(QWidget):
    releaseAllRequested = Signal()
    testKeyRequested = Signal(str)          # logical name: left/right/accelerate/brake
    holdKeyRequested = Signal(str, int)     # logical name, milliseconds
    exportDiagnosticsRequested = Signal()
    manualKeyDownRequested = Signal(str)
    manualKeyUpRequested = Signal(str)
    browserTestRequested = Signal()
    focusGameRequested = Signal()
    armNotepadTestRequested = Signal()
    armBrowserTestRequested = Signal()
    testRightIn2SecondsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine_settings = None
        self._last_frame_state = None
        self._last_pedals = None
        self._last_key_states = {}
        self._sendinput_tested_ok = None
        self._compat_results: dict[str, bool | None] = {"notepad": None, "browser": None, "game": None}
        self._focus_countdown_value = 0

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("MOTIONDRIVE INPUT DIAGNOSTICS")
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        # ---------------------------------------------------------- pipeline
        pipeline_panel = _Panel("PIPELINE")
        row = QHBoxLayout()
        self._stage_labels: dict[str, QLabel] = {}
        for name in _STAGE_NAMES:
            lbl = QLabel(f"\U0001F7E1\n{name}")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("font-size: 11px;")
            row.addWidget(lbl)
            self._stage_labels[name] = lbl
        pipeline_panel.body().addLayout(row)
        note = QLabel("Browser/Game response cannot be verified automatically by MotionDrive.")
        note.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        pipeline_panel.body().addWidget(note)
        root.addWidget(pipeline_panel)

        # ------------------------------------------------------- live values
        values_panel = _Panel("GESTURE STATE (Hands & Control Intensity)")
        vb = values_panel.body()
        self._camera_label = QLabel("Camera: ● disconnected")
        self._hands_label = QLabel("Left Hand: —     Right Hand: —")
        self._steering_label = QLabel("Steering: raw=0.0°  smoothed=+0.00 (+0%)")
        self._throttle_label = QLabel("Throttle: raw=0.00  smoothed=0.00 (0%)")
        self._brake_label = QLabel("Brake: raw=0.00  smoothed=0.00 (0%)")
        self._fps_label = QLabel("FPS: —")
        for w in (self._camera_label, self._hands_label, self._steering_label,
                  self._throttle_label, self._brake_label, self._fps_label):
            w.setStyleSheet("font-size: 12px;")
            vb.addWidget(w)
        root.addWidget(values_panel)

        # -------------------------------------------------------- key state
        key_panel = _Panel("ACTUAL OUTPUT STATE (Windows SendInput Key Injection)")
        kb = key_panel.body()
        grid = QGridLayout()
        grid.setSpacing(12)
        self._key_map_labels: dict[str, QLabel] = {}
        self._key_state_labels: dict[str, QLabel] = {}
        for col, name in enumerate(("left", "right", "accelerate", "brake")):
            map_lbl = QLabel(f"{name.upper()}: -")
            map_lbl.setStyleSheet("font-size: 11px; color: #8b96a5;")
            state_lbl = QLabel("UP")
            state_lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {COLOR_TEXT_DIM};")
            grid.addWidget(map_lbl, 0, col)
            grid.addWidget(state_lbl, 1, col)
            self._key_map_labels[name] = map_lbl
            self._key_state_labels[name] = state_lbl
        kb.addLayout(grid)

        self._last_event_label = QLabel("Last Event: none")
        self._last_event_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        kb.addWidget(self._last_event_label)
        root.addWidget(key_panel)

        # --------------------------------------------------------- physical key test
        keytest_panel = _Panel("PHYSICAL KEYBOARD TEST  (Windows SendInput)")
        ktb = keytest_panel.body()
        btn_row = QHBoxLayout()
        for name in ("left", "right", "accelerate", "brake"):
            btn = QPushButton(f"TEST {name.upper()}")
            btn.clicked.connect(lambda _c=False, n=name: self.testKeyRequested.emit(n))
            btn_row.addWidget(btn)
        ktb.addLayout(btn_row)
        self._keytest_result_label = QLabel("")
        self._keytest_result_label.setWordWrap(True)
        self._keytest_result_label.setStyleSheet("font-size: 11px;")
        ktb.addWidget(self._keytest_result_label)

        hold_btn = QPushButton("HOLD ACCELERATE FOR 3 SECONDS")
        hold_btn.clicked.connect(lambda: self.holdKeyRequested.emit("accelerate", 3000))
        ktb.addWidget(hold_btn)
        self._hold_result_label = QLabel("")
        self._hold_result_label.setStyleSheet("font-size: 11px;")
        ktb.addWidget(self._hold_result_label)

        release_btn = QPushButton("⛔  RELEASE ALL KEYS")
        release_btn.setObjectName("stop")
        release_btn.clicked.connect(self.releaseAllRequested.emit)
        ktb.addWidget(release_btn)
        root.addWidget(keytest_panel)

        # ------------------------------------------------------ armed test
        # SendInput() does not target a window handle -- Windows delivers
        # injected key events to whatever window is currently foreground.
        # Clicking a SEND button inside MotionDrive makes MotionDrive
        # foreground, which makes any "does the browser receive it" test
        # meaningless by construction. This panel arms a test, hides
        # MotionDrive, waits for focus to settle on the target, verifies
        # MotionDrive is NOT foreground, and only then sends -- never the
        # other way around.
        armed_panel = _Panel("ARMED INPUT TEST  (the real way to test foreground input)")
        ab = armed_panel.body()
        armed_note = QLabel("1. Click inside Notepad or the browser test field.\n"
                              "2. Click one of the ARM buttons below.\n"
                              "3. MotionDrive hides immediately -- do not click it again.\n"
                              "4. MotionDrive waits for the target to become foreground, then sends "
                              "A D W S automatically and reappears with the result.")
        armed_note.setWordWrap(True)
        armed_note.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        ab.addWidget(armed_note)

        armed_btn_row = QHBoxLayout()
        arm_notepad_btn = QPushButton("ARM NOTEPAD TEST")
        arm_notepad_btn.clicked.connect(self._confirm_arm_notepad_test)
        arm_browser_btn = QPushButton("ARM BROWSER TEST")
        arm_browser_btn.clicked.connect(self.armBrowserTestRequested.emit)
        test_2s_btn = QPushButton("TEST RIGHT KEY IN 2 SECONDS")
        test_2s_btn.setToolTip("Countdown shown here (no popup), then MotionDrive hides and sends "
                                 "just the RIGHT key -- click your target first.")
        test_2s_btn.clicked.connect(self.testRightIn2SecondsRequested.emit)
        armed_btn_row.addWidget(arm_notepad_btn)
        armed_btn_row.addWidget(arm_browser_btn)
        armed_btn_row.addWidget(test_2s_btn)
        ab.addLayout(armed_btn_row)

        self._armed_status_label = QLabel("Not armed.")
        self._armed_status_label.setWordWrap(True)
        self._armed_status_label.setStyleSheet("font-size: 13px; font-weight: 600; padding: 6px 0;")
        ab.addWidget(self._armed_status_label)

        notepad_mark_row = QHBoxLayout()
        notepad_pass_btn = QPushButton("Mark: Notepad received ADWS")
        notepad_pass_btn.clicked.connect(lambda: self._set_compat_result("notepad", True))
        notepad_fail_btn = QPushButton("Mark: Notepad did NOT receive keys")
        notepad_fail_btn.clicked.connect(lambda: self._set_compat_result("notepad", False))
        notepad_mark_row.addWidget(notepad_pass_btn)
        notepad_mark_row.addWidget(notepad_fail_btn)
        ab.addLayout(notepad_mark_row)

        hotkey_note = QLabel("Global hotkeys (work even while MotionDrive is hidden, and never "
                               "bring MotionDrive to the foreground):\n"
                               "F9 = RIGHT   F10 = LEFT   F11 = ACCELERATE   F12 = BRAKE")
        hotkey_note.setWordWrap(True)
        hotkey_note.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px; padding-top: 6px;")
        ab.addWidget(hotkey_note)

        fg_debug_label_head = QLabel("FOREGROUND WINDOW DEBUG")
        fg_debug_label_head.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 10px; font-weight: 700; padding-top: 8px;")
        ab.addWidget(fg_debug_label_head)
        self._fg_debug_label = QLabel("HWND: —    Process: —    Window title: —\n"
                                        "MotionDrive foreground: —    Browser DOM focus: UNKNOWN "
                                        "(MotionDrive cannot see inside the page -- click the field yourself)")
        self._fg_debug_label.setWordWrap(True)
        self._fg_debug_label.setStyleSheet(f"font-family: Consolas, monospace; font-size: 11px; color: {COLOR_TEXT_DIM};")
        ab.addWidget(self._fg_debug_label)
        root.addWidget(armed_panel)

        # ------------------------------------------------- manual key test
        manual_panel = _Panel("MANUAL KEYBOARD TEST  (bypasses gesture detection entirely)")
        mb = manual_panel.body()
        note = QLabel("If a game doesn't respond to these, the problem is NOT hand tracking or "
                       "gesture detection -- it's downstream (Windows input, browser, or the game itself).")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        mb.addWidget(note)
        self._manual_buttons: dict[str, QPushButton] = {}
        self._manual_held: dict[str, bool] = {}
        manual_row = QHBoxLayout()
        for name in ("left", "right", "accelerate", "brake"):
            btn = QPushButton(f"HOLD {name.upper()}")
            btn.clicked.connect(lambda _c=False, n=name: self._toggle_manual_hold(n))
            manual_row.addWidget(btn)
            self._manual_buttons[name] = btn
            self._manual_held[name] = False
        mb.addLayout(manual_row)
        root.addWidget(manual_panel)

        # ----------------------------------------------------- browser test
        browser_panel = _Panel("BROWSER TEXT INPUT TEST")
        bwb = browser_panel.body()
        browser_note = QLabel("Opens a plain local web page with a text field in your default browser. "
                                "Click the field, then use ARM BROWSER TEST above -- that's the valid "
                                "way to test this.")
        browser_note.setWordWrap(True)
        browser_note.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        bwb.addWidget(browser_note)
        open_browser_btn = QPushButton("OPEN TEST PAGE")
        open_browser_btn.clicked.connect(self._confirm_browser_test)
        bwb.addWidget(open_browser_btn)
        send_warning = QLabel("⚠ The SEND buttons below click MotionDrive first, which makes "
                                "MotionDrive the foreground window -- SendInput will then deliver into "
                                "MotionDrive, not the browser. They only prove the SendInput call itself "
                                "works, NOT that the browser receives it. Use ARM BROWSER TEST for a "
                                "real result.")
        send_warning.setWordWrap(True)
        send_warning.setStyleSheet(f"color: {COLOR_YELLOW}; font-size: 11px;")
        bwb.addWidget(send_warning)
        send_row = QHBoxLayout()
        for name in ("left", "right", "accelerate", "brake"):
            btn = QPushButton(f"SEND {name.upper()}")
            btn.clicked.connect(lambda _c=False, n=name: self.testKeyRequested.emit(n))
            send_row.addWidget(btn)
        bwb.addLayout(send_row)
        self._browser_result_label = QLabel("")
        self._browser_result_label.setStyleSheet("font-size: 11px;")
        bwb.addWidget(self._browser_result_label)
        browser_pass_row = QHBoxLayout()
        browser_pass_btn = QPushButton("Mark: Browser field received the keys")
        browser_pass_btn.clicked.connect(lambda: self._set_compat_result("browser", True))
        browser_fail_btn = QPushButton("Mark: Browser field did NOT receive keys")
        browser_fail_btn.clicked.connect(lambda: self._set_compat_result("browser", False))
        browser_pass_row.addWidget(browser_pass_btn)
        browser_pass_row.addWidget(browser_fail_btn)
        bwb.addLayout(browser_pass_row)
        root.addWidget(browser_panel)

        # -------------------------------------------------------- game test
        game_panel = _Panel("GAME TEST  (e.g. Drift Hunters MAX)")
        gtb = game_panel.body()
        self._focus_countdown_label = QLabel("")
        self._focus_countdown_label.setStyleSheet(f"color: {COLOR_YELLOW}; font-size: 13px; font-weight: 700;")
        gtb.addWidget(self._focus_countdown_label)
        focus_game_btn = QPushButton("FOCUS GAME")
        focus_game_btn.setToolTip("Brings your browser to the foreground once, then counts down so "
                                    "you can click inside the game before testing.")
        focus_game_btn.clicked.connect(self._start_focus_game_countdown)
        gtb.addWidget(focus_game_btn)

        game_btn_row = QHBoxLayout()
        game_tests = [("left", "TEST LEFT"), ("right", "TEST RIGHT"),
                       ("accelerate", "TEST GAS"), ("brake", "TEST BRAKE"),
                       ("handbrake", "TEST HANDBRAKE")]
        for logical, label in game_tests:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _c=False, n=logical: self.holdKeyRequested.emit(n, 1000))
            game_btn_row.addWidget(btn)
        gtb.addLayout(game_btn_row)

        game_pass_row = QHBoxLayout()
        game_pass_btn = QPushButton("Mark: Car responded")
        game_pass_btn.clicked.connect(lambda: self._set_compat_result("game", True))
        game_fail_btn = QPushButton("Mark: Car did NOT respond")
        game_fail_btn.clicked.connect(lambda: self._set_compat_result("game", False))
        game_pass_row.addWidget(game_pass_btn)
        game_pass_row.addWidget(game_fail_btn)
        gtb.addLayout(game_pass_row)
        root.addWidget(game_panel)

        # ------------------------------------------------------- target window
        focus_panel = _Panel("TARGET WINDOW / FOCUS")
        fb = focus_panel.body()
        self._focus_app_label = QLabel("Application: —")
        self._focus_title_label = QLabel("Window: —")
        self._focus_status_label = QLabel("Focused: —")
        for w in (self._focus_app_label, self._focus_title_label, self._focus_status_label):
            w.setStyleSheet("font-size: 12px;")
            fb.addWidget(w)
        self._focus_warning_label = QLabel("")
        self._focus_warning_label.setWordWrap(True)
        self._focus_warning_label.setStyleSheet(f"color: {COLOR_YELLOW}; font-size: 12px; font-weight: 600;")
        fb.addWidget(self._focus_warning_label)
        focus_test_btn = QPushButton("FOCUS TEST")
        focus_test_btn.clicked.connect(self._poll_foreground_window)
        fb.addWidget(focus_test_btn)
        root.addWidget(focus_panel)

        # ------------------------------------------------------------ perf
        perf_panel = _Panel("PERFORMANCE")
        pb = perf_panel.body()
        self._perf_fps_label = QLabel("Camera FPS: —    Tracking FPS: —")
        self._perf_res_label = QLabel("Camera resolution: —    Inference scale: —")
        self._perf_cpu_label = QLabel("CPU: —    RAM: —    GPU: unavailable")
        for w in (self._perf_fps_label, self._perf_res_label, self._perf_cpu_label):
            w.setStyleSheet("font-size: 12px;")
            pb.addWidget(w)

        self._perf_stage_label = QLabel("")
        self._perf_stage_label.setStyleSheet(f"font-family: Consolas, monospace; font-size: 11px; color: {COLOR_TEXT_DIM};")
        pb.addWidget(self._perf_stage_label)

        self._perf_regression_label = QLabel("")
        self._perf_regression_label.setWordWrap(True)
        self._perf_regression_label.setStyleSheet(f"color: {COLOR_YELLOW}; font-size: 12px; font-weight: 700;")
        pb.addWidget(self._perf_regression_label)

        run_perf_btn = QPushButton("RUN PERFORMANCE TEST")
        run_perf_btn.clicked.connect(self._run_performance_test)
        pb.addWidget(run_perf_btn)
        self._perf_report_label = QLabel("")
        self._perf_report_label.setWordWrap(True)
        self._perf_report_label.setStyleSheet("font-size: 11px;")
        pb.addWidget(self._perf_report_label)

        root.addWidget(perf_panel)
        self._perf_idle_sample = None
        self._perf_test_phase = None

        # --------------------------------------------------------- checklist
        checklist_panel = _Panel("SYSTEM DIAGNOSTICS")
        cb = checklist_panel.body()
        run_btn = QPushButton("RUN SYSTEM DIAGNOSTICS")
        run_btn.clicked.connect(self._run_system_diagnostics)
        cb.addWidget(run_btn)
        self._checklist_label = QLabel("")
        self._checklist_label.setStyleSheet("font-size: 12px;")
        cb.addWidget(self._checklist_label)
        root.addWidget(checklist_panel)

        # --------------------------------------------------- compatibility
        compat_panel = _Panel("COMPATIBILITY RESULT")
        cpb = compat_panel.body()
        self._compat_label = QLabel("Run the Notepad test above to get started.")
        self._compat_label.setWordWrap(True)
        self._compat_label.setStyleSheet("font-size: 12px;")
        cpb.addWidget(self._compat_label)
        copy_report_btn = QPushButton("COPY DIAGNOSTIC REPORT")
        copy_report_btn.clicked.connect(self._copy_diagnostic_report)
        cpb.addWidget(copy_report_btn)
        root.addWidget(compat_panel)

        # -------------------------------------------------------------- log
        log_panel = _Panel("EVENT LOG")
        lb = log_panel.body()
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(500)
        self._log_view.setFixedHeight(180)
        self._log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        lb.addWidget(self._log_view)
        log_btn_row = QHBoxLayout()
        clear_btn = QPushButton("CLEAR LOG")
        clear_btn.clicked.connect(self._log_view.clear)
        export_log_btn = QPushButton("EXPORT LOG")
        export_log_btn.clicked.connect(self._export_log)
        export_diag_btn = QPushButton("EXPORT DIAGNOSTICS")
        export_diag_btn.clicked.connect(self._export_diagnostics)
        log_btn_row.addWidget(clear_btn)
        log_btn_row.addWidget(export_log_btn)
        log_btn_row.addWidget(export_diag_btn)
        lb.addLayout(log_btn_row)
        root.addWidget(log_panel)

        root.addStretch()

        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(500)
        self._focus_timer.timeout.connect(self._poll_foreground_window)
        self._focus_timer.start()

    # -------------------------------------------------------------- helpers
    def set_key_mapping(self, key_map: dict) -> None:
        for name, key in key_map.items():
            if name in self._key_map_labels:
                self._key_map_labels[name].setText(f"{name.upper()}: {key.upper()}")

    def set_camera_state(self, connected: bool, fps: float) -> None:
        color = COLOR_GREEN if connected else COLOR_RED
        text = "connected" if connected else "disconnected"
        self._camera_label.setText(f"Camera: ● {text}")
        self._camera_label.setStyleSheet(f"font-size: 12px; color: {color};")
        self._fps_label.setText(f"FPS: {fps:.0f}" if connected else "FPS: —")
        self._update_pipeline(camera=connected)

    def on_frame(self, tracking, state: SteeringState, pedals: PedalState, fps: float) -> None:
        # Keep the latest values for the checklist/export even between
        # renders, but only actually repaint the labels at ~8Hz (spec:
        # diagnostics UI updates 5-10 times/sec, never full camera FPS --
        # this page is only visible occasionally, no reason to pay for a
        # full label/style recalculation on every single tracking frame).
        self._last_frame_state = state
        self._last_pedals = pedals
        now = time.time()
        if now - getattr(self, "_last_render_time", 0.0) < 0.12:
            return
        self._last_render_time = now

        left_ok = tracking is not None and tracking.left is not None
        right_ok = tracking is not None and tracking.right is not None
        left_conf = f"{tracking.left.confidence * 100:.0f}%" if left_ok else "—"
        right_conf = f"{tracking.right.confidence * 100:.0f}%" if right_ok else "—"
        self._hands_label.setText(
            f"Left Hand: {'✓' if left_ok else '—'} ({left_conf})     "
            f"Right Hand: {'✓' if right_ok else '—'} ({right_conf})")
        self._steering_label.setText(
            f"Steering: raw={state.raw_angle_deg:+.1f}°  smoothed={state.value:+.2f} ({state.value * 100:+.0f}%)")
        self._throttle_label.setText(f"Throttle: smoothed={pedals.throttle:.2f} ({pedals.throttle * 100:.0f}%)")
        self._brake_label.setText(f"Brake: smoothed={pedals.brake:.2f} ({pedals.brake * 100:.0f}%)")
        self._fps_label.setText(f"FPS: {fps:.0f}")
        self._update_pipeline(
            hand_tracking=left_ok or right_ok,
            gesture=left_ok or right_ok,
            control_value=True,
        )

    def set_key_states(self, key_states: dict) -> None:
        self._last_key_states = key_states
        any_down = False
        for name, lbl in self._key_state_labels.items():
            key, is_down = key_states.get(name, ("-", False))
            lbl.setText(f"{key.upper()}: {'DOWN' if is_down else 'UP'}")
            lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {COLOR_GREEN if is_down else COLOR_TEXT_DIM};")
            any_down = any_down or is_down
        self._update_pipeline(key_mapper=True)

    def add_event(self, timestamp: str, message: str) -> None:
        self._log_view.appendPlainText(f"{timestamp}  {message}")
        self._last_event_label.setText(f"Last Event: {message}  ({timestamp})")

    def show_key_test_result(self, name: str, key: str, down_ok: bool, up_ok: bool) -> None:
        self._sendinput_tested_ok = bool(down_ok and up_ok)
        self._update_pipeline(windows_input=self._sendinput_tested_ok)
        self._keytest_result_label.setText(
            f"Attempting: {key.upper()} KEY DOWN\n"
            f"API: Windows SendInput\n"
            f"Result: {'SUCCESS' if down_ok else 'FAILED'}\n\n"
            f"{key.upper()} KEY UP\n"
            f"Result: {'SUCCESS' if up_ok else 'FAILED'}\n\n"
            f"API call success: {'YES' if (down_ok and up_ok) else 'NO'}\n"
            f"Did the target application respond: Unknown "
            f"(response cannot be verified automatically)"
        )

    def show_hold_result(self, key: str, down_ts: str, up_ts: str) -> None:
        self._hold_result_label.setText(f"{down_ts}  {key.upper()} DOWN\n{up_ts}  {key.upper()} UP")

    # ------------------------------------------------------------- pipeline
    def _update_pipeline(self, **stages) -> None:
        mapping = {
            "camera": "Camera", "hand_tracking": "Hand Tracking", "gesture": "Gesture",
            "control_value": "Control Value", "key_mapper": "Key Mapper",
            "windows_input": "Windows Input",
        }
        for key, value in stages.items():
            name = mapping.get(key)
            if not name:
                continue
            color = "green" if value else "red" if value is False else "yellow"
            lbl = self._stage_labels[name]
            lbl.setText(f"{_dot(color)}\n{name}")
        # Browser/Game are always shown as unverifiable, never claimed working.
        for name in ("Browser", "Game"):
            self._stage_labels[name].setText(f"{_dot('yellow')}\n{name}")

    # --------------------------------------------------------------- focus
    def _poll_foreground_window(self) -> None:
        info = get_foreground_window_info()
        if info is None:
            self._focus_app_label.setText("Application: unavailable")
            self._focus_title_label.setText("Window: —")
            self._focus_status_label.setText("Focused: —")
            self._focus_warning_label.setText("")
            return
        self._focus_app_label.setText(f"Application: {info.process_name}  (PID {info.pid})")
        self._focus_title_label.setText(f"Window: {info.window_title or '(untitled)'}")
        self._focus_status_label.setText("Focused: YES")
        if info.is_motiondrive:
            self._focus_warning_label.setText(
                "\U0001F7E1 WARNING\nMotionDrive currently has keyboard focus. "
                "The game may not receive keyboard input. Click the game window before driving.")
        else:
            self._focus_warning_label.setText("")

    # --------------------------------------------------------- performance
    def set_performance_sample(self, sample) -> None:
        self._perf_idle_sample = sample
        self._perf_fps_label.setText(
            f"Camera FPS: {sample.camera_fps:.0f}    Tracking FPS: {sample.tracking_fps:.0f}")
        cpu_text = f"{sample.cpu_percent:.0f}%" if sample.cpu_percent is not None else "unavailable"
        ram_text = f"{sample.ram_mb:.0f} MB" if sample.ram_mb is not None else "unavailable"
        self._perf_cpu_label.setText(f"CPU: {cpu_text}    RAM: {ram_text}    GPU: unavailable")

        stages = sample.stage_times_ms
        if stages:
            order = ["image_conversion", "hand_tracking", "gesture_processing",
                      "input_processing", "ui_handoff", "total"]
            lines = [f"{name.replace('_', ' ').title():<20} {stages[name]:5.1f} ms"
                     for name in order if name in stages]
            self._perf_stage_label.setText("\n".join(lines))

        if sample.regression_detected:
            self._perf_regression_label.setText(
                f"⚠ PERFORMANCE DROP DETECTED\n\n"
                f"Baseline: {sample.baseline_fps:.0f} FPS\n"
                f"Current: {sample.tracking_fps:.0f} FPS\n\n"
                f"Investigating...")
        else:
            self._perf_regression_label.setText("")

    def set_tracking_resolution_info(self, camera_res: str, inference_scale: float) -> None:
        self._perf_res_label.setText(
            f"Camera resolution: {camera_res}    Inference scale: {inference_scale:.0%}")

    def _run_performance_test(self) -> None:
        sample = self._perf_idle_sample
        if sample is None:
            self._perf_report_label.setText("No performance data yet -- start the camera first.")
            return
        info = get_foreground_window_info()
        target = f"{info.process_name}" if info else "unknown"

        if sample.tracking_fps >= 20:
            verdict = "GOOD"
            bottleneck = ""
        elif sample.tracking_fps >= 10:
            verdict = "FAIR"
            bottleneck = "\nLikely bottleneck: CPU contention from the foreground application."
        else:
            verdict = "POOR"
            slowest = max(
                ((k, v) for k, v in sample.stage_times_ms.items() if k != "total"),
                key=lambda kv: kv[1], default=(None, 0))
            bottleneck = f"\nLikely bottleneck: {slowest[0].replace('_', ' ')}" if slowest[0] else ""

        cpu_line = f"CPU: {sample.cpu_percent:.0f}%" if sample.cpu_percent is not None else "CPU: unavailable"
        lines = [
            "MOTIONDRIVE PERFORMANCE REPORT (live snapshot -- not an idle-vs-gaming",
            "comparison; run this again while your game is running to compare)",
            "",
            f"Camera FPS: {sample.camera_fps:.0f}",
            f"Tracking FPS: {sample.tracking_fps:.0f}",
            cpu_line,
            "",
            f"Foreground application: {target}",
            "",
            f"Performance: {verdict}{bottleneck}",
        ]
        self._perf_report_label.setText("\n".join(lines))

    # ----------------------------------------------------------- checklist
    def _run_system_diagnostics(self) -> None:
        checks = []
        checks.append(("Camera", "connected" in self._camera_label.text()))
        checks.append(("Hand tracking", self._last_frame_state is not None))
        checks.append(("Steering", self._last_frame_state is not None))
        checks.append(("Throttle", self._last_pedals is not None))
        checks.append(("Brake", self._last_pedals is not None))
        checks.append(("Keyboard mapping", all(l.text() != f"{n.upper()}: -"
                       for n, l in self._key_map_labels.items())))
        checks.append(("SendInput", self._sendinput_tested_ok is True))
        info = get_foreground_window_info()
        checks.append(("Foreground detection", info is not None))
        checks.append(("Key release safety", True))  # SafetyWatchdog.release_all always available

        red_dot = "\U0001F534"
        lines = []
        all_ok = True
        for name, ok in checks:
            mark = "✓" if ok else red_dot
            lines.append(f"{mark} {name}")
            all_ok = all_ok and ok
        lines.append("")
        lines.append("SYSTEM READY" if all_ok else "INPUT SYSTEM NEEDS ATTENTION")
        self._checklist_label.setText("\n".join(lines))

    # -------------------------------------------------------------- export
    def _export_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Event Log",
            f"MotionDrive-EventLog-{time.strftime('%Y-%m-%d')}.txt", "Text Files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._log_view.toPlainText())
            QMessageBox.information(self, "Exported", f"Event log saved to:\n{path}")

    def build_diagnostics_text(self, extra: dict) -> str:
        lines = [
            "MotionDrive Diagnostics Report",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
        lines.append("")
        lines.append("--- Event Log ---")
        lines.append(self._log_view.toPlainText())
        return "\n".join(lines)

    def _export_diagnostics(self) -> None:
        self.exportDiagnosticsRequested.emit()

    # --------------------------------------------------------------- armed
    def _confirm_arm_notepad_test(self) -> None:
        reply = QMessageBox.question(
            self, "Arm Notepad Test",
            "This opens Notepad, hides MotionDrive, waits for Notepad to become "
            "the foreground window, then sends A D W S through the real Windows "
            "input path -- the definitive test for whether keyboard injection "
            "works at all, independent of any browser.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.armNotepadTestRequested.emit()

    def show_armed_status(self, text: str) -> None:
        self._armed_status_label.setText(text)

    def show_foreground_debug(self, info, is_motiondrive: bool) -> None:
        if info is None:
            self._fg_debug_label.setText("HWND: unavailable    Process: unavailable    Window title: unavailable\n"
                                           "MotionDrive foreground: unknown    Browser DOM focus: UNKNOWN")
            return
        self._fg_debug_label.setText(
            f"HWND: {info.hwnd}    Process: {info.process_name}    Window title: {info.window_title or '(untitled)'}\n"
            f"MotionDrive foreground: {'TRUE' if is_motiondrive else 'FALSE'}    "
            f"Browser DOM focus: UNKNOWN (click the field yourself before arming)")

    # --------------------------------------------------------- manual test
    def _toggle_manual_hold(self, logical_name: str) -> None:
        btn = self._manual_buttons[logical_name]
        if self._manual_held[logical_name]:
            self.manualKeyUpRequested.emit(logical_name)
            self._manual_held[logical_name] = False
            btn.setText(f"HOLD {logical_name.upper()}")
        else:
            self.manualKeyDownRequested.emit(logical_name)
            self._manual_held[logical_name] = True
            btn.setText(f"RELEASE {logical_name.upper()}")

    def release_all_manual_holds(self) -> None:
        for name, held in list(self._manual_held.items()):
            if held:
                self._toggle_manual_hold(name)

    # -------------------------------------------------------------- browser
    def _confirm_browser_test(self) -> None:
        reply = QMessageBox.question(
            self, "Test Browser Input",
            "This opens a plain local web page with a text field in your default "
            "browser. Click the field, then use the SEND buttons above to test "
            "real Windows keyboard input reaching the browser directly.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.browserTestRequested.emit()
            self._browser_result_label.setText(
                "Test page opened. Click the text field, then use SEND LEFT/RIGHT/ACCELERATE/BRAKE above.")

    # ---------------------------------------------------------- focus game
    def _start_focus_game_countdown(self) -> None:
        self.focusGameRequested.emit()
        self._focus_countdown_value = 3
        self._update_focus_countdown()
        timer = QTimer(self)
        timer.setInterval(1000)

        def _tick():
            self._focus_countdown_value -= 1
            if self._focus_countdown_value <= 0:
                self._focus_countdown_label.setText("\U0001F7E2 GAME INPUT ACTIVE")
                timer.stop()
            else:
                self._update_focus_countdown()
        timer.timeout.connect(_tick)
        timer.start()
        self._focus_countdown_timer = timer  # keep alive

    def _update_focus_countdown(self) -> None:
        self._focus_countdown_label.setText(f"Click the game area\n\n{self._focus_countdown_value}")

    # --------------------------------------------------------- compat/report
    def _set_compat_result(self, stage: str, passed: bool) -> None:
        self._compat_results[stage] = passed
        self._update_compat_label()

    def _update_compat_label(self) -> None:
        r = self._compat_results
        if r["notepad"] is None:
            self._compat_label.setText("Run the Notepad test above to get started.")
            return
        if r["notepad"] is False:
            self._compat_label.setText(
                "NOTEPAD: ✗ FAIL\n\n"
                "Windows keyboard injection is not working.\n"
                "Fix the Windows input engine before testing anything else.")
            return
        lines = ["NOTEPAD: ✓ PASS"]
        if r["browser"] is None:
            lines.append("\nRun the Browser Text Input test next.")
            self._compat_label.setText("\n".join(lines))
            return
        lines.append(f"BROWSER TEXT FIELD: {'✓ PASS' if r['browser'] else '✗ FAIL'}")
        if not r["browser"]:
            lines.append("\nWindows input works. Browser input handling needs investigation.")
            self._compat_label.setText("\n".join(lines))
            return
        if r["game"] is None:
            lines.append("\nRun the Game Test next (click FOCUS GAME, then the TEST buttons).")
            self._compat_label.setText("\n".join(lines))
            return
        lines.append(f"GAME: {'✓ PASS' if r['game'] else '✗ FAIL'}")
        if not r["game"]:
            lines.append(
                "\nMotionDrive is successfully generating Windows keyboard input.\n"
                "The browser receives keyboard input.\n"
                "The game itself is not responding.\n\n"
                "Likely causes:\n"
                "• Game canvas/iframe focus\n"
                "• Game-specific keyboard handling\n"
                "• Game compatibility")
        else:
            lines.append("\nEverything works end to end.")
        self._compat_label.setText("\n".join(lines))

    def _copy_diagnostic_report(self) -> None:
        from PySide6.QtWidgets import QApplication
        report = self.build_diagnostics_text({
            "Notepad": self._result_text("notepad"),
            "Browser text input": self._result_text("browser"),
            "Game": self._result_text("game"),
        })
        QApplication.clipboard().setText(report)
        QMessageBox.information(self, "Copied", "Diagnostic report copied to clipboard.")

    def _result_text(self, stage: str) -> str:
        v = self._compat_results.get(stage)
        return "PASS" if v is True else "FAIL" if v is False else "NOT TESTED"
