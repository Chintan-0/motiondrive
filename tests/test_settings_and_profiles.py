from motiondrive.settings import Settings, SettingsStore
from motiondrive.profiles import Profile, ProfileStore


def test_settings_clamp_bounds():
    s = Settings(sensitivity=999, smoothing=-5, steering_range=200, dead_zone=99)
    s.clamp()
    assert s.sensitivity == 100
    assert s.smoothing == 0
    assert s.steering_range == 100
    assert s.dead_zone == 20


def test_settings_store_roundtrip(tmp_path):
    store = SettingsStore(path=tmp_path / "settings.json")
    s = Settings(sensitivity=70, smoothing=30, input_mode="keyboard")
    store.save(s)
    loaded = store.load()
    assert loaded.sensitivity == 70
    assert loaded.smoothing == 30
    assert loaded.input_mode == "keyboard"


def test_settings_store_creates_recommended_defaults_if_missing(tmp_path):
    store = SettingsStore(path=tmp_path / "settings.json")
    loaded = store.load()
    assert loaded.sensitivity == Settings.recommended().sensitivity
    assert (tmp_path / "settings.json").exists()


def test_profile_slug():
    p = Profile(name="Assetto Corsa!!")
    assert p.slug() == "assetto-corsa"


def test_profile_store_roundtrip(tmp_path):
    store = ProfileStore(directory=tmp_path)
    p = Profile(name="Forza", icon="F", sensitivity=80)
    store.save(p)
    profiles = store.list_profiles()
    names = [x.name for x in profiles]
    assert "Forza" in names
    fetched = store.get("Forza")
    assert fetched.sensitivity == 80


def test_profile_store_creates_default_if_empty(tmp_path):
    store = ProfileStore(directory=tmp_path)
    profiles = store.list_profiles()
    assert len(profiles) >= 4
    names = [p.name for p in profiles]
    assert "MR RACER - Poki" in names
    assert "Racing Limits - CrazyGames" in names

    mr_racer = store.get("MR RACER - Poki")
    assert mr_racer.input_mode == "keyboard"
    assert (mr_racer.key_left, mr_racer.key_right, mr_racer.key_accelerate, mr_racer.key_brake) == ("a", "d", "w", "s")

    racing_limits = store.get("Racing Limits - CrazyGames")
    assert racing_limits.input_mode == "keyboard"
    assert (racing_limits.key_left, racing_limits.key_right, racing_limits.key_accelerate, racing_limits.key_brake) == ("left", "right", "up", "down")


def test_profile_delete(tmp_path):
    store = ProfileStore(directory=tmp_path)
    p = Profile(name="ToDelete")
    store.save(p)
    assert store.get("ToDelete") is not None
    store.delete(p)
    assert store.get("ToDelete") is None


def test_apply_browser_profiles_to_engine():
    from motiondrive.engine import MotionDriveEngine
    engine = MotionDriveEngine()

    mr_racer = Profile(name="MR RACER - Poki", input_mode="keyboard", key_left="a", key_right="d", key_accelerate="w", key_brake="s")
    engine.apply_profile(mr_racer)
    assert engine.settings.input_mode == "keyboard"
    assert engine._key_map() == {"left": "a", "right": "d", "accelerate": "w", "brake": "s"}

    racing_limits = Profile(name="Racing Limits - CrazyGames", input_mode="keyboard", key_left="left", key_right="right", key_accelerate="up", key_brake="down")
    engine.apply_profile(racing_limits)
    assert engine.settings.input_mode == "keyboard"
    assert engine._key_map() == {"left": "left", "right": "right", "accelerate": "up", "brake": "down"}

