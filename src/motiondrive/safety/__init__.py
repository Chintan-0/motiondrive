"""Single choke point for releasing game input. Every path that can end a
driving session (hands lost, camera error, crash, ESC, STOP button) must go
through SafetyWatchdog.release_all() -- never call an input backend directly
from anywhere else once a session is running."""
from __future__ import annotations

from typing import Callable, Optional

from motiondrive.input import InputBackend, NullBackend
from motiondrive.logging_ import get_logger

log = get_logger(__name__)


class SafetyWatchdog:
    def __init__(self):
        self._backend: InputBackend = NullBackend()
        self._active = False
        self._on_release: Optional[Callable[[str], None]] = None

    def arm(self, backend: InputBackend) -> None:
        self._backend = backend
        self._active = True
        log.info("Safety watchdog armed with backend=%s", backend.name)

    def set_release_callback(self, cb: Callable[[str], None]) -> None:
        """cb(reason) is called on the UI thread whenever inputs are released,
        so the dashboard can update its status indicator."""
        self._on_release = cb

    @property
    def active(self) -> bool:
        return self._active

    def apply(self, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False) -> None:
        self.apply_player(1, steering, throttle, brake, shift)

    def apply_player(self, player_id: int, steering: float, throttle: float = 0.0, brake: float = 0.0, shift: bool = False) -> None:
        if not self._active:
            return
        try:
            self._backend.apply(steering, throttle, brake, shift, player_id=player_id)
        except Exception:
            log.exception("Input backend raised during apply_player(%d); releasing for safety", player_id)
            self.release_all(f"Input device error (Player {player_id})")

    def release_player(self, player_id: int, reason: str) -> None:
        try:
            self._backend.release_player(player_id)
            log.info("Released Player %d inputs: %s", player_id, reason)
        except Exception:
            log.exception("Input backend raised during release_player(%d) (ignored)", player_id)

    def release_all(self, reason: str) -> None:
        try:
            self._backend.release()
        except Exception:
            log.exception("Input backend raised during release() (ignored)")
        was_active = self._active
        self._active = False
        self._backend = NullBackend()
        if was_active:
            log.warning("Controller stopped: %s", reason)
        if self._on_release:
            try:
                self._on_release(reason)
            except Exception:
                log.exception("release callback raised (ignored)")
