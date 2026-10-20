"""The four Pirate Audio buttons, turned into tap / hold / repeat events."""

import threading
import time

HOLD_TIME = 0.6     # seconds before a press counts as a hold
REPEAT_TIME = 0.15  # interval between repeat events while held
COMBO_TIME = 3.0    # hold A+B this long to power off

TAP, HOLD, REPEAT = "tap", "hold", "repeat"
COMBO = "AB"


class PressTracker:
    """Converts sampled pressed/released states into (button, kind) events."""

    def __init__(self):
        self._down = {}  # name -> [pressed_at, last_event_at, held]
        self._suppressed = set()
        self._combo_since = None
        self._combo_fired = False

    def update(self, pressed: dict[str, bool], now: float) -> list[tuple[str, str]]:
        events = []
        if pressed.get("A") and pressed.get("B"):
            self._suppressed |= {"A", "B"}
            if self._combo_since is None:
                self._combo_since = now
            elif not self._combo_fired and now - self._combo_since >= COMBO_TIME:
                events.append((COMBO, HOLD))
                self._combo_fired = True
        else:
            self._combo_since = None
            self._combo_fired = False

        for name, down in pressed.items():
            st = self._down.get(name)
            if down:
                if st is None:
                    self._down[name] = [now, now, False]
                elif name not in self._suppressed:
                    if not st[2] and now - st[0] >= HOLD_TIME:
                        events.append((name, HOLD))
                        st[1:] = [now, True]
                    elif st[2] and now - st[1] >= REPEAT_TIME:
                        events.append((name, REPEAT))
                        st[1] = now
            elif st is not None:
                del self._down[name]
                if name in self._suppressed:
                    self._suppressed.discard(name)
                elif not st[2]:
                    events.append((name, TAP))
        return events


class GpioButtons:
    def __init__(self, y_pin: int = 24):
        from gpiozero import Button

        pins = {"A": 5, "B": 6, "X": 16, "Y": y_pin}
        self._buttons = {name: Button(pin, pull_up=True) for name, pin in pins.items()}
        # Latch presses so a quick tap isn't lost while the main loop is busy drawing.
        self._latched = set()
        self._changed = threading.Event()
        for name, button in self._buttons.items():
            button.when_pressed = lambda _b=None, n=name: self._press(n)
            button.when_released = lambda _b=None: self._changed.set()
        self._tracker = PressTracker()

    def _press(self, name: str):
        self._latched.add(name)
        self._changed.set()

    def wait(self, timeout: float):
        """Sleep until a button changes, or for timeout seconds."""
        self._changed.wait(timeout)
        self._changed.clear()

    def poll(self) -> list[tuple[str, str]]:
        pressed = {}
        for name, button in self._buttons.items():
            pressed[name] = button.is_pressed or name in self._latched
            self._latched.discard(name)
        return self._tracker.update(pressed, time.monotonic())

