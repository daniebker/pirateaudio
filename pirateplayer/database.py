"""The music library index and play history, kept in SQLite.

Tracks are indexed in the background: on a Pi Zero, reading the tags of a big library takes
minutes. Later syncs only re-read files whose size or modification time changed.

Plays are kept in their own table, by path, rather than as a count on the track: a track
that disappears from the index (say, while its folder is being renamed) gets its history
back when it reappears.
"""

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .library import TRACK_SUFFIXES, read_info

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
BATCH = 50  # tracks written per transaction while indexing

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    path   TEXT PRIMARY KEY,
    title  TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album  TEXT NOT NULL DEFAULT '',
    genre  TEXT NOT NULL DEFAULT '',
    year   TEXT NOT NULL DEFAULT '',
    length REAL NOT NULL DEFAULT 0,
    size   INTEGER NOT NULL,
    mtime  REAL NOT NULL,
    added  REAL NOT NULL   -- when the file arrived on the player
);
CREATE TABLE IF NOT EXISTS plays (
    path TEXT NOT NULL,
    at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS plays_by_path ON plays (path, at);
"""


@dataclass
class Track:
    """A row of the index, with its play count and last play over the period asked for."""
    path: Path
    title: str
    artist: str
    album: str
    genre: str
    year: str
    length: float
    added: float
    plays: int = 0
    last_played: float | None = None


class Database:
    """One connection shared by the main loop and the indexer thread, behind a lock."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, and kinder to SD cards
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(f"made by a newer version (schema {version})")
        with self._conn:
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- the index -------------------------------------------------------

    def known(self) -> dict[str, tuple[int, float]]:
        """Indexed paths and the (size, mtime) they had when their tags were read."""
        with self._lock:
            return {p: (s, m) for p, s, m in self._conn.execute(
                "SELECT path, size, mtime FROM tracks")}

    def put_tracks(self, rows: list[dict]):
        """Add or update tracks. A track already indexed keeps the time it was added."""
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO tracks (path, title, artist, album, genre, year, length, size, mtime,
                                       added)
                   VALUES (:path, :title, :artist, :album, :genre, :year, :length, :size, :mtime,
                           :added)
                   ON CONFLICT (path) DO UPDATE SET
                       title=excluded.title, artist=excluded.artist, album=excluded.album,
                       genre=excluded.genre, year=excluded.year, length=excluded.length,
                       size=excluded.size, mtime=excluded.mtime""", rows)

    def remove_tracks(self, paths):
        with self._lock, self._conn:
            self._conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in paths])

    def track_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

    # ---- plays ---------------------------------------------------------------

    def record_play(self, path: Path, at: float | None = None):
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO plays (path, at) VALUES (?, ?)",
                               (str(path), time.time() if at is None else at))

    def play_count(self, path: Path, since: float = 0.0) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM plays WHERE path = ? AND at >= ?",
                                      (str(path), since)).fetchone()[0]

    def tracks(self, since: float = 0.0) -> list[Track]:
        """Every indexed track, with its plays since `since` and when it was last played ever."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT t.path, t.title, t.artist, t.album, t.genre, t.year, t.length, t.added,
                          COALESCE(p.n, 0), p.last
                   FROM tracks t LEFT JOIN (
                       SELECT path, SUM(at >= ?) AS n, MAX(at) AS last FROM plays GROUP BY path
                   ) p ON p.path = t.path""", (since,)).fetchall()
        return [Track(Path(r[0]), *r[1:]) for r in rows]


def open_database(path: Path) -> Database | None:
    """Open the database, starting afresh if the file is unreadable. None if that fails too."""
    try:
        return Database(path)
    except sqlite3.DatabaseError as e:
        broken = path.with_name(path.name + ".broken")
        log.error("Library database %s is unusable (%s); moving it to %s", path, e, broken)
        try:
            os.replace(path, broken)
            return Database(path)
        except (OSError, sqlite3.Error) as e:
            log.error("No library database: %s", e)
    except (OSError, sqlite3.Error) as e:
        log.error("No library database: %s", e)
    return None


def _walk(root: Path):
    """Every track under root, skipping hidden files and folders."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not name.startswith(".") and os.path.splitext(name)[1].lower() in TRACK_SUFFIXES:
                yield Path(folder) / name


def sync(db: Database, root: Path, progress=None, stop: threading.Event | None = None) -> int:
    """Bring the index in line with the files under root; return how many tracks there are.

    `progress(count)` is called as tracks are found. Setting `stop` abandons the sync, leaving
    what's been indexed so far.
    """
    known = db.known()
    seen: set[str] = set()
    batch: list[dict] = []
    for path in _walk(root):
        if stop is not None and stop.is_set():
            break
        key = str(path)
        try:
            st = path.stat()
        except OSError:
            continue
        seen.add(key)
        if known.get(key) != (st.st_size, st.st_mtime):
            info = read_info(path, art=False)
            # ctime is when the file arrived on this filesystem: copying keeps the mtime
            # (rsync -a does), but can't keep the ctime.
            batch.append({"path": key, "title": info.title, "artist": info.artist,
                          "album": info.album, "genre": info.genre, "year": info.year,
                          "length": info.length, "size": st.st_size, "mtime": st.st_mtime,
                          "added": st.st_ctime})
            if len(batch) >= BATCH:
                db.put_tracks(batch)
                batch = []
        if progress:
            progress(len(seen))
    if batch:
        db.put_tracks(batch)
    if stop is None or not stop.is_set():
        db.remove_tracks(set(known) - seen)
    return len(seen)


class Indexer:
    """Runs sync() in a background thread, one at a time."""

    def __init__(self, db: Database, root: Path):
        self.db = db
        self.root = root
        self.found = 0          # tracks found so far by the running sync
        self.last_count: int | None = None  # tracks found by the last finished sync
        self.finished = 0       # syncs finished, so callers can tell when another one has
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start a sync, unless one is running already."""
        if self.running:
            return False
        self.found = 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="indexer", daemon=True)
        self._thread.start()
        return True

    def _run(self):
        started = time.monotonic()
        try:
            count = sync(self.db, self.root, self._progress, self._stop)
        except Exception:
            log.exception("Indexing %s failed", self.root)
            return
        self.last_count = count
        self.finished += 1
        log.info("Indexed %d tracks in %.1fs", count, time.monotonic() - started)

    def _progress(self, count: int):
        self.found = count

    def join(self, timeout: float | None = None):
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self):
        self._stop.set()
        self.join(5)
