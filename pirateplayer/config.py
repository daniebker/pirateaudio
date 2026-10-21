"""Settings, optionally overridden by ~/.config/pirateplayer/config.toml."""

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "pirateplayer" / "config.toml"


@dataclass
class Config:
    music_dir: Path = field(default_factory=lambda: Path.home() / "Music")
    state_file: Path = field(
        default_factory=lambda: Path.home() / ".local" / "state" / "pirateplayer" / "state.json"
    )
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "pirateplayer")
    volume: int = 50                 # starting volume (percent) if no saved state
    bluetooth_volume: int = 50       # the same for Bluetooth headphones, which keep their own
    backlight_timeout: float = 30.0  # seconds of inactivity before the screen turns off; 0 = never
    rotation: int = 90               # display rotation; 90 suits the Pirate Audio layout
    y_pin: int = 24                  # early Pirate Audio boards wired button Y to GPIO 20
    battery: str = "auto"            # auto | waveshare | pisugar | none
    low_battery_percent: float = 5.0 # power off below this when not on external power
    audio_device: str = ""           # mpv --audio-device; empty = default
    bluetooth: bool = True           # offer Bluetooth headsets, if the Pi has Bluetooth


def load(path: Path = CONFIG_PATH) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg
    data = tomllib.loads(path.read_text())
    for f in fields(Config):
        if f.name not in data:
            continue
        value = data[f.name]
        if isinstance(getattr(cfg, f.name), Path):
            value = Path(value).expanduser()
        setattr(cfg, f.name, value)
    return cfg
