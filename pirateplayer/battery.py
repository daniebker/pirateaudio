"""Battery monitoring for common Pi Zero UPS boards."""

import logging
import socket
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class BatteryStatus:
    percent: float
    external_power: bool


class WaveshareUPS:
    """Waveshare UPS HAT (C) for Pi Zero: an INA219 at I2C address 0x43."""

    _CONFIG = 0x3EEF       # 32V range, /8 gain, 12-bit x32 averaging, continuous
    _CALIBRATION = 4096    # 0.1 mA per LSB with the board's 0.1 ohm shunt

    def __init__(self, bus: int = 1, address: int = 0x43):
        from smbus2 import SMBus

        self._bus = SMBus(bus)
        self._addr = address
        self._write(0x05, self._CALIBRATION)
        self._write(0x00, self._CONFIG)
        # Voltage sags under load and jumps around; smooth over the last few readings.
        self._voltages = deque(maxlen=5)

    def _read(self, reg: int) -> int:
        hi, lo = self._bus.read_i2c_block_data(self._addr, reg, 2)
        return (hi << 8) | lo

    def _write(self, reg: int, value: int):
        self._bus.write_i2c_block_data(self._addr, reg, [(value >> 8) & 0xFF, value & 0xFF])

    def read(self) -> BatteryStatus:
        self._write(0x05, self._CALIBRATION)  # the INA219 forgets this after a brown-out
        voltage = (self._read(0x02) >> 3) * 0.004
        current = self._read(0x04)
        if current & 0x8000:
            current -= 0x10000
        self._voltages.append(voltage)
        average = sum(self._voltages) / len(self._voltages)
        percent = min(100.0, max(0.0, (average - 3.0) / 1.2 * 100))
        # Positive current means the battery is being charged.
        return BatteryStatus(percent, external_power=current > 0)


class PiSugar:
    """PiSugar 2/3, via the pisugar-server daemon's TCP interface."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8423):
        self._addr = (host, port)

    def _get(self, key: str) -> str:
        with socket.create_connection(self._addr, timeout=1) as s:
            s.sendall(f"get {key}\n".encode())
            reply = s.recv(256).decode()
        return reply.split(":", 1)[1].strip()

    def read(self) -> BatteryStatus:
        return BatteryStatus(
            percent=float(self._get("battery")),
            external_power=self._get("battery_power_plugged") == "true",
        )


def open_battery(kind: str):
    """Return a battery monitor with a .read() method, or None if there isn't one."""
    candidates = {"waveshare": [WaveshareUPS], "pisugar": [PiSugar],
                  "auto": [WaveshareUPS, PiSugar], "none": []}.get(kind, [])
    for cls in candidates:
        try:
            monitor = cls()
            monitor.read()
            log.info("Battery monitor: %s", cls.__name__)
            return monitor
        except Exception as e:
            log.debug("No %s: %s", cls.__name__, e)
    if kind not in ("auto", "none"):
        log.warning("Battery monitor %r not found", kind)
    return None
