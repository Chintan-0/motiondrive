"""Central place for all on-disk locations MotionDrive uses.

Everything lives under %LOCALAPPDATA%\\MotionDrive so a normal, non-admin
Windows user can install/uninstall cleanly and nothing touches Program Files
at runtime.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _local_app_data() -> Path:
    env = os.environ.get("LOCALAPPDATA")
    if env:
        return Path(env)
    # Fallback for non-Windows dev/test environments.
    return Path.home() / ".local" / "share"


APP_DATA_DIR = _local_app_data() / "MotionDrive"
LOGS_DIR = APP_DATA_DIR / "logs"
CONFIG_DIR = APP_DATA_DIR / "config"
PROFILES_DIR = APP_DATA_DIR / "profiles"
CALIBRATION_FILE = CONFIG_DIR / "calibration.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
FIRST_RUN_FLAG = CONFIG_DIR / "first_run_complete.flag"


def ensure_dirs() -> None:
    for d in (APP_DATA_DIR, LOGS_DIR, CONFIG_DIR, PROFILES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resource_path(relative: str) -> Path:
    """Resolve a bundled asset path, working from source, _MEIPASS, or frozen executable directory."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / relative
        if cand.exists():
            return cand

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        cand1 = exe_dir / relative
        if cand1.exists():
            return cand1
        cand2 = exe_dir / "_internal" / relative
        if cand2.exists():
            return cand2

    # Running from source: project_root/relative
    source_root = Path(__file__).resolve().parent.parent.parent
    return source_root / relative
