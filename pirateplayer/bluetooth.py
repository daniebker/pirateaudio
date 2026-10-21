"""Bluetooth headsets: scanning, pairing and connecting through BlueZ, finding the
bluez-alsa PCM that mpv plays to once a headset is connected, and taking the headset's
play/pause, next and previous buttons (AVRCP), which BlueZ passes to a registered media player.

Everything runs on the main thread: poll() dispatches pending D-Bus messages, and the slow
calls (Pair, Connect) are asynchronous, reporting back through `events`.
"""

import logging
import queue
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"
BLUEALSA = "org.bluealsa"
BLUEALSA_ROOT = "/org/bluealsa"
AGENT_PATH = "/pirateplayer/agent"
PLAYER_PATH = "/pirateplayer/player"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
PCM = "org.bluealsa.PCM1"
MEDIA = "org.bluez.Media1"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"  # what BlueZ calls a registered player through

# The headset buttons, as BlueZ calls the player's methods.
CONTROLS = ("Play", "Pause", "PlayPause", "Stop", "Next", "Previous")

A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
AUDIO_VIDEO_CLASS = 0x04  # major device class, bits 8-12 of the Class property

PAIRING, CONNECTING, DISCONNECTING = "Pairing…", "Connecting…", "Disconnecting…"

# bluez-alsa re-creates a headset's PCM when the stream's codec settings change, which happens
# as soon as mpv opens it. Only a PCM missing for longer than this means the headset has gone.
SINK_GRACE = 3.0

# Device properties that change what the Bluetooth screen shows. RSSI changes constantly
# while scanning and isn't shown, so it doesn't trigger a redraw.
_SHOWN = {"Alias", "Name", "Paired", "Connected", "Class", "Icon", "UUIDs"}

_REASONS = {
    "org.bluez.Error.AuthenticationFailed": "pairing failed",
    "org.bluez.Error.AuthenticationCanceled": "pairing was cancelled",
    "org.bluez.Error.AuthenticationRejected": "pairing was refused",
    "org.bluez.Error.AuthenticationTimeout": "pairing timed out",
    "org.bluez.Error.ConnectionAttemptFailed": "not in range or switched off",
    "org.freedesktop.DBus.Error.NoReply": "no answer",
}


@dataclass
class BtDevice:
    address: str
    name: str
    paired: bool = False
    connected: bool = False
    busy: str = ""  # PAIRING, CONNECTING or DISCONNECTING while a request is running


def audio_device(address: str) -> str:
    """The mpv --audio-device that plays to a connected headset through bluez-alsa."""
    return f"alsa/bluealsa:DEV={address},PROFILE=a2dp"


def address_from_path(path: str) -> str:
    """/org/bluez/hci0/dev_00_1A_7D_DA_71_13 (or a bluez-alsa path under it) -> 00:1A:..."""
    for part in path.split("/"):
        if part.startswith("dev_"):
            return part[4:].replace("_", ":")
    return ""


def is_audio(props: dict) -> bool:
    if A2DP_SINK_UUID in props.get("UUIDs", []):
        return True
    if str(props.get("Icon", "")).startswith("audio"):
        return True
    cls = props.get("Class")
    return cls is not None and (int(cls) >> 8) & 0x1F == AUDIO_VIDEO_CLASS


def sort_devices(devices: list[BtDevice]) -> list[BtDevice]:
    # Not by signal strength: rows would jump around under the cursor while scanning.
    return sorted(devices, key=lambda d: (not d.connected, not d.paired, d.name.lower(), d.address))


def visible_devices(devices: dict[str, dict], busy: dict[str, str]) -> list[BtDevice]:
    """The devices worth listing: paired ones, and audio devices found by a scan.

    `devices` maps BlueZ object paths to Device1 properties; `busy` maps paths to the
    request in progress.
    """
    result = []
    for path, p in devices.items():
        paired = bool(p.get("Paired"))
        # Until its name is known a device is just an address, which means nothing to anyone.
        if not paired and (not is_audio(p) or "Name" not in p):
            continue
        address = str(p.get("Address", address_from_path(path)))
        result.append(BtDevice(address, str(p.get("Alias") or p.get("Name") or address), paired,
                               bool(p.get("Connected")), busy.get(path, "")))
    return sort_devices(result)


