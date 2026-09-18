"""Per-game profiles: each stores its own input mode, sensitivity, smoothing,
dead zone, steering range, and key mappings. Persisted as one JSON file per profile."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from motiondrive.logging_ import get_logger
from motiondrive.paths import PROFILES_DIR, ensure_dirs

log = get_logger(__name__)

DEFAULT_PROFILE_ICON = "\U0001F3AE"  # 🎮

# Trimmed to exactly the presets the simplified Settings UI exposes --
# desktop-game (gamepad-only) templates like Assetto Corsa/GTA V/Forza/
# BeamNG were removed since "CUSTOM" already covers anyone driving a real
# installed game, and the built-in list is meant to be the small, obvious
# set for browser driving. Each browser preset gets a real default
# game_url (Slow Roads, a free browser driving game) so START & PLAY has
# somewhere sensible to open for a first-time user -- CUSTOM intentionally
# has none, since it's meant to be pointed at the user's own game.
_SLOWROADS_URL = "https://slowroads.io/"

BUILTIN_TEMPLATES = [
    ("MR RACER - Poki", "\U0001F3CE️", "keyboard", "a", "d", "w", "s", _SLOWROADS_URL),
    ("Racing Limits - CrazyGames", "\U0001F3C1", "keyboard", "left", "right", "up", "down", _SLOWROADS_URL),
    ("Generic WASD Racing", "\U0001F3AE", "keyboard", "a", "d", "w", "s", _SLOWROADS_URL),
    ("Generic Arrow Racing", "\U0001F697", "keyboard", "left", "right", "up", "down", _SLOWROADS_URL),
    ("Custom", "\U0001F527", "keyboard", "a", "d", "w", "s", ""),
]

# A pure in-memory name -> URL lookup, always up to date with the current
# code regardless of what's actually saved on disk. ProfileStore only
# auto-creates BUILTIN_TEMPLATES when its directory is completely empty --
# an existing install with profile JSON files saved before game_url
# existed keeps loading those old files forever (list_profiles() only
# pulls dataclass fields that are actually present in the file), so a
# genuine built-in preset's stored Profile object can have game_url stuck
# at "" even though the code's own template says otherwise. Call sites
# that need "this preset's real URL" should prefer this dict over trusting
# a loaded Profile's own game_url blindly.
BUILTIN_GAME_URLS = {name: url for (name, _icon, _mode, _l, _r, _a, _b, url) in BUILTIN_TEMPLATES}


@dataclass
class Profile:
    name: str = "Default"
    icon: str = DEFAULT_PROFILE_ICON
    input_mode: str = "gamepad"
    sensitivity: int = 55
    smoothing: int = 45
    steering_range: int = 60
    dead_zone: int = 8

    throttle_sensitivity: int = 55
    brake_sensitivity: int = 55
    gesture_threshold: int = 35
    pedal_smoothing: int = 40
    accelerate_gesture: str = "pinch"
    brake_gesture: str = "pinch"

    key_left: str = "a"
    key_right: str = "d"
    key_accelerate: str = "w"
    key_brake: str = "s"

    # Browser/game launch target for START & PLAY. Per-preset, not a global
    # hardcode -- a built-in browser-driving preset can point at its game's
    # URL (e.g. Slow Roads for the generic presets); CUSTOM leaves this
    # blank so the user's own configured game_exe_path (Settings) is used
    # instead. Opened via the user's default browser, never embedded.
    game_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return s or "profile"


class ProfileStore:
    def __init__(self, directory: Optional[Path] = None):
        self.directory = directory or PROFILES_DIR

    def _file_for(self, slug: str) -> Path:
        return self.directory / f"{slug}.json"

    def list_profiles(self) -> list[Profile]:
        ensure_dirs()
        profiles = []
        for f in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                known = {k: data[k] for k in Profile.__dataclass_fields__ if k in data}
                profiles.append(Profile(**known))
            except Exception:
                log.exception("Failed to load profile file %s", f)
        if not profiles:
            # Generate default built-in profiles on first run
            for name, icon, mode, left, right, acc, brk, url in BUILTIN_TEMPLATES:
                p = Profile(
                    name=name, icon=icon, input_mode=mode,
                    key_left=left, key_right=right, key_accelerate=acc, key_brake=brk,
                    game_url=url,
                )
                self.save(p)
                profiles.append(p)
        return profiles

    def save(self, profile: Profile) -> None:
        ensure_dirs()
        path = self._file_for(profile.slug())
        path.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
        log.info("Profile saved: %s", profile.name)

    def delete(self, profile: Profile) -> None:
        path = self._file_for(profile.slug())
        if path.exists():
            path.unlink()
            log.info("Profile deleted: %s", profile.name)

    def get(self, name: str) -> Optional[Profile]:
        for p in self.list_profiles():
            if p.name == name:
                return p
        return None

