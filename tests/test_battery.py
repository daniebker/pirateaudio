import socketserver
import sys
import threading
import types

import pytest

from pirateplayer import battery
from pirateplayer.battery import BatteryStatus, PiSugar, WaveshareUPS, open_battery


class FakeSMBus:
    def __init__(self, bus):
        self.registers = {0x02: 0, 0x04: 0}
        self.writes = []

    def read_i2c_block_data(self, addr, reg, length):
        value = self.registers[reg]
        return [(value >> 8) & 0xFF, value & 0xFF]

    def write_i2c_block_data(self, addr, reg, data):
        self.writes.append((reg, data))


@pytest.fixture
def ups(monkeypatch):
    monkeypatch.setitem(sys.modules, "smbus2", types.SimpleNamespace(SMBus=FakeSMBus))
    return WaveshareUPS()


def set_reading(ups, volts, current):
    ups._bus.registers[0x02] = int(round(volts / 0.004)) << 3
    ups._bus.registers[0x04] = current & 0xFFFF


def test_waveshare_writes_calibration(ups):
    assert ups._bus.writes[:2] == [(0x05, [0x10, 0x00]), (0x00, [0x3E, 0xEF])]


def test_waveshare_percent_and_charging(ups):
    set_reading(ups, 3.6, 120)
    status = ups.read()
    assert status.percent == pytest.approx(50, abs=0.5)
    assert status.external_power


def test_waveshare_discharging_and_clamped(ups):
    set_reading(ups, 4.4, -300)
    status = ups.read()
    assert status.percent == 100
    assert not status.external_power


def test_waveshare_smooths_voltage(ups):
    set_reading(ups, 4.2, -1)
    ups.read()
    set_reading(ups, 3.0, -1)
    assert ups.read().percent == pytest.approx(50, abs=0.5)


class PiSugarHandler(socketserver.StreamRequestHandler):
    replies = {"battery": "battery: 87.5", "battery_power_plugged": "battery_power_plugged: true"}

    def handle(self):
        key = self.rfile.readline().decode().split()[1]
        self.wfile.write((self.replies[key] + "\n").encode())


def test_pisugar():
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), PiSugarHandler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            status = PiSugar(port=server.server_address[1]).read()
        finally:
            server.shutdown()
    assert status == BatteryStatus(87.5, external_power=True)


class Good:
    def read(self):
        return BatteryStatus(50, False)


class Broken:
    def __init__(self):
        raise OSError("no device")


def test_open_battery_auto_falls_through(monkeypatch):
    monkeypatch.setattr(battery, "WaveshareUPS", Broken)
    monkeypatch.setattr(battery, "PiSugar", Good)
    assert isinstance(open_battery("auto"), Good)


def test_open_battery_none_found(monkeypatch):
    monkeypatch.setattr(battery, "WaveshareUPS", Broken)
    monkeypatch.setattr(battery, "PiSugar", Broken)
    assert open_battery("auto") is None
    assert open_battery("pisugar") is None


def test_open_battery_disabled_or_unknown():
    assert open_battery("none") is None
    assert open_battery("something else") is None
