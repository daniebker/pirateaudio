"""Run the player on a desktop: a pygame window stands in for the Pirate Audio hardware.

The screen, buttons, UPS battery, Bluetooth and audio are simulated; everything else is the
real app.
"""

import logging
import os
import queue
import time
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame
from PIL import Image

from .app import BLUETOOTH, BROWSER, MENU, PLAYLISTS, SETTINGS, App
from .battery import BatteryStatus
from .bluetooth import CONNECTING, DISCONNECTING, PAIRING, BtDevice, sort_devices
from .buttons import PressTracker
from .config import Config
from .display import SIZE
from .player import PAUSED, PLAYING, STOPPED, Player

log = logging.getLogger(__name__)

SIDE = 100   # room either side of the screen for the buttons
TOP = 40
STATUS = 58  # help/status bar under the board
FPS = 30

KEYS = {
    pygame.K_a: "A", pygame.K_UP: "A",
    pygame.K_b: "B", pygame.K_DOWN: "B",
    pygame.K_x: "X", pygame.K_RETURN: "X", pygame.K_RIGHT: "X",
    pygame.K_y: "Y", pygame.K_ESCAPE: "Y", pygame.K_LEFT: "Y", pygame.K_BACKSPACE: "Y",
}
HINTS = {
    MENU: {"A": "Up", "B": "Down", "X": "Open", "Y": "Playing"},
    BROWSER: {"A": "Up", "B": "Down", "X": "Open", "Y": "Back"},
    "now_playing": {"A": "Vol +", "B": "Vol -", "X": "Play/Pause", "Y": "Browse"},
    BLUETOOTH: {"A": "Up", "B": "Down", "X": "Select", "Y": "Back"},
    PLAYLISTS: {"A": "Up", "B": "Down", "X": "Play", "Y": "Back"},
    SETTINGS: {"A": "Up", "B": "Down", "X": "Select", "Y": "Back"},
}

WINDOW_BG = (44, 46, 54)
BOARD = (14, 14, 16)
BUTTON = (205, 205, 210)
BUTTON_DOWN = (255, 168, 38)
TEXT = (225, 225, 230)
DIM = (140, 142, 155)


class PygamePlayer:
    """Plays through pygame's mixer, so the simulator doesn't need mpv."""

    def __init__(self):
        try:
            pygame.mixer.init()
        except pygame.error as e:
            log.warning("No audio device (%s); playing silently", e)
            os.environ["SDL_AUDIODRIVER"] = "dummy"
            pygame.mixer.init()
        self.music = pygame.mixer.music
        self.events: queue.Queue = queue.Queue()
        self.state = STOPPED
        self._offset = 0.0     # track position when play() was last called
        self._volume = 0.5
        self._last_command = 0.0

    @property
    def position(self) -> float:
        if self.state == STOPPED:
            return self._offset
        return self._offset + max(0, self.music.get_pos()) / 1000

    @position.setter
    def position(self, value: float):
        self._offset = value

    def _play_from(self, start: float, paused: bool):
        self.music.play(start=start)
        self.music.set_volume(self._volume)
        if paused:
            self.music.pause()
        self._offset = start
        self._last_command = time.monotonic()
        self.state = PAUSED if paused else PLAYING

    def load(self, path: Path, start: float = 0.0, paused: bool = False):
        self._last_command = time.monotonic()
        try:
            self.music.load(str(path))
            self._play_from(start, paused)
        except pygame.error as e:
            self.state = STOPPED
            self.events.put(("error", str(e)))

    def toggle_pause(self):
        if self.state == PLAYING:
            self.music.pause()
            self.state = PAUSED
        elif self.state == PAUSED:
            self.music.unpause()
            self.state = PLAYING

    def pause(self):
        if self.state == PLAYING:
            self.toggle_pause()

    def stop(self):
        self.music.stop()
        self.state = STOPPED
        self._offset = 0.0

    def seek(self, seconds: float):
        if self.state != STOPPED:
            self._play_from(max(0.0, seconds), self.state == PAUSED)

    def set_volume(self, percent: int):
        self._volume = percent / 100
        self.music.set_volume(self._volume)

    def set_audio_device(self, device: str):
        log.info("Audio output: %s (simulated; still playing here)", device or "default")

    def poll_position(self):
        pass  # position is always current

    def update(self):
        """Called every frame: notice when a track has finished."""
        settled = time.monotonic() - self._last_command > 0.3
        if self.state == PLAYING and settled and not self.music.get_busy():
            self.state = STOPPED
            self._offset = 0.0
            self.events.put(("ended", None))

    def close(self):
        self.music.stop()
        pygame.mixer.quit()


