"""Shared Drive Feel preset definitions used by both the Controller and
Settings pages, so the two can never drift out of sync with each other --
per the "do not duplicate settings state inside the UI" rule."""
from __future__ import annotations

from motiondrive.settings import Settings

# Each preset sets several real SteeringEngine-backed Settings fields at
# once. BALANCED matches Settings.RECOMMENDED's sensitivity/smoothing/
# dead_zone (the numeric out-of-the-box feel).
DRIVE_FEEL_PRESETS = {
    "CALM": dict(icon="🍃", caption="Smooth & stable",
                  sensitivity=35, smoothing=70, dead_zone=10, steering_preset="smooth"),
    "BALANCED": dict(icon="🎯", caption="Recommended",
                       sensitivity=55, smoothing=45, dead_zone=5, steering_preset="balanced"),
    "DIRECT": dict(icon="⚡", caption="Fast & responsive",
                    sensitivity=75, smoothing=25, dead_zone=3, steering_preset="responsive"),
}


def matches_preset(settings: Settings, preset: dict) -> bool:
    return (settings.sensitivity == preset["sensitivity"] and
            settings.smoothing == preset["smoothing"] and
            settings.dead_zone == preset["dead_zone"] and
            settings.steering_preset == preset["steering_preset"])


def active_preset_name(settings: Settings) -> str | None:
    for name, preset in DRIVE_FEEL_PRESETS.items():
        if matches_preset(settings, preset):
            return name
    return None