def pick_sink(pcms: dict[str, dict]) -> str | None:
    """The address of the headset to play to: the most recently connected A2DP sink."""
    # With the Pi as the A2DP source, the PCM we write to is the "sink" end of it.
    sinks = [p for p in pcms.values() if p.get("Transport") == "A2DP-source" and p.get("Mode") == "sink"]
    if not sinks:
        return None
    newest = max(sinks, key=lambda p: int(p.get("Sequence", 0)))
    return address_from_path(str(newest.get("Device", ""))) or None


def player_status(state: str) -> str:
    """The MPRIS PlaybackStatus for a player state: "playing" -> "Playing", and so on."""
    return state.capitalize()


def player_metadata(title: str, artist: str, album: str, length: float) -> dict:
    """The MPRIS Metadata BlueZ passes on to the headset, which some read out or show."""
    metadata = {"xesam:title": title, "mpris:length": int(length * 1_000_000)}
    if artist:
        metadata["xesam:artist"] = [artist]
    if album:
        metadata["xesam:album"] = album
    return metadata


def _reason(error) -> str:
    name = error.get_dbus_name() or ""
    message = error.get_dbus_message() or ""
    if name in _REASONS:
        return _REASONS[name]
    if "profile-unavailable" in message:
        return "no Bluetooth audio service (is bluealsa running?)"
    if message in ("Page Timeout", "Host is down") or "timeout" in message.lower():
        return "not in range or switched off"
    return message or name.rsplit(".", 1)[-1]


