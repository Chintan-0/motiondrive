"""Application entry point: single-instance guard, startup logging, splash screen, and main window lifecycle."""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from motiondrive import __version__
from motiondrive.paths import LOGS_DIR, CONFIG_DIR, ensure_dirs, resource_path
from motiondrive.logging_ import get_logger

log = get_logger("motiondrive.app")
STARTUP_LOG_FILE = LOGS_DIR / "startup.log"


def _log_startup_step(msg: str) -> None:
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    entry = f"[{timestamp}] {msg}\n"
    try:
        with open(STARTUP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass
    log.info(msg)


def _check_single_instance() -> bool:
    if sys.platform == "win32":
        try:
            import ctypes
            mutex_name = "Global\\MotionDrive_SingleInstance_Mutex_1_0_0"
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, True, mutex_name)
            err = kernel32.GetLastError()
            if err == 183:  # ERROR_ALREADY_EXISTS
                _log_startup_step("Single instance check: ALREADY RUNNING. Bringing window to front.")
                try:
                    user32 = ctypes.windll.user32
                    hwnd = user32.FindWindowW(None, "MotionDrive")
                    if not hwnd:
                        hwnd = user32.FindWindowW(None, "⚡ MotionDrive")
                    if hwnd:
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                return False
        except Exception:
            _log_startup_step("Single instance check failed (non-fatal)")
    return True


def _show_startup_crash_dialog(error_msg: str) -> None:
    _log_startup_step(f"CRASH: {error_msg}")
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("MotionDrive couldn't start")
        box.setText("Something went wrong while starting MotionDrive.\n\nA detailed startup log has been written.")
        open_log_btn = box.addButton("Open Log", QMessageBox.ActionRole)
        ok_btn = box.addButton("OK", QMessageBox.AcceptRole)
        box.setDefaultButton(ok_btn)
        box.exec()
        if box.clickedButton() == open_log_btn:
            try:
                os.startfile(str(STARTUP_LOG_FILE))
            except Exception:
                import subprocess
                subprocess.Popen(["notepad", str(STARTUP_LOG_FILE)])
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"MotionDrive couldn't start.\n\nLog: {STARTUP_LOG_FILE}",
                "MotionDrive Error",
                0x10,
            )
        except Exception:
            pass


def main() -> int:
    ensure_dirs()
    # Reset startup log for current launch
    try:
        STARTUP_LOG_FILE.write_text(f"=== MotionDrive v{__version__} Startup Log ===\n", encoding="utf-8")
    except Exception:
        pass

    _log_startup_step(f"MotionDrive starting v{__version__}")
    _log_startup_step(f"Executable: {sys.executable}")
    _log_startup_step(f"Resource path: {resource_path('assets')}")
    _log_startup_step(f"Config path: {CONFIG_DIR}")

    if not _check_single_instance():
        return 0

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QIcon
        from motiondrive.ui.theme import QSS
    except Exception as e:
        tb = traceback.format_exc()
        _show_startup_crash_dialog(f"Failed to load PySide6 / UI base dependencies:\n{tb}")
        return 1

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("MotionDrive")
    app.setStyleSheet(QSS)
    _log_startup_step("PySide6 Application: initialized")

    icon_path = resource_path("assets/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # MainWindow owns its own startup splash now (StartupOverlay, a child
    # widget covering its full window rect) rather than a separate small
    # splash window that later animated its geometry toward the real
    # window -- that "box growing into the corner" effect, and the brief
    # moment either window could be seen underneath/behind the other, is
    # exactly what this architecture removes: there is only ever one
    # window, built at its correct final size/position/theme, with the
    # ignition sequence playing on top of it as soon as it's shown. Its
    # fade-in animation only ticks once the real event loop is pumping
    # (same reason the old splash needed a `finished`-signal-driven
    # rebuild), which is naturally satisfied here since app.exec() runs
    # right after show().
    window_holder: dict = {}
    try:
        _log_startup_step("Creating MainWindow...")
        from motiondrive.ui.main_window import MainWindow
        from PySide6.QtGui import QGuiApplication
        window = MainWindow()

        screens = QGuiApplication.screens()
        screens_info = [s.availableGeometry().getRect() for s in screens]
        primary_work = QGuiApplication.primaryScreen().availableGeometry().getRect() if QGuiApplication.primaryScreen() else None
        _log_startup_step(f"Startup window diagnostics: screens={len(screens)} {screens_info}, primary_work={primary_work}")
        _log_startup_step(f"MainWindow geometry: {window.geometry().getRect()}")

        # "Start Minimized" still shows a real taskbar-visible window --
        # just minimized, never hidden to tray -- so it behaves like any
        # normal Windows app honoring that launch preference.
        if window.engine.settings.start_minimized:
            window.showMinimized()
            _log_startup_step("MainWindow state: MINIMIZED (start_minimized=True)")
        else:
            window.show()
            window.ensure_window_visible()
            window.raise_()
            window.activateWindow()
            _log_startup_step(f"MainWindow state: VISIBLE (geometry={window.geometry().getRect()}, minimized={window.isMinimized()}, visible={window.isVisible()})")

        window_holder["window"] = window  # keep a strong reference alive
        _log_startup_step("Main Window: created and shown")
        _log_startup_step("Startup: SUCCESS")
    except Exception:
        tb = traceback.format_exc()
        _show_startup_crash_dialog(f"Failed to initialize MainWindow:\n{tb}")
        return 1

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
