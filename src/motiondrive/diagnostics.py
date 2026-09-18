"""Foreground-window detection + an in-memory event log for the Input
Diagnostics screen. No Qt import here -- same "pure logic, testable without
a display" pattern as steering/pedals/controller_state."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class ForegroundWindowInfo:
    process_name: str
    window_title: str
    pid: int
    is_motiondrive: bool
    hwnd: int = 0


def get_foreground_window_info() -> Optional[ForegroundWindowInfo]:
    """Live OS query -- who currently has keyboard focus. Returns None on
    non-Windows or if the query fails for any reason (never raises).

    This can only ever answer "which window/process is foreground" -- it
    cannot know whether a specific DOM element inside a browser page has
    focus (that requires browser integration MotionDrive doesn't have).
    Callers must not conflate "Chrome is running" with "Chrome is
    foreground" with "the page's input field has focus" -- these are three
    different, decreasingly-verifiable facts."""
    try:
        import win32gui
        import win32process
        import win32api
        import win32con
        import os

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = "unknown"
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            try:
                full_path = win32process.GetModuleFileNameEx(handle, 0)
                process_name = os.path.basename(full_path)
            finally:
                win32api.CloseHandle(handle)
        except Exception:
            pass
        return ForegroundWindowInfo(
            process_name=process_name,
            window_title=title,
            pid=pid,
            is_motiondrive=process_name.lower() == "motiondrive.exe",
            hwnd=int(hwnd),
        )
    except Exception:
        return None


class EventLog:
    """Ring buffer of (timestamp_str, message) for the diagnostics event
    log -- capped so it can never grow unbounded (spec: ~200-500 events)."""

    def __init__(self, maxlen: int = 500):
        self._events: deque = deque(maxlen=maxlen)

    def add(self, message: str) -> tuple:
        entry = (time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}", message)
        self._events.append(entry)
        return entry

    def snapshot(self) -> list:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)