class Bluetooth:
    def __init__(self):
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        self._dbus = dbus
        self._context = GLib.MainContext.default()
        # A connection of our own: the shared one may already exist without a main loop.
        self._bus = dbus.SystemBus(mainloop=DBusGMainLoop(), private=True)

        # Events for the main loop: ("changed", None), ("audio", address or None),
        # ("error", message), ("control", one of CONTROLS) when a headset button is pressed
        self.events: queue.Queue = queue.Queue()
        self.scanning = False
        self._devices: dict[str, dict] = {}  # BlueZ object path -> Device1 properties
        self._busy: dict[str, str] = {}      # object path -> PAIRING / CONNECTING / DISCONNECTING
        self._pcms: dict[str, dict] = {}     # bluez-alsa object path -> PCM1 properties
        self._sink: str | None = None
        self._sink_lost_at: float | None = None  # when the PCM went, if it might come back

        objects = self._bus.get_object(BLUEZ, "/").GetManagedObjects(dbus_interface=OBJECT_MANAGER)
        adapters = sorted(path for path, ifaces in objects.items() if ADAPTER in ifaces)
        if not adapters:
            raise RuntimeError("no Bluetooth adapter")
        self._adapter_path = adapters[0]
        adapter = self._bus.get_object(BLUEZ, self._adapter_path)
        self._adapter = dbus.Interface(adapter, ADAPTER)
        self._adapter_props = dbus.Interface(adapter, PROPERTIES)
        if not objects[self._adapter_path][ADAPTER].get("Powered"):
            # Fails if the radio is blocked: run `sudo rfkill unblock bluetooth`.
            self._adapter_props.Set(ADAPTER, "Powered", True)
        self.scanning = bool(objects[self._adapter_path][ADAPTER].get("Discovering"))
        # Headsets use classic Bluetooth; leaving out LE hides a crowd of beacons and gadgets.
        self._adapter.SetDiscoveryFilter({"Transport": "bredr"})
        for path, ifaces in objects.items():
            if DEVICE in ifaces and path.startswith(self._adapter_path + "/"):
                self._devices[path] = dict(ifaces[DEVICE])

        self._agent = _make_agent(self, self._bus)
        manager = dbus.Interface(self._bus.get_object(BLUEZ, "/org/bluez"), "org.bluez.AgentManager1")
        manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
        manager.RequestDefaultAgent(AGENT_PATH)
        self._agent_manager = manager

        # Without a registered player, BlueZ drops the headset's button presses.
        self._player = _make_player(self, self._bus)
        self._media = dbus.Interface(self._bus.get_object(BLUEZ, self._adapter_path), MEDIA)
        self._media.RegisterPlayer(PLAYER_PATH, self._player.properties,
                                   reply_handler=lambda: log.info("Headset buttons ready"),
                                   error_handler=lambda e: log.warning(
                                       "Headset buttons unavailable: %s", e))

        for bus_name in (BLUEZ, BLUEALSA):
            self._bus.add_signal_receiver(self._interfaces_added, "InterfacesAdded", OBJECT_MANAGER,
                                          bus_name)
            self._bus.add_signal_receiver(self._interfaces_removed, "InterfacesRemoved",
                                          OBJECT_MANAGER, bus_name)
        self._bus.add_signal_receiver(self._properties_changed, "PropertiesChanged", PROPERTIES,
                                      BLUEZ, path_keyword="path")
        # Also called straight away with the current owner, if bluez-alsa is already running.
        self._bus.watch_name_owner(BLUEALSA, self._bluealsa_owner)

    # ---- main loop interface -------------------------------------------------

    def poll(self):
        while self._context.pending():
            self._context.iteration(False)
        if self._sink_lost_at is not None and time.monotonic() - self._sink_lost_at >= SINK_GRACE:
            self._update_sink(now=True)

    def devices(self) -> list[BtDevice]:
        return visible_devices(self._devices, self._busy)

    def start_scan(self):
        self._adapter.StartDiscovery(reply_handler=lambda: None,
                                     error_handler=lambda e: self._call_failed("scan", e))

    def stop_scan(self):
        if self.scanning:
            self._adapter.StopDiscovery(reply_handler=lambda: None, error_handler=lambda e: None)

    def connect(self, address: str, quiet: bool = False):
        """Pair with the device if need be, then connect. `quiet` reports no failure, for
        reconnecting at startup to a headset that may well be switched off."""
        path = self._path(address)
        if path is None or self._busy.get(path) or self._devices[path].get("Connected"):
            return
        self.stop_scan()  # discovery slows down pairing and connecting, and they can fail
        # One headset at a time.
        for other, props in self._devices.items():
            if other != path and props.get("Connected") and is_audio(props):
                self._device(other).Disconnect(reply_handler=lambda: None, error_handler=lambda e: None)

        def failed(what, error):
            self._set_busy(path, "")
            log.warning("Bluetooth %s %s failed: %s", what, address, error)
            if not quiet:
                name = self._devices.get(path, {}).get("Alias", address)
                self.events.put(("error", f"Couldn't {what} {name}:\n{_reason(error)}"))

        def connect():
            self._set_busy(path, CONNECTING)
            self._device(path).Connect(reply_handler=lambda: self._set_busy(path, ""),
                                       error_handler=lambda e: failed("connect to", e), timeout=30)

        def paired():
            # Trusted lets the headset connect back by itself next time it's switched on.
            try:
                self._dbus.Interface(self._bus.get_object(BLUEZ, path), PROPERTIES).Set(
                    DEVICE, "Trusted", True)
            except self._dbus.DBusException as e:
                log.warning("Couldn't trust %s: %s", address, e)
            connect()

        def pair_failed(error):
            if error.get_dbus_name() == "org.bluez.Error.AlreadyExists":
                paired()
            else:
                failed("pair with", error)

        if self._devices[path].get("Paired"):
            connect()
        else:
            self._set_busy(path, PAIRING)
            self._device(path).Pair(reply_handler=paired, error_handler=pair_failed, timeout=60)

    def disconnect(self, address: str):
        path = self._path(address)
        if path is None or self._busy.get(path):
            return
        self._set_busy(path, DISCONNECTING)
        self._device(path).Disconnect(reply_handler=lambda: self._set_busy(path, ""),
                                      error_handler=lambda e: self._set_busy(path, ""))

    def forget(self, address: str):
        path = self._path(address)
        if path is not None:
            self._adapter.RemoveDevice(path, reply_handler=lambda: None,
                                       error_handler=lambda e: self._call_failed("forget", e))

    def now_playing(self, state: str, position: float, metadata: dict | None):
        """Tell BlueZ what's playing, for headsets that pick play or pause by it."""
        self._player.update(state, position, metadata)

    def close(self):
        for unregister in (self.stop_scan, lambda: self._agent_manager.UnregisterAgent(AGENT_PATH),
                           lambda: self._media.UnregisterPlayer(PLAYER_PATH)):
            try:
                unregister()
            except self._dbus.DBusException:
                pass
        self._bus.close()

    # ---- helpers -------------------------------------------------------------

    def _path(self, address: str) -> str | None:
        for path, props in self._devices.items():
            if props.get("Address") == address:
                return path
        return None

    def _device(self, path: str):
        return self._dbus.Interface(self._bus.get_object(BLUEZ, path), DEVICE)

    def _set_busy(self, path: str, what: str):
        if what:
            self._busy[path] = what
        else:
            self._busy.pop(path, None)
        self.events.put(("changed", None))

    def _call_failed(self, what: str, error):
        log.warning("Bluetooth %s failed: %s", what, error)
        self.events.put(("error", f"Couldn't {what}:\n{_reason(error)}"))

    def is_pairing(self, path: str) -> bool:
        return self._busy.get(path) == PAIRING

    def is_paired(self, path: str) -> bool:
        return bool(self._devices.get(path, {}).get("Paired"))

    # ---- signals -------------------------------------------------------------

    def _interfaces_added(self, path, interfaces):
        path = str(path)
        if DEVICE in interfaces and path.startswith(self._adapter_path + "/"):
            self._devices[path] = dict(interfaces[DEVICE])
            self.events.put(("changed", None))
        if PCM in interfaces:
            self._pcms[path] = dict(interfaces[PCM])
            self._update_sink()

    def _interfaces_removed(self, path, interfaces):
        path = str(path)
        if DEVICE in interfaces and self._devices.pop(path, None) is not None:
            self._busy.pop(path, None)
            self.events.put(("changed", None))
        if PCM in interfaces and self._pcms.pop(path, None) is not None:
            self._update_sink()

    def _properties_changed(self, interface, changed, invalidated, path=None):
        if interface == DEVICE and path in self._devices:
            self._devices[path].update(changed)
            if _SHOWN & set(changed):
                self.events.put(("changed", None))
            if "Connected" in changed and not changed["Connected"]:
                # Don't wait for bluez-alsa to notice: stop playing to the headset straight away.
                for pcm, props in list(self._pcms.items()):
                    if str(props.get("Device")) == path:
                        del self._pcms[pcm]
                self._update_sink(now=True)
        elif interface == ADAPTER and path == self._adapter_path and "Discovering" in changed:
            self.scanning = bool(changed["Discovering"])
            self.events.put(("changed", None))

    def _bluealsa_owner(self, owner: str):
        self._pcms.clear()
        if owner:
            try:
                objects = self._bus.get_object(BLUEALSA, BLUEALSA_ROOT).GetManagedObjects(
                    dbus_interface=OBJECT_MANAGER)
            except self._dbus.DBusException as e:
                log.warning("Can't list bluez-alsa PCMs: %s", e)
                objects = {}
            for path, ifaces in objects.items():
                if PCM in ifaces:
                    self._pcms[str(path)] = dict(ifaces[PCM])
        else:
            log.info("bluez-alsa is not running; Bluetooth headsets can connect but not play")
        self._update_sink(now=True)

    def _update_sink(self, now: bool = False):
        """Report a change of headset to play to. Losing it altogether waits SINK_GRACE
        seconds unless `now`, in case its PCM is only being re-created."""
        sink = pick_sink(self._pcms)
        if sink is None and self._sink is not None and not now:
            if self._sink_lost_at is None:
                self._sink_lost_at = time.monotonic()
            return
        self._sink_lost_at = None
        if sink != self._sink:
            self._sink = sink
            self.events.put(("audio", sink))


