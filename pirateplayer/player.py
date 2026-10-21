"""Audio playback by driving mpv over its JSON IPC socket."""

import json
import logging
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

STOPPED, PAUSED, PLAYING = "stopped", "paused", "playing"

_POSITION_REQUEST = 1  # request_id used for time-pos queries
_PAUSE_OBSERVER = 1


class Player:
    def __init__(self, device: str = ""):
        self._dir = tempfile.mkdtemp(prefix="pirateplayer-")
        sock_path = f"{self._dir}/mpv.sock"
        # No scripts or key bindings: this mpv is headless, and RAM is tight on a Pi Zero.
        cmd = ["mpv", "--no-config", "--idle=yes", "--no-video", "--no-terminal",
               "--audio-display=no", "--load-scripts=no", "--osc=no", "--ytdl=no",
               "--input-default-bindings=no", "--prefetch-playlist=yes",
               f"--input-ipc-server={sock_path}"]
        if device:
            cmd.append(f"--audio-device={device}")
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        try:
            self._sock = self._connect(sock_path)
        except RuntimeError:
            self._proc.kill()
            shutil.rmtree(self._dir, ignore_errors=True)
            raise
        self._file = self._sock.makefile("r", encoding="utf-8", errors="replace")

        # Events for the main loop: ("ended", None), ("next", None) when it went straight on to
        # the queued track, ("error", message), ("died", None)
        self.events: queue.Queue = queue.Queue()
        self.state = STOPPED
        self.position = 0.0
        # Between our loadfile and mpv's start-file, any end-file belongs to the old track.
        self._loading = False
        self._lock = threading.Lock()  # commands are sent from the main and reader threads
        self._queue_lock = threading.RLock()  # and both of them queue the next track
        self._loaded = False  # whether mpv has opened the current track
        self._next: Path | None = None  # the track to go straight on to, for gapless playback
        self._queued = False  # whether _next has been handed to mpv
        threading.Thread(target=self._read_output, daemon=True).start()
        self._send("observe_property", _PAUSE_OBSERVER, "pause")

    def _connect(self, path: str) -> socket.socket:
        deadline = time.monotonic() + 60  # mpv takes 7s or more to start on a Pi Zero
        while True:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(path)
                return sock
            except OSError:
                sock.close()
                if self._proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("could not start mpv")
                time.sleep(0.1)

    def _send(self, *command, request_id: int | None = None):
        message = {"command": list(command)}
        if request_id is not None:
            message["request_id"] = request_id
        try:
            with self._lock:
                self._sock.sendall((json.dumps(message) + "\n").encode())
        except OSError:
            log.error("mpv is not running")

    def _read_output(self):
        for line in self._file:
            try:
                self._handle(json.loads(line))
            except ValueError:
                pass
        self.events.put(("died", None))

    def _handle(self, msg: dict):
        event = msg.get("event")
        if msg.get("request_id") == _POSITION_REQUEST:
            if isinstance(msg.get("data"), (int, float)):
                self.position = msg["data"]
        elif event == "start-file":
            self._loading = False
            self._loaded = False
        elif event == "file-loaded":
            self._loaded = True
            self._send_next()
        elif event == "end-file" and not self._loading:
            self._loaded = False
            reason = msg.get("reason")
            with self._queue_lock:
                queued, self._queued = self._queued, False
                if queued:
                    self._next = None
            if reason == "eof" and queued:
                # mpv carries straight on to the queued track.
                self.position = 0.0
                self.events.put(("next", None))
            elif reason == "eof":
                self.state = STOPPED
                self.events.put(("ended", None))
            elif reason == "error":
                self.state = STOPPED
                self.events.put(("error", msg.get("file_error", "unknown error")))
        elif event == "property-change" and msg.get("name") == "pause":
            if self.state != STOPPED:
                self.state = PAUSED if msg.get("data") else PLAYING

    def load(self, path: Path, start: float = 0.0, paused: bool = False):
        self._loading = True
        self._loaded = False
        self._next, self._queued = None, False
        self.position = start
        self.state = PAUSED if paused else PLAYING
        # Options set now apply to the next file mpv loads.
        self._send("set_property", "start", f"{start:.1f}")
        self._send("set_property", "pause", paused)
        self._send("loadfile", str(path), "replace")

    def queue(self, path: Path | None):
        """Have mpv go straight on to `path` when this track ends, with no gap between them;
        None plays nothing after it."""
        with self._queue_lock:
            self._next = path
            if self._queued:
                self._queued = False
                self._send("playlist-clear")  # everything but the current track
            self._send_next()

    def _send_next(self):
        # Only once the current track is open: it has already used the "start" option by then,
        # so it can be reset for the next one.
        with self._queue_lock:
            if self._loaded and self._next is not None and not self._queued:
                self._queued = True
                self._send("set_property", "start", "0")
                # append-play, so that if this track ended first, the next one plays anyway.
                self._send("loadfile", str(self._next), "append-play")

    def toggle_pause(self):
        if self.state != STOPPED:
            self._send("cycle", "pause")

    def pause(self):
        if self.state != STOPPED:
            self.state = PAUSED
            self._send("set_property", "pause", True)

    def stop(self):
        self.state = STOPPED
        self._next, self._queued = None, False
        self._send("stop")

    def seek(self, seconds: float):
        self.position = max(0.0, seconds)
        self._send("seek", self.position, "absolute")

    def set_volume(self, percent: int):
        self._send("set_property", "volume", percent)

    def set_audio_device(self, device: str):
        """Switch output, e.g. to a Bluetooth headset; mpv reopens audio straight away."""
        self._send("set_property", "audio-device", device or "auto")

    def poll_position(self):
        if self.state != STOPPED:
            self._send("get_property", "time-pos", request_id=_POSITION_REQUEST)

    def close(self):
        self._send("quit")
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._sock.close()
        shutil.rmtree(self._dir, ignore_errors=True)