class SimBattery:
    """A UPS whose charge and charger you control from the keyboard."""

    def __init__(self):
        self.percent = 80.0
        self.external_power = False

    def read(self) -> BatteryStatus:
        return BatteryStatus(self.percent, self.external_power)


class SimBluetooth:
    """Pretend headsets: a scan finds them one by one, and pairing and connecting take a moment.

    One is already paired, and one is out of range so connecting to it fails.
    """

    NEARBY = [("00:1A:7D:DA:71:13", "Sony WH-1000XM4", 0.0),
              ("F4:4E:FD:00:12:34", "JBL Flip 5", 2.0),
              ("2C:41:A1:55:0E:7B", "Bose QC Earbuds", 4.0),
              ("40:EF:4C:8A:22:9F", "Old speaker (out of range)", 6.0)]

    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self.scanning = False
        self._scan_started = 0.0
        address, name, _ = self.NEARBY[0]
        self._devices = {address: BtDevice(address, name, paired=True)}
        self._pending: list[tuple[float, object]] = []  # (when, callable)

    def poll(self):
        now = time.monotonic()
        if self.scanning:
            for address, name, after in self.NEARBY:
                if address not in self._devices and now - self._scan_started >= after:
                    self._devices[address] = BtDevice(address, name)
                    self.events.put(("changed", None))
        for item in [p for p in self._pending if p[0] <= now]:
            self._pending.remove(item)
            item[1]()

    def _later(self, seconds: float, action):
        self._pending.append((time.monotonic() + seconds, action))

    def devices(self) -> list[BtDevice]:
        return sort_devices(list(self._devices.values()))

    def start_scan(self):
        self.scanning = True
        self._scan_started = time.monotonic()
        self.events.put(("changed", None))

    def stop_scan(self):
        self.scanning = False
        # Like BlueZ, forget unpaired devices that the scan found.
        self._devices = {a: d for a, d in self._devices.items() if d.paired or d.busy}
        self.events.put(("changed", None))

    def connect(self, address: str, quiet: bool = False):
        device = self._devices.get(address)
        if device is None or device.busy or device.connected:
            return
        device.busy = CONNECTING if device.paired else PAIRING  # before stop_scan drops it
        self.stop_scan()
        for other in self._devices.values():
            if other.connected:
                self.switch_off(other.address)

        def done():
            device.busy = ""
            if "out of range" in device.name:
                if not quiet:
                    self.events.put(("error", f"Couldn't connect to {device.name}:\n"
                                              "not in range or switched off"))
            else:
                device.paired = device.connected = True
                self.events.put(("audio", address))
            self.events.put(("changed", None))
        self._later(1.5, done)
        self.events.put(("changed", None))

    def disconnect(self, address: str):
        device = self._devices.get(address)
        if device is None or device.busy:
            return
        device.busy = DISCONNECTING
        self.events.put(("changed", None))

        def done():
            device.busy = ""
            self.switch_off(address)
        self._later(0.5, done)

    def switch_off(self, address: str | None = None):
        """The headset going away: switched off, or out of range. None = whichever is connected."""
        for device in self._devices.values():
            if device.connected and address in (None, device.address):
                device.connected = False
                self.events.put(("audio", None))
                self.events.put(("changed", None))

    def forget(self, address: str):
        self.switch_off(address)
        self._devices.pop(address, None)
        self.events.put(("changed", None))

    def connected(self) -> str:
        return next((d.name for d in self._devices.values() if d.connected), "")

    def close(self):
        pass


