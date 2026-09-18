"""Tests for motiondrive.startup (Windows 'start with Windows' registry
integration) -- uses a fake in-memory winreg module so these never touch the
real Windows registry, same technique tests/test_hand_tracking.py uses for
mediapipe."""
import sys
import types

import pytest


class _FakeWinReg:
    """Minimal in-memory stand-in for the winreg module's Run-key surface."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values: dict[str, str] = {}
        self.fail_open = False

    def OpenKey(self, _hive, _path, _reserved, _access):
        if self.fail_open:
            raise OSError("registry unavailable")
        return _FakeKeyCtx(self)

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return (self.values[name], self.REG_SZ)

    def SetValueEx(self, _key, name, _reserved, _type, value):
        self.values[name] = value

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class _FakeKeyCtx:
    def __init__(self, winreg_mod):
        self._winreg = winreg_mod

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_winreg():
    fake = _FakeWinReg()
    module = types.ModuleType("winreg")
    module.HKEY_CURRENT_USER = fake.HKEY_CURRENT_USER
    module.KEY_READ = fake.KEY_READ
    module.KEY_SET_VALUE = fake.KEY_SET_VALUE
    module.REG_SZ = fake.REG_SZ
    module.OpenKey = fake.OpenKey
    module.QueryValueEx = fake.QueryValueEx
    module.SetValueEx = fake.SetValueEx
    module.DeleteValue = fake.DeleteValue
    sys.modules["winreg"] = module
    return fake


def _reload_startup():
    sys.modules.pop("motiondrive.startup", None)
    import motiondrive.startup as startup
    return startup


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    sys.modules.pop("winreg", None)
    sys.modules.pop("motiondrive.startup", None)


def test_is_enabled_false_when_never_registered(monkeypatch):
    _install_fake_winreg()
    monkeypatch.setattr(sys, "platform", "win32")
    startup = _reload_startup()
    assert startup.is_enabled() is False


def test_enable_then_is_enabled_true(monkeypatch):
    _install_fake_winreg()
    monkeypatch.setattr(sys, "platform", "win32")
    startup = _reload_startup()
    assert startup.enable() is True
    assert startup.is_enabled() is True


def test_disable_removes_registration(monkeypatch):
    fake = _install_fake_winreg()
    monkeypatch.setattr(sys, "platform", "win32")
    startup = _reload_startup()
    startup.enable()
    assert startup.disable() is True
    assert startup.is_enabled() is False


def test_disable_is_a_noop_when_never_registered(monkeypatch):
    _install_fake_winreg()
    monkeypatch.setattr(sys, "platform", "win32")
    startup = _reload_startup()
    assert startup.disable() is True  # must not raise or report failure


def test_set_enabled_dispatches_to_enable_and_disable(monkeypatch):
    _install_fake_winreg()
    monkeypatch.setattr(sys, "platform", "win32")
    startup = _reload_startup()
    assert startup.set_enabled(True) is True
    assert startup.is_enabled() is True
    assert startup.set_enabled(False) is True
    assert startup.is_enabled() is False


def test_registry_failure_reports_false_not_exception(monkeypatch):
    fake = _install_fake_winreg()
    fake.fail_open = True
    monkeypatch.setattr(sys, "platform", "win32")
    startup = _reload_startup()
    assert startup.enable() is False
    assert startup.is_enabled() is False


def test_non_windows_platform_is_always_disabled_and_inert(monkeypatch):
    _install_fake_winreg()
    monkeypatch.setattr(sys, "platform", "linux")
    startup = _reload_startup()
    assert startup.is_enabled() is False
    assert startup.enable() is False
    assert startup.disable() is False
