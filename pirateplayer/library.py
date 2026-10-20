"""Browsing the music folder and reading track metadata."""

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

TRACK_SUFFIXES = {".mp3", ".flac", ".wav"}
COVER_NAMES = ("cover", "folder", "front", "album")


def _natural_key(path: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def is_track(path: Path) -> bool:
    return path.suffix.lower() in TRACK_SUFFIXES and path.is_file()


def list_dir(directory: Path) -> list[Path]:
    """Folders first, then tracks, in natural order. Hidden entries are skipped."""
    try:
        entries = [p for p in directory.iterdir() if not p.name.startswith(".")]
    except OSError:
        return []
    dirs = sorted((p for p in entries if p.is_dir()), key=_natural_key)
    tracks = sorted((p for p in entries if is_track(p)), key=_natural_key)
    return dirs + tracks


def tracks_in(directory: Path, recursive: bool = False) -> list[Path]:
    tracks = []
    for p in list_dir(directory):
        if p.is_dir():
            if recursive:
                tracks.extend(tracks_in(p, recursive=True))
        else:
            tracks.append(p)
    return tracks


@dataclass
class TrackInfo:
    title: str
    artist: str = ""
    album: str = ""
    length: float = 0.0
    art: Image.Image | None = None
    genre: str = ""
    year: str = ""


def read_info(path: Path, art: bool = True) -> TrackInfo:
    """Tags, length and cover art. Without `art`, skips decoding the cover, which is slow."""
    info = TrackInfo(title=path.stem)
    try:
        import mutagen

        audio = mutagen.File(path)
    except Exception as e:  # mutagen raises a variety of errors on bad files
        log.warning("Could not read tags from %s: %s", path, e)
        audio = None

    tags = {}
    if audio is not None:
        info.length = audio.info.length
        tags = _tags(audio)
        if art:
            info.art = _embedded_art(audio)
    if path.suffix.lower() == ".wav":
        # WAV files usually carry RIFF INFO tags, which mutagen doesn't read.
        tags = _riff_info(path) | {k: v for k, v in tags.items() if v}

    info.title = tags.get("title") or path.stem
    info.artist = tags.get("artist", "")
    info.album = tags.get("album", "")
    info.genre = tags.get("genre", "")
    info.year = tags.get("year", "")[:4]
    if art and info.art is None:
        info.art = _folder_art(path.parent)
    return info


def _tags(audio) -> dict[str, str]:
    from mutagen.id3 import ID3

    tags = audio.tags
    if isinstance(tags, ID3):  # MP3, and WAV files with an ID3 chunk
        def text(*frame_ids):
            for frame_id in frame_ids:
                frame = tags.get(frame_id)
                if frame and frame.text:
                    return str(frame.text[0]).strip()
            return ""

        return {"title": text("TIT2"), "artist": text("TPE1", "TPE2"), "album": text("TALB"),
                "genre": text("TCON"), "year": text("TDRC", "TYER")}
    if tags is not None:  # Vorbis comments (FLAC)
        def text(*keys):
            for key in keys:
                values = tags.get(key)
                if values:
                    return str(values[0]).strip()
            return ""

        return {"title": text("title"), "artist": text("artist", "albumartist"),
                "album": text("album"), "genre": text("genre"), "year": text("date", "year")}
    return {}


def _embedded_art(audio) -> Image.Image | None:
    from mutagen.id3 import ID3

    pictures = []
    if isinstance(audio.tags, ID3):
        pictures = [audio.tags[k].data for k in audio.tags.keys() if k.startswith("APIC")]
    # FLAC keeps pictures outside the tags; prefer the front cover (type 3).
    flac_pictures = sorted(getattr(audio, "pictures", []), key=lambda p: p.type != 3)
    pictures += [p.data for p in flac_pictures]

    for data in pictures:
        try:
            return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            pass
    return None


def _riff_info(path: Path) -> dict[str, str]:
    """Read title/artist/album from a WAV file's LIST/INFO chunk."""
    names = {b"INAM": "title", b"IART": "artist", b"IPRD": "album"}
    found = {}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return found
            f.seek(12)
            while header := f.read(8):
                if len(header) < 8:
                    break
                chunk_id, size = header[:4], int.from_bytes(header[4:], "little")
                if chunk_id == b"LIST" and f.read(4) == b"INFO":
                    data = f.read(size - 4)
                    pos = 0
                    while pos + 8 <= len(data):
                        sub_id, sub_size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
                        value = data[pos + 8:pos + 8 + sub_size].split(b"\0", 1)[0]
                        if sub_id in names:
                            found[names[sub_id]] = value.decode("utf-8", "replace").strip()
                        pos += 8 + sub_size + (sub_size & 1)
                    break
                f.seek(size + (size & 1) - (4 if chunk_id == b"LIST" else 0), 1)
    except OSError:
        pass
    return found


def _folder_art(directory: Path) -> Image.Image | None:
    try:
        candidates = [p for p in directory.iterdir()
                      if p.stem.lower() in COVER_NAMES and p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    except OSError:
        return None
    for p in sorted(candidates):
        try:
            return Image.open(p).convert("RGB")
        except Exception:
            pass
    return None
