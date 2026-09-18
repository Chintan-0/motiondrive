"""Automated UI & window lifecycle tests for MainWindow, TitleBar, and StartupOverlay."""
import sys
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from motiondrive.ui.main_window import MainWindow, _CloseDialog
from motiondrive.ui.startup_overlay import StartupOverlay
from motiondrive.ui.titlebar import TitleBar, _CaptionButton


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    app.processEvents()


def _cleanup_window(window, qapp):
    try:
        if hasattr(window, "startup_overlay") and window.startup_overlay is not None:
            window.startup_overlay.stop()
        if hasattr(window, "engine") and window.engine is not None:
            window.engine.stop_camera()
            window.engine.emergency_stop()
        # window.close() triggers the real close-confirmation dialog
        # (_CloseDialog). Most tests here don't mock it, so with no real
        # user to click it, exec() would just sit there / return a
        # non-accepted result -- closeEvent then does event.ignore() and
        # the window is never actually closed. That left one live
        # MainWindow (hotkeys still registered and all) dangling per test,
        # which made test_single_top_level_window fail nondeterministically
        # depending on execution order, since a "closed" window from an
        # earlier test was still a real top-level widget. Force the dialog
        # to the accept path here so cleanup is deterministic regardless of
        # what any individual test already did or didn't mock.
        original_exec = _CloseDialog.exec

        def _force_close(self):
            self.choice = "close"
            return QDialog.Accepted

        _CloseDialog.exec = _force_close
        try:
            window.close()
        finally:
            _CloseDialog.exec = original_exec
        window.deleteLater()
    except Exception:
        pass
    qapp.processEvents()
    qapp.processEvents()  # deleteLater() can need more than one pass to fully release native window/hotkey state


def test_main_window_creates_successfully(qapp):
    window = MainWindow()
    assert window is not None
    assert window.windowTitle() == "MotionDrive"
    _cleanup_window(window, qapp)


def test_startup_overlay_belongs_to_main_window(qapp):
    window = MainWindow()
    assert hasattr(window, "startup_overlay")
    overlay = window.startup_overlay
    assert isinstance(overlay, StartupOverlay)
    assert not overlay.isWindow()  # Must NOT be a separate top-level window!
    assert overlay.rect() == window.rect()
    _cleanup_window(window, qapp)


def test_single_top_level_window(qapp):
    window = MainWindow()
    window.show()
    # Filtered to *visible* windows deliberately: close() only hides a
    # widget, it doesn't destroy it -- the underlying object stays a real
    # (but hidden, closed) QMainWindow until the event loop actually
    # processes its deleteLater(), which isn't guaranteed to have happened
    # yet by the time an earlier test's cleanup returns. Counting every
    # MainWindow object still technically alive in memory made this test
    # fail nondeterministically depending on execution order and GC timing
    # -- what actually matters (and what the "one window" startup redesign
    # is about) is that the user is never shown more than one MotionDrive
    # window at once, i.e. visible top-level windows, not Python object
    # lifetime.
    visible_top_levels = [
        w for w in QApplication.topLevelWidgets()
        if isinstance(w, MainWindow) and w.isVisible()
    ]
    assert len(visible_top_levels) == 1
    _cleanup_window(window, qapp)


def test_titlebar_minimize_button(qapp):
    window = MainWindow()
    window.show()
    assert hasattr(window, "titlebar")
    titlebar = window.titlebar
    assert isinstance(titlebar.min_btn, _CaptionButton)
    titlebar.min_btn.click()
    assert window.isMinimized()
    window.showNormal()
    _cleanup_window(window, qapp)


def test_titlebar_maximize_and_restore_buttons(qapp):
    window = MainWindow()
    window.showNormal()
    assert not window.isMaximized()

    titlebar = window.titlebar
    titlebar.max_btn.click()
    assert window.isMaximized()

    titlebar.max_btn.click()
    assert not window.isMaximized()

    _cleanup_window(window, qapp)


def test_titlebar_close_button(qapp, monkeypatch):
    window = MainWindow()
    window.show()
    window.engine.settings.close_to_tray = False

    # Mock dialog execution so headless test doesn't block on GUI modal exec()
    def _mock_exec(self):
        self.choice = "close"
        return QDialog.Accepted

    monkeypatch.setattr(_CloseDialog, "exec", _mock_exec)
    window.titlebar.close_btn.click()
    _cleanup_window(window, qapp)


def test_closing_during_startup_does_not_crash(qapp, monkeypatch):
    window = MainWindow()
    window.show()

    def _mock_exec(self):
        self.choice = "close"
        return QDialog.Accepted

    monkeypatch.setattr(_CloseDialog, "exec", _mock_exec)
    overlay = window.startup_overlay
    assert overlay is not None
    overlay.start()
    _cleanup_window(window, qapp)


def test_startup_overlay_reveals_home(qapp):
    window = MainWindow()
    window.show()
    overlay = window.startup_overlay
    overlay.skip()
    assert not overlay.isVisible()
    _cleanup_window(window, qapp)


