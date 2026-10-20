import queue

from pirateplayer.bluetooth import (A2DP_SINK_UUID, CONNECTING, DEVICE, PCM, SINK_GRACE,
                                    Bluetooth, BtDevice, address_from_path, audio_device, is_audio,
                                    pick_sink, visible_devices)

HEADPHONES_CLASS = 0x240404  # Audio/Video major class, wearable headset minor class
PHONE_CLASS = 0x5A020C


def dev(address, **props):
    return f"/org/bluez/hci0/dev_{address.replace(':', '_')}", {"Address": address, **props}


def test_audio_device():
    assert audio_device("00:1A:7D:DA:71:13") == "alsa/bluealsa:DEV=00:1A:7D:DA:71:13,PROFILE=a2dp"


def test_address_from_path():
    assert address_from_path("/org/bluez/hci0/dev_00_1A_7D_DA_71_13") == "00:1A:7D:DA:71:13"
    assert address_from_path("/org/bluealsa/hci0/dev_00_1A_7D_DA_71_13/a2dpsrc/sink") \
        == "00:1A:7D:DA:71:13"
    assert address_from_path("/org/bluez/hci0") == ""


def test_is_audio():
    assert is_audio({"UUIDs": [A2DP_SINK_UUID]})
    assert is_audio({"Icon": "audio-headphones"})
    assert is_audio({"Class": HEADPHONES_CLASS})
    assert not is_audio({"Class": PHONE_CLASS, "Icon": "phone"})
    assert not is_audio({})


def test_visible_devices_lists_paired_and_named_audio_devices():
    devices = dict([
        dev("00:00:00:00:00:01", Name="Phone", Class=PHONE_CLASS),            # not audio
        dev("00:00:00:00:00:02", Class=HEADPHONES_CLASS),                     # no name yet
        dev("00:00:00:00:00:03", Name="speaker", Alias="Speaker", Class=HEADPHONES_CLASS),
        dev("00:00:00:00:00:04", Name="Old phone", Alias="Old phone", Paired=True),
        dev("00:00:00:00:00:05", Name="Headset", Alias="Headset", Paired=True, Connected=True,
            UUIDs=[A2DP_SINK_UUID]),
    ])
    busy = {"/org/bluez/hci0/dev_00_00_00_00_00_03": CONNECTING}
    assert visible_devices(devices, busy) == [
        BtDevice("00:00:00:00:00:05", "Headset", paired=True, connected=True),
        BtDevice("00:00:00:00:00:04", "Old phone", paired=True),
        BtDevice("00:00:00:00:00:03", "Speaker", busy=CONNECTING),
    ]


def test_pick_sink_chooses_the_newest_a2dp_sink():
    def pcm(address, transport="A2DP-source", mode="sink", sequence=0):
        path = f"/org/bluez/hci0/dev_{address.replace(':', '_')}"
        return {"Device": path, "Transport": transport, "Mode": mode, "Sequence": sequence}

    assert pick_sink({}) is None
    pcms = {
        "/a": pcm("00:00:00:00:00:01", sequence=1),
        "/b": pcm("00:00:00:00:00:02", sequence=2),
        "/c": pcm("00:00:00:00:00:03", transport="A2DP-sink", mode="source", sequence=3),
        "/d": pcm("00:00:00:00:00:04", transport="HFP-AG", sequence=4),
    }
    assert pick_sink(pcms) == "00:00:00:00:00:02"
    del pcms["/b"]
    assert pick_sink(pcms) == "00:00:00:00:00:01"


def test_headset_disconnecting_stops_audio_before_bluealsa_notices():
    path, props = dev("00:1A:7D:DA:71:13", Name="Headset", Paired=True, Connected=True)
    bt = Bluetooth.__new__(Bluetooth)  # no D-Bus: drive the signal handler directly
    bt.events = queue.Queue()
    bt._devices = {path: props}
    bt._pcms = {path + "/a2dpsrc/sink": {"Device": path, "Transport": "A2DP-source", "Mode": "sink"}}
    bt._sink = "00:1A:7D:DA:71:13"
    bt._sink_lost_at = None

    bt._properties_changed(DEVICE, {"RSSI": -50}, [], path=path)
    assert bt.events.empty()
    bt._properties_changed(DEVICE, {"Connected": False}, [], path=path)
    events = []
    while not bt.events.empty():
        events.append(bt.events.get())
    assert ("audio", None) in events
    assert bt._pcms == {}


class IdleContext:
    def pending(self):
        return False


def headset_bt():
    """A Bluetooth with no D-Bus, playing to a connected headset."""
    path, props = dev("00:1A:7D:DA:71:13", Name="Headset", Paired=True, Connected=True)
    bt = Bluetooth.__new__(Bluetooth)
    bt.events = queue.Queue()
    bt._context = IdleContext()
    bt._devices = {path: props}
    bt._pcms = {}
    bt._sink = None
    bt._sink_lost_at = None
    pcm = path + "/a2dpsrc/sink"
    bt._interfaces_added(pcm, {PCM: {"Device": path, "Transport": "A2DP-source", "Mode": "sink"}})
    assert bt.events.get() == ("audio", "00:1A:7D:DA:71:13")
    return bt, path, pcm


def test_pcm_re_created_while_connected_is_not_a_disconnect():
    bt, path, pcm = headset_bt()
    bt._interfaces_removed(pcm, [PCM])
    bt.poll()
    bt._interfaces_added(pcm, {PCM: {"Device": path, "Transport": "A2DP-source", "Mode": "sink"}})
    bt.poll()
    assert bt.events.empty()
    assert bt._sink_lost_at is None


def test_pcm_gone_for_good_is_reported_after_the_grace_period():
    bt, path, pcm = headset_bt()
    bt._interfaces_removed(pcm, [PCM])
    bt.poll()
    assert bt.events.empty()
    bt._sink_lost_at -= SINK_GRACE
    bt.poll()
    assert bt.events.get() == ("audio", None)
