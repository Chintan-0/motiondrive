"""Raw Win32 SendInput wrapper, used only for the Advanced "virtual-key"
injection mode (scan-code mode is pydirectinput's already-correct, already-
tested default -- see input/__init__.py). Exists so MotionDrive can honestly
report the *actual* SendInput() return value (the real number of events the
OS accepted) rather than assuming success from "no Python exception was
raised" -- that distinction is the whole point of this diagnostics pass."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# US-layout virtual-key codes for the letters this app ever needs to send,
# plus arrows/space -- deliberately small, not a full keyboard map.
VK_CODES = {
    "a": 0x41, "d": 0x44, "s": 0x53, "w": 0x57, "space": 0x20,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


def send_virtual_key_detailed(key: str, key_down: bool) -> tuple[bool, int, int]:
    """Sends one key event via SendInput using a virtual-key code.
    Returns tuple: (success: bool, count: int, win32_error_code: int)."""
    vk = VK_CODES.get(key.lower())
    if vk is None:
        return False, 0, 87  # ERROR_INVALID_PARAMETER
    extra = ctypes.c_ulong(0)
    flags = 0 if key_down else KEYEVENTF_KEYUP
    ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0,
                      dwExtraInfo=ctypes.pointer(extra))
    inp = _INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=ki))
    inserted = ctypes.windll.user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    err = ctypes.GetLastError() if inserted == 0 else 0
    return inserted == 1, inserted, err


def send_virtual_key(key: str, key_down: bool) -> bool:
    """Backwards-compatible wrapper returning bool success."""
    ok, _, _ = send_virtual_key_detailed(key, key_down)
    return ok

