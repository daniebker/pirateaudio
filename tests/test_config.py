from pathlib import Path

from pirateplayer.config import Config, load


def test_missing_file_gives_defaults(tmp_path):
    cfg = load(tmp_path / "nope.toml")
    assert cfg == Config()
    assert cfg.volume == 50
    assert cfg.battery == "auto"


def test_overrides_and_expands_paths(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('music_dir = "~/Tunes"\nvolume = 70\nbattery = "none"\nunknown = 1\n')
    cfg = load(path)
    assert cfg.music_dir == Path.home() / "Tunes"
    assert cfg.volume == 70
    assert cfg.battery == "none"
    assert cfg.y_pin == 24  # untouched default
    assert not hasattr(cfg, "unknown")
