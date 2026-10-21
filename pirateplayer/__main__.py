import argparse
import logging
import signal
import sys
from pathlib import Path

from . import config
from .app import App, open_library
from .battery import open_battery
from .bluetooth import open_bluetooth
from .player import Player
from .ui import Renderer


def main():
    parser = argparse.ArgumentParser(description="Music player for the Pimoroni Pirate Audio")
    parser.add_argument("--config", type=Path, default=config.CONFIG_PATH)
    parser.add_argument("--music", type=Path, help="music folder (overrides the config file)")
    parser.add_argument("--sim", action="store_true",
                        help="run on a desktop in a window that simulates the hardware")
    parser.add_argument("--scale", type=int, default=2, help="simulator screen zoom (default 2)")
    parser.add_argument("--mpv", action="store_true",
                        help="simulator: play through mpv like the Pi does, instead of pygame")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = config.load(args.config)
    if args.music:
        cfg.music_dir = args.music.expanduser().resolve()

    if args.sim:
        from . import simulator

        simulator.run(cfg, scale=args.scale, use_mpv=args.mpv)
        return

    from .buttons import GpioButtons
    from .display import LcdDisplay

    display = LcdDisplay(cfg.rotation)
    display.show(Renderer().message("Starting…"))  # mpv takes a while to start on a Pi Zero
    buttons = GpioButtons(cfg.y_pin)
    battery = open_battery(cfg.battery)

    # Turn SIGTERM (systemctl stop, shutdown) into a normal exit so state gets saved.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    library = open_library(cfg)  # syncs while mpv starts
    player = Player(cfg.audio_device)
    bluetooth = open_bluetooth() if cfg.bluetooth else None
    app = App(cfg, display, buttons, battery, player, bluetooth=bluetooth, library=library)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        player.close()
        if bluetooth:
            bluetooth.close()
        display.set_backlight(False)


if __name__ == "__main__":
    main()
