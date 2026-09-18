"""Top-level window: sidebar navigation + stacked pages, wired to the engine.

Also owns: window geometry persistence, close/minimize-to-tray behavior,
global hotkeys (work even while a game has focus), and the floating camera
overlay lifecycle."""
from __future__ import annotations

import subprocess
import sys
import webbrowser

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                                 QPushButton, QStackedWidget, QLabel, QButtonGroup,
                                 QMessageBox, QDialog, QFrame, QSizeGrip)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT),
                    ("ptMaxPosition", wintypes.POINT), ("ptMinTrackSize", wintypes.POINT),
                    ("ptMaxTrackSize", wintypes.POINT)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                    ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]

from motiondrive.calibration import Calibration
from motiondrive.camera import list_cameras
from motiondrive.controller_state import ControllerState, TrackingState
from motiondrive.engine import MotionDriveEngine
from motiondrive.paths import FIRST_RUN_FLAG, ensure_dirs, resource_path
from motiondrive.profiles import Profile, ProfileStore
from motiondrive.settings import Settings
from motiondrive.ui.error_dialogs import show_friendly_error, CAMERA_START_TIPS, GAMEPAD_TIPS
from motiondrive.ui.theme import COLOR_RED, COLOR_GREEN, COLOR_YELLOW, COLOR_ACCENT
from motiondrive.ui.floating_overlay import FloatingCameraOverlay
from motiondrive.ui.hotkeys import GlobalHotkeyManager
from motiondrive.ui.pages.about import AboutPage
from motiondrive.ui.pages.calibration import CalibrationPage
from motiondrive.ui.pages.dashboard import DashboardPage
from motiondrive.ui.pages.diagnostics import DiagnosticsPage
from motiondrive.ui.pages.onboarding import WelcomePage, SystemCheckPage
from motiondrive.ui.pages.settings import SettingsPage
from motiondrive.ui.pages.test_controller import TestControllerPage
from motiondrive.ui.startup_overlay import StartupOverlay
from motiondrive.ui.titlebar import TitleBar, _CaptionButton
from motiondrive.ui.tray import TrayController
from motiondrive.ui.phone_dialog import ControllerSourceDialog, PhoneQRDialog
from motiondrive.logging_ import get_logger

log = get_logger(__name__)

