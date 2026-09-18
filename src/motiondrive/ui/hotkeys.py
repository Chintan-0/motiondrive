"""System-wide hotkeys (work even while a game has focus) via the real
Win32 RegisterHotKey mechanism -- Qt shortcuts only fire while the app is
focused, which doesn't satisfy "stop/pause the controller from inside a
game". No extra dependency: ctypes + a QAbstractNativeEventFilter."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from motiondrive.logging_ import get_logger

log = get_logger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

_VK_F = {f"F{i}": 0x70 + (i - 1) for i in range(1, 13)}  # F1..F12


def key_name_to_vk(name: str):
    return _VK_F.get(name.upper())


def parse_hotkey_spec(spec: str) -> tuple[int, int] | None:
    """Parses hotkey strings like 'F7', 'Ctrl+Shift+M', 'Alt+F4' into (modifiers, vk)."""
    parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
    if not parts:
        return None
    mods = MOD_NOREPEAT
    key = parts[-1]
    for m in parts[:-1]:
        if m in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif m in ("SHIFT",):
            mods |= MOD_SHIFT
        elif m in ("ALT",):
            mods |= MOD_ALT

    if key.startswith("F") and key[1:].isdigit():
        f_num = int(key[1:])
        if 1 <= f_num <= 12:
            return mods, 0x70 + (f_num - 1)
    if len(key) == 1 and 'A' <= key <= 'Z':
        return mods, ord(key)
    
    vk = key_name_to_vk(key)
    if vk is not None:
        return mods, vk
    return None


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, manager: "GlobalHotkeyManager"):
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._manager._on_hotkey(msg.wParam)
        return False, 0


class GlobalHotkeyManager(QObject):
    """Register named hotkeys against the main window's HWND; emits
    `triggered(id_name)` when one fires, regardless of which app has focus."""

    triggered = Signal(str)

    def __init__(self, hwnd: int, parent=None):
        super().__init__(parent)
        self._hwnd = hwnd
        self._registered: dict[int, str] = {}
        self._next_id = 1
        self._filter = _NativeFilter(self)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    def register(self, id_name: str, key_name: str) -> bool:
        parsed = parse_hotkey_spec(key_name)
        if parsed is None:
            log.warning("Unsupported hotkey spec %r for %s", key_name, id_name)
            return False
        mods, vk = parsed
        hotkey_id = self._next_id
        self._next_id += 1
        ok = ctypes.windll.user32.RegisterHotKey(self._hwnd, hotkey_id, mods, vk)
        if not ok:
            log.warning("Could not register global hotkey %s (%s) -- may be in use by another app", key_name, id_name)
            return False
        self._registered[hotkey_id] = id_name
        log.info("Registered global hotkey %s -> %s", key_name, id_name)
        return True

    def unregister_all(self) -> None:
        for hotkey_id in list(self._registered):
            ctypes.windll.user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._registered.clear()

    def _on_hotkey(self, hotkey_id: int) -> None:
        id_name = self._registered.get(hotkey_id)
        if id_name:
            self.triggered.emit(id_name)
