"""Rotating file logger. Never shown to the user directly -- surfaced only
via Settings -> Advanced -> Open Logs."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from motiondrive.paths import LOGS_DIR, ensure_dirs

_LOGGER_NAME = "motiondrive"
_configured = False


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    global _configured
    if not _configured:
        _configure_root()
        _configured = True
    return logging.getLogger(name)


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def doRollover(self):
        try:
            super().doRollover()
        except (PermissionError, OSError):
            pass


def _configure_root() -> None:
    ensure_dirs()
    root = logging.getLogger(_LOGGER_NAME)
    root.setLevel(logging.DEBUG)

    file_handler = SafeRotatingFileHandler(
        LOGS_DIR / "motiondrive.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    # Only echo to stdout when actually running from a console (source /
    # dev mode). The frozen GUI build has no console attached.
    if sys.stdout is not None and getattr(sys, "frozen", False) is False:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        root.addHandler(stream_handler)

    # Route uncaught exceptions to the log instead of a console traceback.
    def _excepthook(exc_type, exc_value, exc_tb):
        root.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook
