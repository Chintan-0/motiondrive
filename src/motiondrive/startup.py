"""Real Windows "start with Windows" registration via the per-user Run
registry key (HKCU\\...\\Run) -- no admin rights needed, no installer
changes required. This is a genuine OS-level integration, not a Settings
field that silently does nothing when toggled."""
from __future__ import annotations

import sys

from motiondrive.logging_ import get_logger

log = get_logger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "MotionDrive"


def _launch_command() -> str:
    """The command Windows should run at login. For the frozen exe this is
    just the exe path; for a source checkout (dev) it's the venv's
    pythonw.exe running the app module, so 'start with Windows' is testable
    without a build."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m motiondrive.app'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        log.exception("Could not read Windows startup registry state (assuming disabled)")
        return False


def enable() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
        log.info("Registered MotionDrive to start with Windows")
        return True
    except Exception:
        log.exception("Failed to register MotionDrive to start with Windows")
        return False


def disable() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass  # already not registered -- not an error
        log.info("Unregistered MotionDrive from starting with Windows")
        return True
    except Exception:
        log.exception("Failed to unregister MotionDrive from starting with Windows")
        return False


def set_enabled(enabled: bool) -> bool:
    return enable() if enabled else disable()
