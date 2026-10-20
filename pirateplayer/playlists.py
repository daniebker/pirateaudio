"""Smart playlists: text files in ~/playlists describing which tracks to pick from the index.

Each `.txt` file is one playlist, named after the file. Lines are `key: value`; blank lines
and lines starting with # are ignored. For example:

    # The 50 tracks played most over the last month
    sort: most played
    period: 30 days
    min plays: 1
    limit: 50

See playlists/README.txt for every key.
"""

import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .database import Track

log = logging.getLogger(__name__)

SUFFIX = ".txt"
README = "readme"  # a README.txt in the folder explains the format; it isn't a playlist

DAY = 86400.0
UNITS = {"hour": DAY / 24, "day": DAY, "week": 7 * DAY, "month": 30 * DAY, "year": 365 * DAY}

MOST_PLAYED, LEAST_PLAYED = "most played", "least played"
RECENTLY_PLAYED, LEAST_RECENTLY_PLAYED = "recently played", "least recently played"
RECENTLY_ADDED, RANDOM, ALBUM, TITLE = "recently added", "random", "album", "title"
SORTS = (MOST_PLAYED, LEAST_PLAYED, RECENTLY_PLAYED, LEAST_RECENTLY_PLAYED, RECENTLY_ADDED,
         RANDOM, ALBUM, TITLE)
TEXT_FILTERS = ("artist", "album", "genre", "folder")


def _natural(text: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def parse_duration(text: str) -> float:
    """'30 days', '2 weeks', '6 months', '1 year', '12 hours'; a bare number is days."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-z]*?)s?\s*", text.lower())
    if not match:
        raise ValueError(f"not a duration: {text!r}")
    number, unit = float(match[1]), match[2] or "day"
    if unit not in UNITS:
        raise ValueError(f"unknown unit {unit!r}; use hours, days, weeks, months or years")
    return number * UNITS[unit]


@dataclass
class SmartPlaylist:
    name: str
    sort: str = ALBUM
    limit: int | None = None
    period: float | None = None         # only plays this recent count towards min/max plays
    min_plays: int | None = None
    max_plays: int | None = None
    not_played_in: float | None = None  # last played longer ago than this, or never
    played_in: float | None = None      # last played within this
    added_in: float | None = None       # added to the player within this
    # Each matches if any of its values is in the track's tag (or, for folder, its path under
    # the music folder), ignoring case. Every one given must match.
    filters: dict[str, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def select(self, tracks: list[Track], root: Path, now: float | None = None) -> list[Path]:
        """Pick and order tracks from the index. `tracks` carries plays over `self.period`."""
        now = time.time() if now is None else now
        picked = [t for t in tracks if self._matches(t, root, now)]
        if self.sort == RANDOM:
            random.shuffle(picked)
        else:
            picked.sort(key=self._key())
        if self.limit is not None:
            picked = picked[:self.limit]
        return [t.path for t in picked]

    def since(self, now: float | None = None) -> float:
        """The start of the period whose plays count."""
        if self.period is None:
            return 0.0
        return (time.time() if now is None else now) - self.period

    def _matches(self, t: Track, root: Path, now: float) -> bool:
        if self.min_plays is not None and t.plays < self.min_plays:
            return False
        if self.max_plays is not None and t.plays > self.max_plays:
            return False
        if self.not_played_in is not None and t.last_played is not None \
                and t.last_played > now - self.not_played_in:
            return False
        if self.played_in is not None and (t.last_played is None
                                           or t.last_played < now - self.played_in):
            return False
        if self.added_in is not None and t.added < now - self.added_in:
            return False
        for key, values in self.filters.items():
            if key == "folder":
                try:
                    text = t.path.relative_to(root).as_posix()
                except ValueError:
                    text = t.path.as_posix()
            else:
                text = getattr(t, key)
            text = text.lower()
            if not any(v.lower() in text for v in values):
                return False
        return True

    def _key(self):
        album_order = lambda t: (_natural(t.artist), _natural(t.album), _natural(str(t.path)))
        last = lambda t: t.last_played or 0.0
        return {
            MOST_PLAYED: lambda t: (-t.plays, -last(t), album_order(t)),
            LEAST_PLAYED: lambda t: (t.plays, last(t), album_order(t)),
            RECENTLY_PLAYED: lambda t: (-last(t), album_order(t)),
            LEAST_RECENTLY_PLAYED: lambda t: (last(t), album_order(t)),
            RECENTLY_ADDED: lambda t: (-t.added, album_order(t)),
            ALBUM: album_order,
            TITLE: lambda t: (_natural(t.title), album_order(t)),
        }[self.sort]


def parse(text: str, name: str) -> SmartPlaylist:
    """Read a playlist file's text. Lines that don't make sense are skipped and listed in
    `problems`, so one typo doesn't lose the whole playlist."""
    pl = SmartPlaylist(name)
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # "key: value" or "key = value", whichever comes first, so values can contain the other.
        cut = min((i for i in (line.find(":"), line.find("=")) if i >= 0), default=-1)
        key, sep, value = (line[:cut], line[cut], line[cut + 1:]) if cut >= 0 else (line, "", "")
        key = " ".join(key.lower().replace("_", " ").split())
        value = value.split(" #", 1)[0].strip()
        try:
            if not sep or not value:
                raise ValueError("expected 'key: value'")
            _apply(pl, key, value)
        except ValueError as e:
            pl.problems.append(f"line {number}: {e}")
    return pl


def _apply(pl: SmartPlaylist, key: str, value: str):
    def count() -> int:
        if not value.isdigit():
            raise ValueError(f"{key} must be a whole number")
        return int(value)

    if key == "name":
        pl.name = value
    elif key == "sort":
        sort = " ".join(value.lower().split())
        if sort not in SORTS:
            raise ValueError(f"unknown sort {value!r}; use one of: {', '.join(SORTS)}")
        pl.sort = sort
    elif key == "limit":
        pl.limit = count()
    elif key == "period":
        pl.period = parse_duration(value)
    elif key == "min plays":
        pl.min_plays = count()
    elif key == "max plays":
        pl.max_plays = count()
    elif key == "not played in":
        pl.not_played_in = parse_duration(value)
    elif key == "played in":
        pl.played_in = parse_duration(value)
    elif key == "added in":
        pl.added_in = parse_duration(value)
    elif key in TEXT_FILTERS:
        pl.filters.setdefault(key, []).append(value)
    else:
        raise ValueError(f"unknown setting {key!r}")


def load_all(directory: Path) -> list[SmartPlaylist]:
    """Every playlist in the folder, in natural order of file name."""
    try:
        files = [p for p in directory.iterdir()
                 if p.suffix.lower() == SUFFIX and p.is_file() and not p.name.startswith(".")
                 and p.stem.lower() != README]
    except OSError:
        return []
    playlists = []
    for path in sorted(files, key=lambda p: _natural(p.name)):
        try:
            pl = parse(path.read_text(encoding="utf-8", errors="replace"), path.stem)
        except OSError as e:
            log.warning("Can't read playlist %s: %s", path, e)
            continue
        for problem in pl.problems:
            log.warning("Playlist %s, %s", path.name, problem)
        playlists.append(pl)
    return playlists