def _make_agent(bt: Bluetooth, bus):
    """A pairing agent for a device with no keyboard or display, as headsets expect.

    It only accepts pairing for a device we asked to pair with, so a stranger can't pair
    with the player just by knowing its address.
    """
    import dbus
    import dbus.service

    class Rejected(dbus.DBusException):
        _dbus_error_name = "org.bluez.Error.Rejected"

    def check_pairing(device):
        if not bt.is_pairing(str(device)):
            raise Rejected("not pairing with this device")

    class Agent(dbus.service.Object):
        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
        def RequestPinCode(self, device):
            check_pairing(device)
            return "0000"  # what older headsets without Secure Simple Pairing use

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pincode):
            check_pairing(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
        def RequestPasskey(self, device):
            check_pairing(device)
            return dbus.UInt32(0)

        @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            check_pairing(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            check_pairing(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid):
            if not (bt.is_paired(str(device)) or bt.is_pairing(str(device))):
                raise Rejected("unknown device")

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Cancel(self):
            pass

    return Agent(bus, AGENT_PATH)


def _make_player(bt: Bluetooth, bus):
    """The media player BlueZ forwards a headset's buttons to, as calls to MPRIS methods.

    Called on the main thread, from poll(), so the presses go to the main loop as events.
    """
    import dbus
    import dbus.service

    class Player(dbus.service.Object):
        def __init__(self):
            super().__init__(bus, PLAYER_PATH)
            self.properties = dbus.Dictionary({
                "PlaybackStatus": "Stopped",
                "Position": dbus.Int64(0),
                "CanControl": True, "CanPlay": True, "CanPause": True,
                "CanGoNext": True, "CanGoPrevious": True,
            }, signature="sv")

        def update(self, state: str, position: float, metadata: dict | None):
            changed = {"PlaybackStatus": player_status(state),
                       "Position": dbus.Int64(int(position * 1_000_000))}
            if metadata is not None:
                metadata = dict(metadata)
                if "mpris:length" in metadata:
                    metadata["mpris:length"] = dbus.Int64(metadata["mpris:length"])
                if "xesam:artist" in metadata:
                    metadata["xesam:artist"] = dbus.Array(metadata["xesam:artist"], signature="s")
                changed["Metadata"] = dbus.Dictionary(metadata, signature="sv")
            self.properties.update(changed)
            self.PropertiesChanged(MPRIS_PLAYER, dbus.Dictionary(changed, signature="sv"),
                                   dbus.Array([], signature="s"))

        def _control(self, name: str):
            log.info("Headset button: %s", name)
            bt.events.put(("control", name))

        @dbus.service.method(MPRIS_PLAYER, in_signature="", out_signature="")
        def Play(self):
            self._control("Play")

        @dbus.service.method(MPRIS_PLAYER, in_signature="", out_signature="")
        def Pause(self):
            self._control("Pause")

        @dbus.service.method(MPRIS_PLAYER, in_signature="", out_signature="")
        def PlayPause(self):
            self._control("PlayPause")

        @dbus.service.method(MPRIS_PLAYER, in_signature="", out_signature="")
        def Stop(self):
            self._control("Stop")

        @dbus.service.method(MPRIS_PLAYER, in_signature="", out_signature="")
        def Next(self):
            self._control("Next")

        @dbus.service.method(MPRIS_PLAYER, in_signature="", out_signature="")
        def Previous(self):
            self._control("Previous")

        @dbus.service.method(PROPERTIES, in_signature="ss", out_signature="v")
        def Get(self, interface, name):
            return self.properties[name]

        @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.properties

        @dbus.service.method(PROPERTIES, in_signature="ssv", out_signature="")
        def Set(self, interface, name, value):
            pass  # repeat and shuffle, from headsets that have them: not supported

        @dbus.service.signal(PROPERTIES, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

    return Player()


def open_bluetooth():
    """Return a Bluetooth controller, or None if there's no adapter or BlueZ isn't usable."""
    try:
        bt = Bluetooth()
        log.info("Bluetooth ready")
        return bt
    except Exception as e:
        log.warning("Bluetooth unavailable: %s", e)
        return None