class SimWindow:
    """The board: the screen in the middle and the A/B/X/Y buttons either side.

    Serves as both the app's display and its buttons.
    """

    def __init__(self, scale: int = 2):
        pygame.init()
        self.scale = scale
        self.screen_px = SIZE * scale
        self.width = self.screen_px + 2 * SIDE
        self.board_height = self.screen_px + 2 * TOP
        self.window = pygame.display.set_mode((self.width, self.board_height + STATUS))
        pygame.display.set_caption("Pirate Audio simulator")
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font_path = font_path if os.path.exists(font_path) else None
        self.font = pygame.font.Font(font_path, 13)
        self.label_font = pygame.font.Font(font_path, 20)

        self.buttons = {
            "A": (SIDE // 2, TOP + 70),
            "B": (SIDE // 2, self.board_height - TOP - 70),
            "X": (self.width - SIDE // 2, TOP + 70),
            "Y": (self.width - SIDE // 2, self.board_height - TOP - 70),
        }
        self.radius = 22
        self.held: set[str] = set()     # down right now (keyboard or mouse)
        self.latched: set[str] = set()  # pressed since the last poll, so quick taps aren't lost
        self._mouse_button = None
        self._tracker = PressTracker()

        self._frame = Image.new("RGB", (SIZE, SIZE))
        self._backlight = True
        self.hints = HINTS[MENU]
        self.status = ""

    # -- display interface --

    def show(self, image: Image.Image):
        self._frame = image
        self.draw()

    def set_backlight(self, on: bool):
        self._backlight = on
        self.draw()

    # -- buttons interface --

    def poll(self) -> list[tuple[str, str]]:
        pressed = {name: name in self.held or name in self.latched for name in self.buttons}
        self.latched.clear()
        return self._tracker.update(pressed, time.monotonic())

    def press(self, name: str):
        self.held.add(name)
        self.latched.add(name)

    def release(self, name: str):
        self.held.discard(name)

    def button_at(self, pos) -> str | None:
        for name, (cx, cy) in self.buttons.items():
            if (pos[0] - cx) ** 2 + (pos[1] - cy) ** 2 <= (self.radius + 4) ** 2:
                return name
        return None

    def handle_mouse(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._mouse_button = self.button_at(event.pos)
            if self._mouse_button:
                self.press(self._mouse_button)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._mouse_button:
            self.release(self._mouse_button)
            self._mouse_button = None

    # -- drawing --

    def draw(self):
        win = self.window
        win.fill(WINDOW_BG)
        pygame.draw.rect(win, BOARD, (6, 6, self.width - 12, self.board_height - 12), border_radius=22)

        x, y = SIDE, TOP
        pygame.draw.rect(win, (60, 60, 66), (x - 4, y - 4, self.screen_px + 8, self.screen_px + 8),
                         border_radius=4)
        if self._backlight:
            frombytes = getattr(pygame.image, "frombytes", None) or pygame.image.fromstring
            surface = frombytes(self._frame.tobytes(), self._frame.size, "RGB")
            win.blit(pygame.transform.scale(surface, (self.screen_px, self.screen_px)), (x, y))
        else:
            pygame.draw.rect(win, (0, 0, 0), (x, y, self.screen_px, self.screen_px))

        for name, (cx, cy) in self.buttons.items():
            down = name in self.held
            pygame.draw.circle(win, BUTTON_DOWN if down else BUTTON, (cx, cy), self.radius)
            pygame.draw.circle(win, (90, 90, 96), (cx, cy), self.radius, 2)
            self._text(name, self.label_font, BOARD, (cx, cy))
            self._text(self.hints.get(name, ""), self.font, DIM, (cx, cy + self.radius + 14))

        help_text = ("Keys: Up/A  Down/B  Enter/X  Esc/Y   ·   hold a key for a long press   ·   "
                     "A+B for 3s powers off")
        self._text(help_text, self.font, DIM, (self.width // 2, self.board_height + 16))
        self._text(self.status, self.font, TEXT, (self.width // 2, self.board_height + 38))
        pygame.display.flip()

    def _text(self, text, font, colour, centre):
        if text:
            surface = font.render(text, True, colour)
            self.window.blit(surface, surface.get_rect(center=centre))


def run(cfg: Config, scale: int = 2, use_mpv: bool = False):
    window = SimWindow(scale)
    player = Player(cfg.audio_device) if use_mpv else PygamePlayer()
    battery = SimBattery()
    bluetooth = SimBluetooth() if cfg.bluetooth else None
    app = App(cfg, window, window, battery, player, simulate=True, bluetooth=bluetooth)
    log.info("Music folder: %s", cfg.music_dir)

    clock = pygame.time.Clock()
    app.start()
    try:
        while app.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    app.running = False
                elif event.type in (pygame.KEYDOWN, pygame.KEYUP) and event.key in KEYS:
                    if event.type == pygame.KEYDOWN:
                        window.press(KEYS[event.key])
                    else:
                        window.release(KEYS[event.key])
                elif event.type == pygame.KEYDOWN and event.key in (pygame.K_c, pygame.K_MINUS,
                                                                    pygame.K_EQUALS):
                    if event.key == pygame.K_c:
                        battery.external_power = not battery.external_power
                    else:
                        step = 10 if event.key == pygame.K_EQUALS else -10
                        battery.percent = min(100.0, max(0.0, battery.percent + step))
                    app.check_battery()
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_h and bluetooth:
                    bluetooth.switch_off()
                elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                    window.handle_mouse(event)

            if isinstance(player, PygamePlayer):
                player.update()
            app.tick(time.monotonic())

            window.hints = HINTS[app.screen]
            charger = "charger connected" if battery.external_power else "on battery"
            audio = "mpv" if use_mpv else "pygame"
            if bluetooth and bluetooth.connected():
                audio = f"{bluetooth.connected()} [H] switch off"
            window.status = (f"Battery {battery.percent:.0f}% ({charger})  [C] charger  [-/=] level"
                             f"   ·   audio: {audio}")
            window.draw()
            clock.tick(FPS)
    finally:
        app.save_state()
        app.close()
        player.close()
        pygame.quit()