NAV_ITEMS = [
    ("dashboard", "Home"),
    ("test", "Controller"),
    ("settings", "Settings"),
    ("about", "About"),
]
NAV_ICONS = {"dashboard": "🏠", "test": "🎮", "settings": "⚙", "about": "ℹ"}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        log.info("Initializing MainWindow")

        # Geometry state MUST exist before anything that can trigger
        # moveEvent/resizeEvent/changeEvent -- including the very first
        # resize() call a few lines down and _restore_window_geometry()
        # later. Qt can and does deliver these events synchronously during
        # construction; relying on "it won't fire yet" was the bug that
        # caused a startup crash (AttributeError on _geometry_save_timer
        # accessed from moveEvent before __init__ had created it).
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.timeout.connect(self._save_window_geometry)
        self._restoring_geometry = False
        self._geometry_initialized = False
        self._last_geometry = None
        log.info("Geometry save timer initialized")

        self.setWindowTitle("MotionDrive")
        # Custom title bar replaces the native one -- but deliberately does
        # NOT use Qt.FramelessWindowHint, which strips the window's native
        # Win32 styles (WS_CAPTION/WS_THICKFRAME) entirely. That's what
        # caused the previous version's degraded window behavior: no
        # taskbar entry when minimized, maximize covering the taskbar
        # instead of respecting the work area, and no edge/corner resize.
        # Instead this keeps the window a genuinely normal, natively-framed
        # top-level window and only hides its native chrome VISUALLY, via
        # the standard "extend client area into the frame" recipe used by
        # Windows Terminal/VS Code/Chrome: nativeEvent() (below) answers
        # WM_NCCALCSIZE with "the whole window is client area" (so Windows
        # paints no native titlebar/border) and WM_NCHITTEST to relabel
        # which pixels count as caption/resize edges -- while the window
        # underneath is still a real WS_OVERLAPPEDWINDOW, so Windows' own
        # DefWindowProc keeps handling taskbar presence, minimize/maximize
        # animations, work-area-aware maximize sizing (via WM_GETMINMAXINFO,
        # also handled below), and Aero snap exactly as it would for any
        # normal window -- none of that is reimplemented by hand.
        # WA_TranslucentBackground lets the root widget's own top-corner
        # radius show as real transparency instead of a black/white square
        # artifact (same proven pattern as _CloseDialog).
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(1180, 860)
        self.setMinimumSize(980, 640)

        icon_path = resource_path("assets/icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.engine = MotionDriveEngine()
        self.engine.frameReady.connect(self._on_frame)
        self.engine.cameraError.connect(self._on_camera_error)
        self.engine.controllerStateChanged.connect(self._on_controller_state_changed)
        self.engine.trackingStateChanged.connect(self._on_tracking_state_changed)
        self.engine.trackingHintChanged.connect(self._on_hint)
        self.engine.testSweepValue.connect(self._on_test_sweep_value)
        self.engine.keyStateChanged.connect(self._on_key_state_changed)
        self.engine.performanceSample.connect(self._on_performance_sample)
        self.engine.performanceRegressionChanged.connect(self._on_performance_regression)

        self._build_ui()
        self._build_tray()
        self._build_floating_overlay()
        self._restore_window_geometry()
        self._geometry_initialized = True
        # _restore_window_geometry() can change self's size after the
        # startup overlay was already sized in _build_ui() -- and Qt does
        # not fire resizeEvent for a setGeometry() call made before the
        # window is shown, so the resizeEvent-based sync never catches
        # this on its own. Re-sync here so overlay.rect() is correct
        # immediately, not just after the next event-loop tick.
        overlay = getattr(self, "startup_overlay", None)
        if overlay is not None:
            overlay.setGeometry(self.rect())
        log.info("MainWindow initialized")

        # ESC stays a local (in-window-focus) emergency stop; the
        # configurable global hotkey (default F8) is the one that works
        # even while a game has focus -- registered once the native window
        # handle exists.
        esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        esc_shortcut.activated.connect(self._emergency_stop)
        self._hotkeys: GlobalHotkeyManager | None = None
        QTimer.singleShot(0, self._register_hotkeys)

        ensure_dirs()
        if not FIRST_RUN_FLAG.exists():
            QTimer.singleShot(0, self._start_first_launch)
        else:
            self.settings_page.load(self.engine.settings)
            self.test_page.load(self.engine.settings)
        log.info("MotionDrive ready")

        # Deferred to the next event-loop tick for the same reason the old
        # splash's fade-in animation was: QPropertyAnimation only ticks
        # once the real event loop is pumping, which doesn't happen until
        # app.exec() starts (after this constructor returns and app.py
        # calls show()/showMinimized()) -- by which point self.isMinimized()
        # below correctly reflects whichever one app.py used.
        QTimer.singleShot(0, self._start_startup_overlay)

    def _start_startup_overlay(self) -> None:
        overlay = getattr(self, "startup_overlay", None)
        if overlay is None:
            return
        # Re-sync geometry one more time before showing anything: the
        # overlay was originally sized from self.rect() back in _build_ui(),
        # which runs BEFORE _restore_window_geometry() -- and Qt does not
        # fire resizeEvent for a setGeometry() call made on a widget that
        # hasn't been shown yet (confirmed directly: 0 resizeEvents for a
        # pre-show setGeometry), so the resizeEvent-based sync elsewhere
        # never catches this. Without this line, any returning user whose
        # saved window size differs from the 1180x860 default would see the
        # overlay rendered at the WRONG (stale, default) size -- exactly
        # the "desktop visible around the splash" bug this overlay exists
        # to prevent. By this point (a singleShot(0) tick after show()),
        # self.rect() reflects the window's true final, restored geometry.
        overlay.setGeometry(self.rect())
        # Nothing is visible to animate toward if launching minimized, and
        # honor the existing (if currently UI-less) "Play startup
        # animation" setting -- either way, skip straight to Home.
        if self.isMinimized() or not self.engine.settings.splash_enabled:
            overlay.skip()
        else:
            overlay.start()

    def _on_startup_overlay_finished(self) -> None:
        log.info("Startup overlay finished; Home revealed")

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        # Plain QWidget doesn't paint its QSS background-color/border-radius
        # by default (only QFrame does that automatically) -- without this,
        # root would stay visually transparent now that the top-level
        # window itself is WA_TranslucentBackground, showing the desktop
        # through the whole app instead of just the rounded top corners.
        root.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(root)
        outer_layout = QVBoxLayout(root)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.titlebar = TitleBar()
        self.titlebar.minimizeRequested.connect(self.showMinimized)
        self.titlebar.maximizeRestoreRequested.connect(self._toggle_maximize_restore)
        self.titlebar.closeRequested.connect(self.close)
        outer_layout.addWidget(self.titlebar)

        body = QWidget()
        root_layout = QHBoxLayout(body)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        outer_layout.addWidget(body, stretch=1)

        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("background-color: #0a0e14;")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 24, 16, 20)
        side_layout.setSpacing(6)

        logo_row = QVBoxLayout()
        logo_row.setSpacing(6)
        logo_row.setAlignment(Qt.AlignHCenter)
        icon_path = resource_path("assets/icon_256.png")
        if icon_path.exists():
            logo_lbl = QLabel()
            logo_lbl.setPixmap(QIcon(str(icon_path)).pixmap(48, 48))
            logo_lbl.setAlignment(Qt.AlignCenter)
            logo_row.addWidget(logo_lbl)
        brand = QLabel("MOTIONDRIVE")
        brand.setAlignment(Qt.AlignCenter)
        brand.setStyleSheet("font-size: 12px; font-weight: 800; letter-spacing: 1.5px;")
        logo_row.addWidget(brand)
        side_layout.addLayout(logo_row)
        side_layout.addSpacing(16)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_buttons = {}
        for key, label in NAV_ITEMS:
            btn = QPushButton(f"{NAV_ICONS[key]}   {label}")
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, k=key: self._go_to(k))
            side_layout.addWidget(btn)
            self.nav_group.addButton(btn)
            self._nav_buttons[key] = btn
            if key == "settings":
                # Help & Tips opens a modal, not a stacked page -- deliberately
                # not part of the exclusive nav_group/page_map, just placed in
                # the same visual position the final nav order calls for.
                help_btn = QPushButton("❓   Help & Tips")
                help_btn.setObjectName("navItem")
                help_btn.clicked.connect(self._show_help_and_tips)
                side_layout.addWidget(help_btn)
        self._nav_buttons["dashboard"].setChecked(True)
        side_layout.addStretch()

        self.sidebar_status_lbl = QLabel("● CONTROLLER OFF")
        self.sidebar_status_lbl.setStyleSheet(
            f"color: {COLOR_RED}; font-size: 11px; font-weight: 700; padding-left: 4px;")
        side_layout.addWidget(self.sidebar_status_lbl)
        side_layout.addSpacing(8)

        root_layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, stretch=1)

        self.welcome_page = WelcomePage()
        self.welcome_page.getStarted.connect(self._start_system_check)

        self.system_check_page = SystemCheckPage()
        self.system_check_page.continueClicked.connect(self._after_system_check)

        self.dashboard_page = DashboardPage()
        self.dashboard_page.startAndPlay.connect(self._start_and_play)

        self.calibration_page = CalibrationPage()
        self.calibration_page.finished.connect(self._on_calibration_finished)
        self.calibration_page.cancelled.connect(lambda: self._go_to("dashboard"))

        self.settings_page = SettingsPage()
        self.settings_page.settingsChanged.connect(self._on_settings_changed)
        self.settings_page.profileChangeRequested.connect(self._on_profile_selected)

        self.test_page = TestControllerPage()
        self.test_page.emergencyStop.connect(self._emergency_stop)

        self.about_page = AboutPage()

        self.diagnostics_page = DiagnosticsPage()
        self.diagnostics_page.releaseAllRequested.connect(self._release_all_active_keys)
        self.diagnostics_page.testKeyRequested.connect(self.engine.test_physical_key)
        self.diagnostics_page.holdKeyRequested.connect(self.engine.hold_key_test)
        self.diagnostics_page.exportDiagnosticsRequested.connect(self._export_diagnostics)
        self.diagnostics_page.manualKeyDownRequested.connect(self.engine.manual_key_down)
        self.diagnostics_page.manualKeyUpRequested.connect(self.engine.manual_key_up)
        self.diagnostics_page.browserTestRequested.connect(self._open_browser_test_page)
        self.diagnostics_page.focusGameRequested.connect(self._focus_game)
        self.diagnostics_page.armNotepadTestRequested.connect(self._arm_notepad_test)
        self.diagnostics_page.armBrowserTestRequested.connect(self._arm_browser_test)
        self.diagnostics_page.testRightIn2SecondsRequested.connect(self._test_right_in_2_seconds)
        self.engine.diagnosticEvent.connect(self.diagnostics_page.add_event)
        self.engine.keyTestResult.connect(self.diagnostics_page.show_key_test_result)
        self.engine.holdTestResult.connect(
            lambda key, d, u: self.diagnostics_page.show_hold_result(key, d, u))
        self.diagnostics_page.set_key_mapping(self.engine._key_map())
        self._last_focused_browser_process: str | None = None
        self._focus_watch_timer: QTimer | None = None
        self._game_target_process: str | None = None
        self._armed_test_timer: QTimer | None = None

        for w in (self.welcome_page, self.system_check_page, self.dashboard_page,
                  self.calibration_page, self.settings_page,
                  self.test_page, self.about_page, self.diagnostics_page):
            self.stack.addWidget(w)

        self.stack.setCurrentWidget(self.dashboard_page)
        self.titlebar.set_page_name("Home")

        # Corner resize handle -- a plain QSizeGrip, not a custom native
        # hit-test region (see the long comment on _hit_test for why: a
        # hand-rolled edge/corner WM_NCHITTEST resize turned out to get
        # visibly "stuck" mid-drag on real hardware). QSizeGrip drives Qt's
        # own resize path instead of a native live-drag loop, so it can't
        # hit that class of bug. Free-floating child of `root` so it can
        # sit on top of the stack in the bottom-right corner without taking
        # layout space; repositioned in resizeEvent.
        self._size_grip = QSizeGrip(root)
        self._size_grip.setStyleSheet("background: transparent;")
        self._size_grip.setFixedSize(16, 16)
        self._size_grip.raise_()

        # Startup "ignition" overlay -- a child widget covering the whole
        # window (title bar included), not a second top-level window.
        # Everything above is already fully built and correctly sized/
        # positioned underneath it by this point, so the user only ever
        # sees one stable window with a splash on top of it, never a
        # separate small window animating into place.
        #
        # Sized from self.rect() rather than root.rect(): root is the
        # QMainWindow's central widget, and its geometry is only finalized
        # by an internal layout pass that (this early, before show()) may
        # not have run yet -- reading root.rect() here could return a
        # small/default size, making the overlay itself tiny and exposing
        # raw desktop around it instead of covering the window. self, as
        # the top-level widget, already reflects the resize() call earlier
        # in __init__ immediately and synchronously, with no such lag.
        self.startup_overlay = StartupOverlay(root)
        self.startup_overlay.setGeometry(self.rect())
        self.startup_overlay.raise_()
        self.startup_overlay.finished.connect(self._on_startup_overlay_finished)

    def _build_tray(self) -> None:
        icon_path = resource_path("assets/icon.ico")
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        self.tray = TrayController(icon, self)
        self.tray.openRequested.connect(self._restore_from_tray)
        self.tray.startRequested.connect(self._start_controller)
        self.tray.stopRequested.connect(self._stop_controller)
        self.tray.pauseResumeRequested.connect(lambda: self.engine.toggle_pause())
        self.tray.showOverlayRequested.connect(self._force_show_overlay)
        self.tray.hideOverlayRequested.connect(self._force_hide_overlay)
        self.tray.settingsRequested.connect(lambda: self._go_to("settings"))
        self.tray.exitRequested.connect(self._exit_from_tray)
        self.tray.show()

    def _build_floating_overlay(self) -> None:
        self.floating_overlay = FloatingCameraOverlay()
        self.floating_overlay.hideRequested.connect(self._force_hide_overlay)
        self.floating_overlay.settingsRequested.connect(lambda: (self._restore_from_tray(), self._go_to("settings")))
        self.floating_overlay.geometryChanged.connect(self._save_overlay_geometry)
        self.floating_overlay.compactToggled.connect(self._save_overlay_compact)
        self._overlay_force_hidden = False
        s = self.engine.settings
        if s.overlay_x >= 0 and s.overlay_y >= 0:
            self.floating_overlay.setGeometry(s.overlay_x, s.overlay_y, s.overlay_width, s.overlay_height)
        else:
            self.floating_overlay.resize(s.overlay_width, s.overlay_height)
        self.floating_overlay.set_compact(s.overlay_compact)
        self._sync_overlay()

    def _force_hide_overlay(self) -> None:
        self._overlay_force_hidden = True
        self.floating_overlay.hide()

    def _force_show_overlay(self) -> None:
        self._overlay_force_hidden = False
        self._sync_overlay()

    def _sync_overlay(self) -> None:
        s = self.engine.settings
        self.floating_overlay.set_input_mode(s.input_mode)
        self.floating_overlay.apply_settings(
            opacity_percent=s.overlay_opacity,
            show_landmarks=s.overlay_show_landmarks,
            show_labels=s.overlay_show_labels,
            show_fps=s.overlay_show_fps,
            show_wheel=s.overlay_show_wheel,
        )
        should_show = (not self._overlay_force_hidden) and (
            s.overlay_mode == "always" or
            (s.overlay_mode == "active_only" and self.engine.controller_running)
        )
        if should_show and self.engine.camera_running:
            self.floating_overlay.show()
        else:
            self.floating_overlay.hide()

    def _save_overlay_geometry(self, x: int, y: int, w: int, h: int) -> None:
        s = self.engine.settings
        s.overlay_x, s.overlay_y, s.overlay_width, s.overlay_height = x, y, w, h
        self.engine.settings_store.save(s)
        self.settings_page.sync_geometry_fields(s)

    def _save_overlay_compact(self, compact: bool) -> None:
        s = self.engine.settings
        s.overlay_compact = compact
        self.engine.settings_store.save(s)
        self.settings_page.sync_geometry_fields(s)

    # ---------------------------------------------------------- hotkeys
    def _register_hotkeys(self) -> None:
        try:
            self._hotkeys = GlobalHotkeyManager(int(self.winId()), self)
            self._hotkeys.triggered.connect(self._on_hotkey)
            self._apply_hotkey_bindings()
        except Exception:
            log.exception("Failed to register global hotkeys (non-fatal)")

    def _apply_hotkey_bindings(self) -> None:
        if not self._hotkeys:
            return
        self._hotkeys.unregister_all()
        self._hotkeys.register("stop", self.engine.settings.hotkey_stop)
        self._hotkeys.register("pause", self.engine.settings.hotkey_pause)
        self._hotkeys.register("mute", getattr(self.engine.settings, "hotkey_mute", "Ctrl+Shift+M"))
        # Fixed developer-diagnostic hotkeys (spec: F9-F12) -- always
        # available, regardless of which window is focused, so a real
        # foreground-input test never requires clicking MotionDrive at all.
        self._hotkeys.register("test_right", "F9")
        self._hotkeys.register("test_left", "F10")
        self._hotkeys.register("test_accelerate", "F11")
        self._hotkeys.register("test_brake", "F12")

    def _on_hotkey(self, id_name: str) -> None:
        if id_name == "stop":
            self.engine.emergency_stop()
            self.tray.notify("MotionDrive", f"Controller stopped. Press {self.engine.settings.hotkey_stop} to stop/start.")
        elif id_name == "pause":
            self.engine.toggle_pause()
        elif id_name == "mute":
            is_muted = self.engine.toggle_mute()
            msg = "Global Mute ACTIVE (Inputs Neutralized)" if is_muted else "Global Mute OFF"
            self.tray.notify("MotionDrive Emergency Mute", msg)
        elif id_name.startswith("test_"):
            self._hotkey_test_send(id_name[len("test_"):])

    def _hotkey_test_send(self, logical_name: str) -> None:
        """F9-F12 diagnostic test: RegisterHotKey delivers WM_HOTKEY without
        ever changing which window is foreground, so this never needs to
        (and never does) bring MotionDrive forward -- the key event goes
        straight to whatever the user was already driving."""
        from motiondrive.diagnostics import get_foreground_window_info
        info = get_foreground_window_info()
        if info and info.is_motiondrive:
            self.engine._log_event(
                f"Hotkey test ({logical_name}) skipped -- MotionDrive still has keyboard focus")
            return
        key = self.engine._key_map().get(logical_name, logical_name)
        self.engine._send_raw_key(key, True)
        QTimer.singleShot(120, lambda k=key: self.engine._send_raw_key(k, False))

    # --------------------------------------------------------------- nav
    def _go_to(self, key: str) -> None:
        page_map = {
            "dashboard": self.dashboard_page,
            "test": self.test_page,
            "settings": self.settings_page,
            "about": self.about_page,
            "diagnostics": self.diagnostics_page,
        }
        if key in self._nav_buttons:
            self._nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(page_map[key])
        page_titles = dict(NAV_ITEMS)
        self.titlebar.set_page_name(page_titles.get(key, ""))

    # -------------------------------------------------------- first launch
    def _start_first_launch(self) -> None:
        self.stack.setCurrentWidget(self.welcome_page)

    def _start_system_check(self) -> None:
        self.stack.setCurrentWidget(self.system_check_page)
        QTimer.singleShot(300, self._run_system_check)

    def _run_system_check(self) -> None:
        cameras = list_cameras()
        camera_ok = len(cameras) > 0
        tracking_ok = True
        try:
            import mediapipe  # noqa: F401
        except Exception:
            tracking_ok = False
        controller_ok = True
        try:
            import vgamepad  # noqa: F401
        except Exception:
            controller_ok = False
        import platform
        compat_ok = platform.system() == "Windows" or True  # allow dev on other OS
        self.system_check_page.set_results(camera_ok, tracking_ok, controller_ok, compat_ok)

    def _after_system_check(self) -> None:
        FIRST_RUN_FLAG.parent.mkdir(parents=True, exist_ok=True)
        FIRST_RUN_FLAG.write_text("1", encoding="utf-8")
        self.settings_page.refresh_cameras()
        self.settings_page.load(self.engine.settings)
        self.test_page.load(self.engine.settings)
        self._go_to("dashboard")
        if not self.engine.settings.onboarding_completed:
            QTimer.singleShot(0, self._show_first_launch_help)

    def _show_first_launch_help(self) -> None:
        dlg = _HelpDialog(self, first_time=True)
        dlg.exec()
        self.engine.settings.onboarding_completed = True
        self.engine.settings_store.save(self.engine.settings)

    def _show_help_and_tips(self) -> None:
        _HelpDialog(self, first_time=False).exec()

    # -------------------------------------------------------------- camera
    # No forced calibration wizard anywhere camera/controller start: Calibration
    # ships with sane, usable defaults (see motiondrive.calibration), so a
    # first-time user drives immediately on default values instead of being
    # walked through a technical wizard. The wizard itself (CalibrationPage/
    # CalibrationWizard) still exists and still works internally -- it's
    # just not reachable from the normal UI anymore.
    def _on_camera_error(self, message: str) -> None:
        self.dashboard_page.set_camera_active(False)
        self.test_page.set_camera_active(False)
        self.diagnostics_page.set_camera_state(False, 0.0)
        self._sync_overlay()
        if self.engine.game_mode_active:
            self.tray.notify("Camera couldn't be started", message)
        else:
            show_friendly_error(self, "Camera couldn't be started.", message, CAMERA_START_TIPS)

    # ---------------------------------------------------------- controller
    def _start_controller(self) -> None:
        self.engine.start_controller()

    def _stop_controller(self) -> None:
        self.engine.stop_controller("Stopped by user")

    def _emergency_stop(self) -> None:
        self.engine.emergency_stop()

    def _hide_and_play(self) -> None:
        """Primary dashboard button once the controller is RUNNING/PAUSED:
        hide the window, keep everything running in the background."""
        self.engine.enable_game_mode()
        self.floating_overlay.set_game_mode(True)
        self._start_focus_watch()
        self.hide()
        self.tray.notify("MotionDrive", "Still driving in the background. "
                          f"Press {self.engine.settings.hotkey_stop} to stop.")

    def _start_focus_watch(self) -> None:
        """Polls the foreground window at a low frequency (500ms, matching
        the diagnostics page's own cadence -- never per-frame) only while
        actually gaming, and shows a small non-modal overlay warning if the
        game/browser loses focus. Never re-focuses anything itself."""
        try:
            from motiondrive.diagnostics import get_foreground_window_info
            info = get_foreground_window_info()
        except Exception:
            info = None
        self._game_target_process = info.process_name if info else self._last_focused_browser_process

        if self._focus_watch_timer is None:
            self._focus_watch_timer = QTimer(self)
            self._focus_watch_timer.setInterval(500)
            self._focus_watch_timer.timeout.connect(self._check_game_focus)
        self._focus_watch_timer.start()

    def _stop_focus_watch(self) -> None:
        if self._focus_watch_timer is not None:
            self._focus_watch_timer.stop()
        self.floating_overlay.set_focus_lost_warning(False)
        self.floating_overlay.set_input_target_status("", False)

    def _check_game_focus(self) -> None:
        try:
            from motiondrive.diagnostics import get_foreground_window_info
            info = get_foreground_window_info()
        except Exception:
            return
        if info is None or not self._game_target_process:
            return
        lost = info.process_name != self._game_target_process
        self.floating_overlay.set_focus_lost_warning(lost)
        self.floating_overlay.set_input_target_status(self._game_target_process, not lost)

    def _resolve_game_url(self) -> str:
        """The active preset's browser-game URL. Checked in three places,
        each covering a real, confirmed way the "obvious" source can be
        stale or missing:

        1. BUILTIN_GAME_URLS -- a pure in-memory, always-current lookup.
           Preferred first because a *stored* Profile file for a built-in
           preset can predate game_url existing at all (ProfileStore only
           merges dataclass fields actually present in the saved JSON), in
           which case the loaded Profile object's own game_url is "" even
           though the real preset has always had a URL.
        2. The stored Profile itself, for a CUSTOM preset (or any future
           non-built-in preset) the user has given its own URL to.
        3. Settings.game_url, as a last resort -- it's only ever written by
           engine.apply_profile(), so it does NOT get filled in just
           because `active_profile` names a real preset. A fresh install
           (whose default active_profile is a real built-in preset) or any
           settings.json saved before the user ever touched the profile
           dropdown had active_profile pointing at a valid preset while
           game_url sat stuck at "" -- confirmed via a real settings file
           with exactly that mismatch."""
        from motiondrive.profiles import BUILTIN_GAME_URLS
        active = self.engine.settings.active_profile
        builtin_url = BUILTIN_GAME_URLS.get(active, "")
        if builtin_url:
            return builtin_url
        try:
            profile = ProfileStore().get(active)
        except Exception:
            profile = None
        if profile and profile.game_url:
            return profile.game_url
        return self.engine.settings.game_url

    def _start_and_play(self) -> None:
        """The single primary Home action: prompt for controller source (HANDS vs PHONE),
        pair via QR if phone, then start controller and launch target game."""
        source_dlg = ControllerSourceDialog(
            current_source=getattr(self.engine.settings, "controller_source", "hands"),
            parent=self
        )
        if source_dlg.exec() != QDialog.Accepted:
            return

        selected_source = source_dlg.selected_source
        self.engine.settings.controller_source = selected_source
        self.engine.settings_store.save(self.engine.settings)

        if selected_source == "phone":
            if not self.engine.phone_server.running:
                if not self.engine.phone_server.start():
                    QMessageBox.warning(self, "Phone Controller Error", "Failed to start local phone controller server.")
                    return
            qr_dlg = PhoneQRDialog(self.engine.phone_server, parent=self)
            if qr_dlg.exec() != QDialog.Accepted:
                self.engine.phone_server.stop()
                return

        self.dashboard_page.set_preparing(True)
        QTimer.singleShot(0, self._do_start_and_play)

    def _do_start_and_play(self) -> None:
        try:
            self._do_start_and_play_inner()
        finally:
            self.dashboard_page.set_preparing(False)

    def _do_start_and_play_inner(self) -> None:
        source = getattr(self.engine.settings, "controller_source", "hands")
        if source == "hands" and not self.engine.camera_running:
            self.engine.start_camera()
            if not self.engine.camera_running:
                return
            self._overlay_force_hidden = False
            self.dashboard_page.set_camera_active(True)
            self.test_page.set_camera_active(True)
            self.diagnostics_page.set_camera_state(True, 0.0)
            self._sync_overlay()

        # Whether THIS click is the one actually starting a session, versus
        # a re-click while already driving (e.g. restored from the tray and
        # pressed START & PLAY again). Without this check, the game/browser
        # launch below ran unconditionally on every click -- a second click
        # while already live would open a duplicate browser tab or a second
        # copy of the configured game exe.
        already_running = self.engine.controller_running
        if not already_running:
            self.engine.start_controller()

        if not already_running:
            game_path = self.engine.settings.game_exe_path
            if game_path:
                try:
                    subprocess.Popen([game_path])
                except Exception:
                    log.exception("Failed to launch configured game exe (non-fatal)")
            else:
                game_url = self._resolve_game_url()
                if game_url:
                    try:
                        # Opens in the user's default browser -- MotionDrive
                        # never embeds the game, it stays a desktop controller.
                        webbrowser.open(game_url)
                    except Exception:
                        log.exception("Failed to open configured game URL (non-fatal)")
        self._hide_and_play()

    def _on_controller_state_changed(self, state: ControllerState, reason: str) -> None:
        self.dashboard_page.set_controller_state(state)
        self.test_page.set_controller_state(state)
        self.tray.set_controller_state(state)
        self._update_sidebar_status(state)
        self._sync_overlay()
        quiet_reasons = ("Stopped by user", "Controller started", "Controller paused",
                          "Controller resumed", "Gamepad test finished")
        if state == ControllerState.OFF and reason not in quiet_reasons:
            self.tray.notify("MotionDrive", reason)
        if state == ControllerState.OFF and "gamepad" in reason.lower():
            # Never a modal popup while the user is gaming (spec: no dialogs
            # can steal focus from the game) -- a tray notification instead.
            if self.engine.game_mode_active:
                self.tray.notify("Controller unavailable", reason)
            else:
                show_friendly_error(self, "Controller unavailable", reason, GAMEPAD_TIPS)
        if state == ControllerState.OFF:
            self.test_page.clear_key_state()
            self.floating_overlay.clear_key_state()

    def _update_sidebar_status(self, state: ControllerState) -> None:
        labels = {
            ControllerState.OFF: ("● CONTROLLER OFF", COLOR_RED),
            ControllerState.RUNNING: ("● CONTROLLER LIVE", COLOR_GREEN),
            ControllerState.PAUSED: ("● CONTROLLER PAUSED", COLOR_YELLOW),
        }
        text, color = labels[state]
        self.sidebar_status_lbl.setText(text)
        self.sidebar_status_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 700; padding-left: 4px;")

    def _on_tracking_state_changed(self, state: TrackingState) -> None:
        self.dashboard_page.set_tracking_state(state)
        self.test_page.set_tracking_state(state)
        self.floating_overlay.set_tracking_state(state)

    def _on_hint(self, text: str) -> None:
        self.dashboard_page.show_hint(text)

    def _on_key_state_changed(self, key_states: dict) -> None:
        self.dashboard_page.update_key_states(key_states)
        self.test_page.update_key_state(key_states)
        self.floating_overlay.update_key_state(key_states)
        self.diagnostics_page.set_key_states(key_states)

    def _on_performance_sample(self, sample) -> None:
        if self.stack.currentWidget() is self.diagnostics_page:
            self.diagnostics_page.set_performance_sample(sample)
        self.floating_overlay.set_performance_sample(sample)

    def _on_performance_regression(self, regressed: bool) -> None:
        # Small, unobtrusive overlay hint only -- never a popup, never
        # changes settings automatically (spec: ask, don't auto-switch).
        self.floating_overlay.set_performance_warning(regressed)

    def _on_test_sweep_value(self, steering: float, throttle: float, brake: float) -> None:
        from motiondrive.pedals import PedalState
        from motiondrive.steering import SteeringState, TrackingQuality
        state = SteeringState(value=steering, angle_deg=steering * 35, raw_angle_deg=0.0,
                               quality=TrackingQuality.EXCELLENT, hand_distance=0.35)
        self.test_page.update_state(None, state, PedalState(throttle, brake), fps=0.0)

    # -------------------------------------------------------------- frames
    def _on_frame(self, qimage, tracking, state, pedals, fps) -> None:
        # Only the page actually on screen gets the (comparatively expensive
        # -- QPainter drawing 21 landmarks x2 hands) full frame render. A
        # hidden page doing this every camera frame was pure wasted UI-thread
        # work competing with tracking for CPU, especially under contention
        # from a demanding foreground app (e.g. a WebGL browser game).
        current = self.stack.currentWidget()
        if current is self.dashboard_page:
            self.dashboard_page.on_frame(qimage, tracking, state, pedals, fps)
        elif current is self.calibration_page:
            self.calibration_page.on_frame(qimage, tracking, state, pedals)
        elif current is self.test_page:
            self.test_page.update_state(tracking, state, pedals, fps, qimage=qimage)
        elif current is self.diagnostics_page:
            self.diagnostics_page.on_frame(tracking, state, pedals, fps)

        if self.floating_overlay.isVisible():
            self.floating_overlay.update_frame(qimage, tracking, state, pedals, fps)

    # --------------------------------------------------------- calibration
    # No UI entry point navigates here anymore (Quick Calibration was
    # removed from the sidebar/Controller per the simplified normal UI --
    # Calibration ships with sane defaults, so a first-time user never
    # needs the wizard). calibration_page itself stays instantiated and
    # wired (finished/cancelled below) so the underlying implementation
    # keeps working if it's ever reconnected.
    def _go_to_widget(self, widget) -> None:
        self.stack.setCurrentWidget(widget)

    def _on_calibration_finished(self, calibration: Calibration) -> None:
        self.engine.apply_calibration(calibration)
        self._go_to("dashboard")

    # ------------------------------------------------------------ settings
    def _on_settings_changed(self, settings: Settings) -> None:
        self.engine.apply_settings(settings)
        self.dashboard_page.set_profile_name(settings.active_profile)
        self.diagnostics_page.set_key_mapping(self.engine._key_map())
        # Settings and Controller each own half of the same Settings object
        # (Settings: camera/controls/performance/overlay/application;
        # Controller: steering feel) -- whichever page didn't originate this
        # change needs to be refreshed so the two never show stale values
        # relative to each other. Guarded by sender() so a slider drag on
        # one page never triggers a redundant full reload (profile list
        # rebuild, etc.) of itself on every single tick.
        sender = self.sender()
        if sender is not self.settings_page:
            self.settings_page.load(self.engine.settings)
        if sender is not self.test_page:
            self.test_page.load(self.engine.settings)
        self._sync_overlay()
        self._apply_hotkey_bindings()

    # ------------------------------------------------------------ profiles
    def _on_profile_selected(self, profile: Profile) -> None:
        self.engine.apply_profile(profile)
        self.settings_page.load(self.engine.settings)
        self.test_page.load(self.engine.settings)
        self.dashboard_page.set_profile_name(profile.name)

    # ------------------------------------------------------------- window
    def _toggle_maximize_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ---------------------------------------------- native window chrome
    # See the __init__ comment above self.setAttribute(WA_TranslucentBackground)
    # for why this exists: the window keeps its real native frame styles
    # (WS_CAPTION/WS_THICKFRAME) so taskbar/minimize/maximize/snap all keep
    # working via Windows' own DefWindowProc, and these three handlers only
    # relabel which pixels are drawn/hit-tested as what.
    def nativeEvent(self, eventType, message):
        if sys.platform != "win32" or eventType != b"windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        try:
            msg = wintypes.MSG.from_address(int(message))
        except Exception:
            return super().nativeEvent(eventType, message)

        if msg.message == 0x0083:  # WM_NCCALCSIZE
            # wParam != 0 means "you may adjust the proposed client rect";
            # returning 0 without touching it keeps the client rect equal
            # to the whole window rect, i.e. Windows paints no native
            # titlebar/border at all -- our own TitleBar fills that space
            # instead. wParam == 0 (rare, legacy) needs the default handling.
            if msg.wParam:
                return True, 0
            return super().nativeEvent(eventType, message)

        if msg.message == 0x0024:  # WM_GETMINMAXINFO
            # Without this, maximizing a window whose native frame is fully
            # claimed as client area (via WM_NCCALCSIZE above) makes Windows
            # size it to the *entire monitor*, covering the taskbar -- this
            # constrains it back to the monitor's work area, exactly like a
            # normal window's maximize.
            self._apply_minmax_info(msg.lParam)
            return True, 0

        if msg.message == 0x0086:  # WM_NCACTIVATE
            # Without this, Windows can briefly paint its own NATIVE
            # caption buttons (a visibly different, larger style than our
            # custom TitleBar's) during an activation transition -- most
            # noticeably the very first time the window becomes active,
            # right as the startup overlay finishes and hands off to Home.
            # This is a well-documented gotcha of the WM_NCCALCSIZE
            # "extend into frame" technique: returning True here tells
            # Windows "the non-client area's active-state repaint is
            # handled, don't do your own default non-client drawing,"
            # which is exactly what a real native-chrome window would
            # otherwise trigger on activation change.
            return True, 1

        if msg.message == 0x0084:  # WM_NCHITTEST
            hit = self._hit_test(msg.lParam)
            if hit is not None:
                return True, hit

        return super().nativeEvent(eventType, message)

    def _apply_minmax_info(self, lparam: int) -> None:
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            if not monitor:
                return
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return
            work, mon = info.rcWork, info.rcMonitor
            mmi = _MINMAXINFO.from_address(lparam)
            mmi.ptMaxPosition.x = work.left - mon.left
            mmi.ptMaxPosition.y = work.top - mon.top
            mmi.ptMaxSize.x = work.right - work.left
            mmi.ptMaxSize.y = work.bottom - work.top
            mmi.ptMaxTrackSize.x = work.right - work.left
            mmi.ptMaxTrackSize.y = work.bottom - work.top
            min_size = self.minimumSize()
            mmi.ptMinTrackSize.x = min_size.width()
            mmi.ptMinTrackSize.y = min_size.height()
        except Exception:
            log.exception("WM_GETMINMAXINFO handling failed (non-fatal, uses default sizing)")

    def _hit_test(self, lparam: int):
        # Deliberately does NOT hand out edge/corner resize codes
        # (HTLEFT/HTRIGHT/HTTOP/HTBOTTOM/corners) here anymore -- that
        # existed in an earlier version of this window and turned out to be
        # the cause of a real, hard-to-reproduce bug: during an active
        # edge-drag resize, Windows renegotiates WM_NCCALCSIZE many times a
        # second as the frame is dragged, and this app's own repaint of the
        # (fully client-area-claimed) content couldn't always keep up --
        # the window would visibly get "stuck" with the outer frame at one
        # size and the painted content at another, revealing raw desktop
        # in the gap. Resizing is handled by the simple, proven QSizeGrip
        # in the corner instead (see _build_ui), which drives Qt's own
        # resize path rather than a live native drag loop. This hit-test
        # now only ever answers "is this the title bar's draggable area."
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        # WM_NCHITTEST's lParam is always PHYSICAL screen pixels (raw Win32
        # coordinates), but Qt's own coordinate system -- and therefore
        # mapFromGlobal()/childAt() below -- operates in LOGICAL (DPI-
        # scaled) pixels. Passing the physical point straight into
        # mapFromGlobal() without converting was a real bug: on any display
        # scaled above 100% (125%/150%/etc, extremely common), the
        # resulting "local" point lands a few pixels off from where the
        # click actually happened. That's not a hard failure -- clicks
        # dead-center on a button still happen to land inside its hitbox,
        # while clicks nearer an edge miss it -- which is exactly why this
        # showed up as "the title bar buttons work sometimes and not
        # others" starting from the very first launch, rather than a clean
        # always-broken or always-working result.
        dpr = self.devicePixelRatioF() or 1.0
        local = self.mapFromGlobal(QPoint(round(x / dpr), round(y / dpr)))
        titlebar = getattr(self, "titlebar", None)

        if titlebar is not None and 0 <= local.y() <= titlebar.height():
            tb_pos = titlebar.mapFrom(self, local)
            child = titlebar.childAt(tb_pos)
            if child is not None and (isinstance(child, _CaptionButton) or child in (titlebar.min_btn, titlebar.max_btn, titlebar.close_btn)):
                return 1    # HTCLIENT -- forces Windows to pass mouse events directly to Qt widget system!
            return 2        # HTCAPTION -- native window drag for empty space
        return None

    def _restore_from_tray(self) -> None:
        self._stop_focus_watch()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.engine.disable_game_mode()
        self.floating_overlay.set_game_mode(False)

    def ensure_window_visible(self) -> None:
        """Defensive window positioning helper: validates current geometry
        against available screen work areas and repositions the window to a
        safe visible area if it is off-screen, zero-sized, or clipped. Guarantees
        the title bar (y >= work.top()) and full window content remain accessible."""
        try:
            screens = QGuiApplication.screens()
            if not screens:
                return

            primary_screen = QGuiApplication.primaryScreen()
            primary_work = primary_screen.availableGeometry() if primary_screen else QRect(0, 0, 1920, 1080)

            rect = self.geometry()
            w = rect.width()
            h = rect.height()

            # Enforce minimum geometry dimensions if invalid/uninitialized
            if w <= 0 or h <= 0:
                w, h = 1180, 860

            min_w = self.minimumWidth()
            min_h = self.minimumHeight()

            # 1. Determine best target screen
            target_screen = None
            center = rect.center()

            # Check if center point lands on any available screen work area
            for screen in screens:
                if screen.availableGeometry().contains(center):
                    target_screen = screen
                    break

            # If center doesn't hit any screen, find screen with largest intersection area
            if target_screen is None:
                max_area = 0
                for screen in screens:
                    inter = screen.availableGeometry().intersected(rect)
                    area = inter.width() * inter.height()
                    if area > max_area:
                        max_area = area
                        target_screen = screen

            # Fallback to primary screen
            if target_screen is None:
                target_screen = primary_screen

            work = target_screen.availableGeometry() if target_screen else primary_work
            work_right = work.left() + work.width()
            work_bottom = work.top() + work.height()

            # Clamp dimensions to work area
            w = max(min_w, min(w, work.width()))
            h = max(min_h, min(h, work.height()))

            x = rect.x()
            y = rect.y()

            # Check intersection of candidate bounds with target work area
            inter = work.intersected(QRect(x, y, w, h))

            # If off-screen, zero-intersection, or severely clipped (< 200px visible in either dimension), center on work area
            if inter.isEmpty() or inter.width() < 200 or inter.height() < 200:
                log.warning(
                    "MainWindow geometry %dx%d at (%d, %d) is off-screen or severely clipped (work area: %s); centering on target screen",
                    rect.width(), rect.height(), rect.x(), rect.y(), work.getRect()
                )
                x = work.left() + max(0, (work.width() - w) // 2)
                y = work.top() + max(0, (work.height() - h) // 2)
            else:
                # Guarantee title bar visibility: y must never be above work.top()
                if y < work.top():
                    y = work.top()
                elif y > work_bottom - 40:
                    y = work.top()
                elif y + h > work_bottom:
                    y = max(work.top(), work_bottom - h)

                # Guarantee left and right boundaries stay inside work area
                if x < work.left():
                    x = work.left()
                elif x + w > work_right:
                    x = max(work.left(), work_right - w)

            new_rect = QRect(x, y, w, h)
            if new_rect != rect:
                log.info("Adjusted MainWindow geometry from %s to safe visible geometry %s on screen work %s",
                         rect.getRect(), new_rect.getRect(), work.getRect())
                self.setGeometry(new_rect)
        except Exception:
            log.exception("ensure_window_visible failed (non-fatal)")

    def _verify_startup_visibility(self) -> None:
        if self.engine.settings.start_minimized:
            return
        if not self.isVisible() or self.isMinimized():
            log.warning("Startup visibility recovery triggered: window was not visible/active; restoring to front")
            self.ensure_window_visible()
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

    def _restore_window_geometry(self) -> None:
        """Geometry persistence is a convenience feature -- it must never be
        able to prevent MotionDrive from launching. Any failure here logs a
        warning and falls back to the default geometry already set by the
        resize() call in __init__."""
        self._restoring_geometry = True
        try:
            s = self.engine.settings
            if not s.remember_window_position:
                log.info("Remember Window Position is off; using default window geometry")
                self.ensure_window_visible()
                return
            if s.window_width <= 0 or s.window_height <= 0:
                log.warning("Saved window geometry dimensions invalid; using safe default size")
                s.window_width, s.window_height = 1180, 860

            log.info("Restoring saved window geometry: %dx%d at (%d, %d)", s.window_width, s.window_height, s.window_x, s.window_y)
            self.setGeometry(s.window_x, s.window_y, s.window_width, s.window_height)
            self.ensure_window_visible()

            if s.window_maximized:
                self.showMaximized()
                log.info("Restored maximized state: True")
            log.info("Geometry restoration complete")
        except Exception:
            log.exception("Failed to restore saved window geometry -- using safe default geometry")
            self.ensure_window_visible()
        finally:
            self._restoring_geometry = False

    def _schedule_geometry_save(self) -> None:
        if self._restoring_geometry:
            return  # don't let the restore itself trigger an immediate re-save
        timer = getattr(self, "_geometry_save_timer", None)
        if timer is None:
            return
        try:
            timer.start(500)
        except RuntimeError:
            pass  # underlying Qt object already destroyed (shutting down)

    def _save_window_geometry(self) -> None:
        if self._restoring_geometry:
            return
        try:
            s = self.engine.settings
            if not s.remember_window_position:
                return
            s.window_maximized = self.isMaximized()
            # isMinimized() must also be excluded here, not just
            # isMaximized(): self.geometry() while genuinely minimized can
            # report Windows' internal off-screen "iconic" placeholder rect
            # rather than the real restored position -- saving that would
            # permanently corrupt the next launch's restore target instead
            # of just this session's live display (see
            # _heal_offscreen_geometry for the matching runtime safety net).
            if not self.isMaximized() and not self.isMinimized():
                geo = self.geometry()
                if geo.width() > 0 and geo.height() > 0:
                    s.window_x, s.window_y = geo.x(), geo.y()
                    s.window_width, s.window_height = geo.width(), geo.height()
            self.engine.settings_store.save(s)
            self.settings_page.sync_geometry_fields(s)
        except Exception:
            log.exception("Failed to save window geometry (ignored)")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        st = "MAXIMIZED" if self.isMaximized() else ("MINIMIZED" if self.isMinimized() else "NORMAL")
        log.info(f"WINDOW SHOWN: {self.width()}x{self.height()} (state: {st})")
        # One-time SWP_FRAMECHANGED nudge on the very first show: forces
        # Windows to re-evaluate the window's non-client area against our
        # WM_NCCALCSIZE override immediately, rather than potentially
        # painting its own default native caption buttons for the first
        # activation before the override "catches up" -- the other half of
        # the WM_NCACTIVATE fix in nativeEvent, both addressing the same
        # brief native-button flash right as the window first appears.
        if sys.platform == "win32" and not getattr(self, "_frame_refreshed", False):
            self._frame_refreshed = True
            try:
                SWP_FRAMECHANGED = 0x0020
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                ctypes.windll.user32.SetWindowPos(
                    int(self.winId()), 0, 0, 0, 0, 0,
                    SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            except Exception:
                log.exception("SWP_FRAMECHANGED refresh failed (non-fatal)")

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._schedule_geometry_save()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_geometry_save()
        overlay = getattr(self, "startup_overlay", None)
        if overlay is not None and overlay.isVisible():
            overlay.setGeometry(self.rect())
        grip = getattr(self, "_size_grip", None)
        if grip is not None:
            root = self.centralWidget()
            grip.move(root.width() - grip.width() - 2, root.height() - grip.height() - 2)
            grip.raise_()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            st = "MAXIMIZED" if self.isMaximized() else ("MINIMIZED" if self.isMinimized() else "NORMAL")
            log.info(f"WINDOW STATE: {st}")
            titlebar = getattr(self, "titlebar", None)
            if titlebar is not None:
                titlebar.set_maximized(self.isMaximized())
            overlay = getattr(self, "startup_overlay", None)
            if overlay is not None and overlay.isVisible():
                overlay.setGeometry(self.rect())
            grip = getattr(self, "_size_grip", None)
            if grip is not None:
                # A maximized window fills the screen -- no edge/corner to
                # grab, and leaving it visible would float a stray handle
                # over the content.
                grip.setVisible(not self.isMaximized() and not self.isFullScreen())
            # Minimize always behaves like a normal Windows app: stays in
            # the taskbar, never disappears to just the tray icon. Only an
            # explicit Close (see closeEvent, gated by close_to_tray) hides
            # the window entirely -- minimize-to-tray as a separate,
            # always-on-top-of-minimize behavior was removed.
            if st == "NORMAL":
                self._heal_offscreen_geometry()
            self._schedule_geometry_save()

    def _heal_offscreen_geometry(self) -> None:
        """Safety net for a real, confirmed bug: the custom WM_NCCALCSIZE
        chrome override can desync from Windows' own native minimize/
        restore animation, leaving the window's actual on-screen rect
        parked at Windows' internal "iconic" placeholder position (a
        coordinate far outside any monitor, roughly -32000 physical
        pixels, scaled by DPI) even though isMinimized() correctly reports
        False afterward -- confirmed by directly inspecting a live stuck
        window's real HWND rect via GetWindowRect while every one of
        MotionDrive's own settings/state said everything was normal. Runs
        every time the window transitions back to NORMAL state; if the
        current rect doesn't land on any real screen, snaps it back to the
        last known-good saved geometry (or centers on the primary screen)
        instead of leaving the user staring at what looks like a window
        that silently failed to open."""
        try:
            geo = QGuiApplication.primaryScreen().virtualGeometry()
            for screen in QGuiApplication.screens():
                geo = geo.united(screen.geometry())
            current = self.geometry()
            if geo.intersects(current):
                return  # on-screen -- nothing to heal
            log.warning("Window rect %s is off-screen after a state change; healing", current)
            s = self.engine.settings
            saved_rect = QRect(s.window_x, s.window_y, s.window_width, s.window_height)
            if s.window_x >= 0 and s.window_y >= 0 and geo.intersects(saved_rect):
                self.setGeometry(saved_rect)
            else:
                primary = QGuiApplication.primaryScreen().availableGeometry()
                w, h = max(self.minimumWidth(), 980), max(self.minimumHeight(), 640)
                self.setGeometry(primary.center().x() - w // 2, primary.center().y() - h // 2, w, h)
        except Exception:
            log.exception("Off-screen geometry healing failed (non-fatal)")

    def closeEvent(self, event) -> None:
        # If an active modal widget (e.g. PhoneQRDialog) is open, close/reject it so closeEvent proceeds cleanly
        active_modal = QApplication.activeModalWidget()
        if active_modal is not None and active_modal is not self:
            try:
                active_modal.reject()
            except Exception:
                pass

        if hasattr(self, "startup_overlay") and self.startup_overlay is not None:
            try:
                self.startup_overlay.stop()
            except Exception:
                pass
        if self.engine.settings.close_to_tray:
            event.ignore()
            self.hide()
            self.tray.notify("MotionDrive", "Still running in the system tray.")
            return

        dlg = _CloseDialog(self.engine.controller_running, self)
        if dlg.exec() == QDialog.Accepted and dlg.choice == "close":
            if self.engine.controller_running:
                self.engine.emergency_stop()
            self._save_window_geometry()
            self._quit()
            event.accept()
        else:
            event.ignore()

    def _exit_from_tray(self) -> None:
        if self.engine.controller_running:
            reply = QMessageBox.question(
                self, "Exit MotionDrive", "Stop controller and exit MotionDrive?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            self.engine.emergency_stop()
        self._quit()

    def _release_all_active_keys(self, reason: str = "Manual release") -> None:
        """The one authoritative key-release path: routes through
        SafetyWatchdog.release_all() (the same call used by stop_controller/
        emergency_stop/shutdown), which is backend-agnostic -- it releases
        whatever backend is currently armed, KeyboardBackend or
        VirtualGamepadBackend, without this UI code needing to know which.
        Also clears any diagnostics manual-hold keys, which live outside the
        watchdog entirely (raw SendInput calls for isolating the input
        layer) and would otherwise be left stuck down."""
        if getattr(self, "engine", None) is not None:
            self.engine.safety.release_all(reason)
        if getattr(self, "diagnostics_page", None) is not None:
            try:
                self.diagnostics_page.release_all_manual_holds()
            except Exception:
                log.exception("Error releasing diagnostics manual-hold keys (ignored)")

    def _open_browser_test_page(self) -> None:
        import tempfile
        import webbrowser
        from pathlib import Path
        html = """<!doctype html><html><head><title>MotionDrive Input Test</title>
<style>body{font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:40px;text-align:center}
input{font-size:24px;padding:12px;width:300px;text-align:center;letter-spacing:4px}</style></head>
<body><h2>MotionDrive Browser Input Test</h2>
<p>Click the field below, then go back to MotionDrive and click ARM BROWSER TEST.
Do not click MotionDrive's SEND buttons -- that would refocus MotionDrive, invalidating the test.</p>
<input id="f" autofocus placeholder="Keys will appear here"></body></html>"""
        path = Path(tempfile.gettempdir()) / "motiondrive_input_test.html"
        path.write_text(html, encoding="utf-8")
        webbrowser.open(path.as_uri())

    # -------------------------------------------------- armed input tests
    def _arm_notepad_test(self) -> None:
        import subprocess
        try:
            subprocess.Popen(["notepad.exe"])
        except Exception:
            log.exception("Could not launch Notepad for armed diagnostic test")
            self.diagnostics_page.show_armed_status(
                "NOTEPAD TEST ABORTED\n\nCouldn't launch Notepad.")
            return
        self.diagnostics_page.show_armed_status("NOTEPAD TEST ARMED\n\nWaiting for Notepad focus...")
        self.hide()
        keys = list(self.engine._key_map().values())  # [left, right, accelerate, brake]
        QTimer.singleShot(900, lambda: self._verify_and_send_armed("NOTEPAD", keys, 1))

    def _arm_browser_test(self) -> None:
        self.diagnostics_page.show_armed_status("BROWSER TEST ARMED\n\nReturning focus to the browser...")
        self.hide()
        keys = list(self.engine._key_map().values())
        QTimer.singleShot(700, lambda: self._verify_and_send_armed("BROWSER", keys, 1))

    def _test_right_in_2_seconds(self) -> None:
        # Countdown shown in-page BEFORE hiding (spec: never a modal dialog).
        self._armed_countdown_value = 2
        self.diagnostics_page.show_armed_status(f"Test armed\n\n{self._armed_countdown_value}")
        timer = QTimer(self)
        timer.setInterval(1000)

        def _tick():
            self._armed_countdown_value -= 1
            if self._armed_countdown_value <= 0:
                timer.stop()
                self.hide()
                key = self.engine._key_map().get("right", "d")
                QTimer.singleShot(500, lambda: self._verify_and_send_armed("RIGHT KEY", [key], 1))
            else:
                self.diagnostics_page.show_armed_status(f"Test armed\n\n{self._armed_countdown_value}")
        timer.timeout.connect(_tick)
        timer.start()
        self._armed_test_timer = timer  # keep alive

    def _verify_and_send_armed(self, label: str, keys: list[str], attempt: int) -> None:
        """Confirms target application is foreground before injecting keys.
        Aborts without sending if target is not foreground. Never steals focus back."""
        from motiondrive.diagnostics import get_foreground_window_info
        info = get_foreground_window_info()
        is_md = bool(info and info.is_motiondrive)
        self.diagnostics_page.show_foreground_debug(info, is_md)
        if is_md:
            if attempt < 3:
                QTimer.singleShot(400, lambda: self._verify_and_send_armed(label, keys, attempt + 1))
                return
            msg = "Browser test aborted: target application is not foreground."
            self.engine._log_event(f"[{label}] {msg}")
            self.diagnostics_page.show_armed_status(msg)
            return

        target = info.process_name if info else "unknown"
        title = info.window_title if info else "unknown"
        hwnd = info.hwnd if info else 0
        self.engine._log_event(f"[{label}] Target verified: HWND={hwnd}, Process={target}, Title='{title}'")
        self.diagnostics_page.show_armed_status(
            f"● {target} foreground (HWND {hwnd})\nSending {' '.join(k.upper() for k in keys)}...")
        self._send_armed_key_sequence(label, keys, target, 0)

    def _send_armed_key_sequence(self, label: str, keys: list[str], target: str, idx: int) -> None:
        if idx >= len(keys):
            self.engine._log_event(f"[{label}] Test sequence completed for {target}")
            self.diagnostics_page.show_armed_status(
                f"✓ {label} TEST SENT to {target}.\n\n"
                f"Check target application for result.")
            return
        key = keys[idx]
        self.engine._send_raw_key(key, True)
        QTimer.singleShot(150, lambda: self._release_armed_key(label, keys, target, idx, key))

    def _release_armed_key(self, label: str, keys: list[str], target: str, idx: int, key: str) -> None:
        self.engine._send_raw_key(key, False)
        QTimer.singleShot(250, lambda: self._send_armed_key_sequence(label, keys, target, idx + 1))

    def _focus_game(self) -> None:
        # Bring the last-known browser to the foreground ONCE -- never
        # repeatedly (spec: must not steal focus during gameplay).
        try:
            from motiondrive.diagnostics import get_foreground_window_info
            info = get_foreground_window_info()
        except Exception:
            info = None
        if info:
            self._last_focused_browser_process = info.process_name
        try:
            import win32gui
            hwnds = []
            def _cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                    hwnds.append(hwnd)
                return True
            win32gui.EnumWindows(_cb, None)
            for hwnd in hwnds:
                title = win32gui.GetWindowText(hwnd).lower()
                if any(b in title for b in ("chrome", "edge", "firefox")):
                    win32gui.SetForegroundWindow(hwnd)
                    break
        except Exception:
            log.exception("Could not bring browser to foreground (non-fatal)")

    def _export_diagnostics(self) -> None:
        import platform
        from PySide6.QtWidgets import QFileDialog, QMessageBox as MB
        from motiondrive import __version__

        s = self.engine.settings
        info = {
            "MotionDrive version": __version__,
            "Windows version": platform.platform(),
            "Camera index": s.camera_index,
            "Camera resolution": f"{s.camera_width}x{s.camera_height}",
            "Camera FPS setting": s.camera_fps,
            "Input mode": s.input_mode,
            "Key mapping": self.engine._key_map(),
            "Controller state": self.engine.controller_state.value,
            "Camera running": self.engine.camera_running,
        }
        text = self.diagnostics_page.build_diagnostics_text(info)
        import time
        default_name = f"MotionDrive-Diagnostics-{time.strftime('%Y-%m-%d')}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Export Diagnostics", default_name, "Text Files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            MB.information(self, "Exported", f"Diagnostics saved to:\n{path}")

    def _quit(self) -> None:
        log.info("Quitting MotionDrive -- releasing input, camera, and hotkeys")
        # Belt-and-suspenders: guarantee every release step runs even if an
        # earlier one raises, so the process never exits with the camera or
        # a key/axis still held. engine.shutdown() already stops the
        # controller (which releases all input) before releasing the camera.
        try:
            self.engine.shutdown()
        except Exception:
            log.exception("Error during engine shutdown (continuing quit anyway)")
        try:
            if self._hotkeys:
                self._hotkeys.unregister_all()
        except Exception:
            log.exception("Error unregistering hotkeys (ignored)")
        try:
            self.floating_overlay.hide()
        except Exception:
            log.exception("Error hiding overlay (ignored)")
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
        log.info("MotionDrive quit complete")


class _CloseDialog(QDialog):
    """Polished dark close-confirmation modal dialog with 16px corner radius."""

    def __init__(self, controller_running: bool, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(420, 220)
        self.choice = "cancel"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        card = QFrame()
        card.setObjectName("closeCard")
        # A bare `QFrame { ... }` type-selector stylesheet here (as this
        # used to be) still leaks its `border` onto every child widget
        # inside -- the title/subtitle QLabels below ended up individually
        # boxed with a border neither of them sets, confirmed by directly
        # rendering this dialog in isolation. Scoping to #closeCard
        # specifically (matched by the same technique used for the
        # Controller page's Quick Calibration card) fixes it.
        card.setStyleSheet("""
            QFrame#closeCard {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 16px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("Close MotionDrive?")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #e6edf3;")
        layout.addWidget(title)

        if controller_running:
            sub = QLabel("Stopping MotionDrive will release all active game controls.")
        else:
            sub = QLabel("Are you sure you want to close MotionDrive?")
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 13px; color: #8b96a5;")
        layout.addWidget(sub)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #21262d;
                border: 1px solid #30363d;
                color: #c9d1d9;
                padding: 8px 16px;
                border-radius: 8px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #30363d; }
        """)
        cancel_btn.clicked.connect(self._on_cancel)

        close_btn = QPushButton("Stop & Exit" if controller_running else "Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #da3633;
                border: none;
                color: #ffffff;
                padding: 8px 16px;
                border-radius: 8px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #f85149; }
        """)
        close_btn.clicked.connect(self._on_close)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        outer.addWidget(card)

    def _on_cancel(self) -> None:
        self.choice = "cancel"
        self.reject()

    def _on_close(self) -> None:
        self.choice = "close"
        self.accept()


_HOW_TO_PLAY = [
    "Sit comfortably in front of your camera.",
    "Put both hands up as if holding a steering wheel.",
    "Choose your game/control preset.",
    "Click START & PLAY.",
    "Move your hands left/right to steer.",
    "Use the hand gestures for throttle and brake.",
]
_BETTER_TRACKING = [
    "Keep both hands visible.",
    "Use good lighting.",
    "Keep your hands inside the camera frame.",
    "Avoid covering one hand with the other.",
    "Sit at a comfortable distance from the camera.",
]


class _HelpDialog(QDialog):
    """One shared HOW TO PLAY / BETTER TRACKING dialog -- used both for
    first-launch onboarding (spec: teach instead of forcing a calibration
    wizard) and for the sidebar's Help & Tips entry later, so the two
    never drift out of sync with duplicated copy."""

    def __init__(self, parent=None, first_time: bool = False):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        card = QFrame()
        card.setObjectName("helpCard")
        card.setStyleSheet("""
            QFrame#helpCard {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 16px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(10)

        title = QLabel("WELCOME TO MOTIONDRIVE" if first_time else "HELP & TIPS")
        title.setStyleSheet("font-size: 17px; font-weight: 800; color: #e6edf3; letter-spacing: 0.5px;")
        layout.addWidget(title)

        if first_time:
            sub = QLabel("Turn your hands into a steering wheel.")
            sub.setStyleSheet(f"font-size: 12px; color: {COLOR_ACCENT}; font-weight: 600;")
            layout.addWidget(sub)

        layout.addSpacing(6)
        how_title = QLabel("HOW TO PLAY")
        how_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #8b96a5; letter-spacing: 1px;")
        layout.addWidget(how_title)
        for i, step in enumerate(_HOW_TO_PLAY, start=1):
            row = QLabel(f"{i}.  {step}")
            row.setWordWrap(True)
            row.setStyleSheet("font-size: 12px; color: #c9d1d9;")
            layout.addWidget(row)

        layout.addSpacing(8)
        tips_title = QLabel("BETTER TRACKING")
        tips_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #8b96a5; letter-spacing: 1px;")
        layout.addWidget(tips_title)
        for tip in _BETTER_TRACKING:
            row = QLabel(f"•  {tip}")
            row.setWordWrap(True)
            row.setStyleSheet("font-size: 12px; color: #c9d1d9;")
            layout.addWidget(row)

        layout.addSpacing(6)
        preset_tip = QLabel("Tip: Start with BALANCED steering.")
        preset_tip.setStyleSheet(f"font-size: 11px; color: {COLOR_ACCENT}; font-weight: 600;")
        layout.addWidget(preset_tip)

        layout.addSpacing(10)
        got_it = QPushButton("GOT IT" if first_time else "Close")
        got_it.setObjectName("primary")
        got_it.clicked.connect(self.accept)
        layout.addWidget(got_it)

        outer.addWidget(card)