def test_start_and_play_does_not_relaunch_game_when_already_running(qapp, monkeypatch):
    """Regression test: _start_and_play() used to launch the configured
    game/browser target unconditionally on every click. Clicking START &
    PLAY a second time while already driving (e.g. after restoring
    MotionDrive from the tray) would open a second browser tab or a
    duplicate game process instead of just re-focusing the existing
    session."""
    import motiondrive.ui.main_window as mw_module
    from motiondrive.controller_state import ControllerState

    window = MainWindow()

    # Camera "starts" successfully without touching real hardware, and the
    # controller flips to RUNNING the first time start_controller() is
    # called -- controller_running is a real property derived from
    # controller_state, so setting that state directly exercises the same
    # code path _start_and_play() actually checks.
    monkeypatch.setattr(window.engine, "camera_running", True)
    monkeypatch.setattr(window.engine, "start_camera", lambda: None)

    def fake_start_controller():
        window.engine.controller_state = ControllerState.RUNNING

    monkeypatch.setattr(window.engine, "start_controller", fake_start_controller)

    window.engine.settings.game_exe_path = ""
    window.engine.settings.game_url = "https://slowroads.io/"

    open_calls = []
    monkeypatch.setattr(mw_module.webbrowser, "open", lambda url: open_calls.append(url))
    monkeypatch.setattr(window, "_hide_and_play", lambda: None)  # skip the real hide/game-mode side effects

    # _start_and_play() defers its actual work by one event-loop tick (so
    # the "PREPARING..." button state has a chance to paint before the
    # blocking camera-start call) -- call the inner method directly here
    # to exercise the actual dedup logic without needing a real event loop.
    window._do_start_and_play_inner()
    window._do_start_and_play_inner()  # simulates a re-click while already driving

    assert open_calls == ["https://slowroads.io/"]  # opened exactly once, not twice
    _cleanup_window(window, qapp)


def test_resolve_game_url_falls_back_to_active_profile(qapp):
    """Regression test: Settings.game_url is only ever written by
    engine.apply_profile() -- it does NOT get filled in just because
    `active_profile` names a real built-in preset. A real settings.json
    was found with active_profile="Racing Limits - CrazyGames" (a genuine
    preset with its own game_url) while game_url sat stuck at "" -- which
    meant START & PLAY silently opened nothing. _resolve_game_url() must
    look up the active profile's own URL rather than trusting the
    possibly-stale copy on Settings."""
    window = MainWindow()
    window.engine.settings.active_profile = "Racing Limits - CrazyGames"
    window.engine.settings.game_url = ""  # the exact stale/never-synced state found in the wild

    assert window._resolve_game_url() == "https://slowroads.io/"
    _cleanup_window(window, qapp)


def test_offscreen_saved_geometry_healed_on_startup(qapp):
    window = MainWindow()
    s = window.engine.settings
    s.remember_window_position = True
    s.window_x = -5000
    s.window_y = -5000
    s.window_width = 1180
    s.window_height = 860

    window._restore_window_geometry()
    geo = window.geometry()

    # Geometry must be moved into a visible screen work area and fit within work area
    from PySide6.QtGui import QGuiApplication
    primary = QGuiApplication.primaryScreen().availableGeometry()
    assert geo.x() >= primary.left() - 100
    assert geo.y() >= primary.top() - 100
    assert geo.width() <= primary.width()
    assert geo.height() <= primary.height()
    _cleanup_window(window, qapp)


def test_ensure_window_visible_preserves_valid_geometry(qapp):
    window = MainWindow()
    from PySide6.QtGui import QGuiApplication
    primary = QGuiApplication.primaryScreen().availableGeometry()
    x = primary.left() + 50
    y = primary.top() + 50
    w = 1000
    h = 640
    window.setGeometry(x, y, w, h)

    window.ensure_window_visible()
    assert window.geometry().x() == x
    assert window.geometry().y() == y
    assert window.geometry().width() == w
    assert window.geometry().height() == h
    _cleanup_window(window, qapp)


def test_startup_overlay_remains_child_widget(qapp):
    window = MainWindow()
    window.show()
    overlay = getattr(window, "startup_overlay", None)
    assert overlay is not None
    assert overlay.parent() is window.centralWidget()
    assert not overlay.isWindow()
    _cleanup_window(window, qapp)


def test_ensure_window_visible_fixes_top_left_clipped_geometry(qapp):
    window = MainWindow()
    from PySide6.QtGui import QGuiApplication
    primary = QGuiApplication.primaryScreen().availableGeometry()
    # Clipped off top-left (e.g. x = -150, y = -40 relative to primary screen left/top)
    window.setGeometry(primary.left() - 150, primary.top() - 40, 1180, 640)

    window.ensure_window_visible()
    geo = window.geometry()
    assert geo.x() >= primary.left()
    assert geo.y() >= primary.top()
    _cleanup_window(window, qapp)


def test_ensure_window_visible_fixes_right_bottom_clipped_geometry(qapp):
    window = MainWindow()
    from PySide6.QtGui import QGuiApplication
    primary = QGuiApplication.primaryScreen().availableGeometry()
    p_right = primary.left() + primary.width()
    p_bottom = primary.top() + primary.height()
    # Clipped off right-bottom
    window.setGeometry(primary.right() - 200, primary.bottom() - 200, 1180, 860)

    window.ensure_window_visible()
    geo = window.geometry()
    assert geo.x() + geo.width() <= p_right
    assert geo.y() + geo.height() <= p_bottom
    _cleanup_window(window, qapp)


def test_ensure_window_visible_recovers_completely_offscreen_geometry(qapp):
    window = MainWindow()
    from PySide6.QtGui import QGuiApplication
    primary = QGuiApplication.primaryScreen().availableGeometry()
    p_right = primary.left() + primary.width()
    p_bottom = primary.top() + primary.height()
    window.setGeometry(primary.left() + 5000, primary.top() + 5000, 1180, 860)

    window.ensure_window_visible()
    geo = window.geometry()
    assert geo.x() >= primary.left()
    assert geo.y() >= primary.top()
    assert geo.x() + geo.width() <= p_right
    assert geo.y() + geo.height() <= p_bottom
    _cleanup_window(window, qapp)


def test_verify_startup_visibility_honors_start_minimized(qapp):
    window = MainWindow()
    window.engine.settings.start_minimized = True
    window.showMinimized()
    window._verify_startup_visibility()
    assert window.isMinimized()
    _cleanup_window(window, qapp)

