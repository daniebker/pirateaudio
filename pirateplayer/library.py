"""Browsing the music folder and reading track metadata."""

import hashlib
import io
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

TRACK_SUFFIXES = {".mp3", ".flac", ".wav"}
COVER_NAMES = ("cover", "folder", "front", "album")


def _natural_text(text: str):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", text)]


def _natural_key(path: Path):
    return _natural_text(path.name)


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
    tags, pictures, length = _read_tags(path)
    return TrackInfo(title=tags.get("title") or path.stem, artist=tags.get("artist", ""),
                     album=tags.get("album", ""), length=length,
                     art=(_open_picture(pictures) or _folder_art(path.parent)) if art else None,
                     genre=tags.get("genre", ""), year=tags.get("year", "")[:4])


def _read_tags(path: Path) -> tuple[dict[str, str], list[bytes], float]:
    """A track's tags, its embedded pictures (front cover first, not yet decoded) and length."""
    try:
        import mutagen

        audio = mutagen.File(path)
    except Exception as e:  # mutagen raises a variety of errors on bad files
        log.warning("Could not read tags from %s: %s", path, e)
        audio = None

    tags, pictures, length = {}, [], 0.0
    if audio is not None:
        length = audio.info.length
        tags, pictures = _tags_and_pictures(audio)
    if path.suffix.lower() == ".wav":
        # WAV files usually carry RIFF INFO tags, which mutagen doesn't read.
        tags = _riff_info(path) | {k: v for k, v in tags.items() if v}
    return tags, pictures, length


def _tags_and_pictures(audio) -> tuple[dict[str, str], list[bytes]]:
    from mutagen.id3 import ID3

    tags, pictures = audio.tags, []
    if isinstance(tags, ID3):  # MP3, and WAV files with an ID3 chunk
        def text(*frame_ids):
            for frame_id in frame_ids:
                frame = tags.get(frame_id)
                if frame and frame.text:
                    return str(frame.text[0]).strip()
            return ""

        found = {"title": text("TIT2"), "artist": text("TPE1", "TPE2"), "album": text("TALB"),
                 "albumartist": text("TPE2", "TPE1"), "genre": text("TCON"),
                 "year": text("TDRC", "TYER")}
        pictures = [tags[k].data for k in tags.keys() if k.startswith("APIC")]
    elif tags is not None:  # Vorbis comments (FLAC)
        def text(*keys):
            for key in keys:
                values = tags.get(key)
                if values:
                    return str(values[0]).strip()
            return ""

        found = {"title": text("title"), "artist": text("artist", "albumartist"),
                 "album": text("album"), "albumartist": text("albumartist", "artist"),
                 "genre": text("genre"), "year": text("date", "year")}
    else:
        found = {}
    # FLAC keeps pictures outside the tags; prefer the front cover (type 3).
    flac_pictures = sorted(getattr(audio, "pictures", []), key=lambda p: p.type != 3)
    pictures += [p.data for p in flac_pictures]
    return found, pictures


def _open_picture(pictures: list[bytes], size: int | None = None) -> Image.Image | None:
    for data in pictures:
        image = _open_image(io.BytesIO(data), size)
        if image is not None:
            return image
    return None


def _open_image(source, size: int | None = None) -> Image.Image | None:
    """Decode an image file or buffer, or None if it isn't one. With a size, a big JPEG is
    decoded at a fraction of its resolution, still at least that size: much faster on a Pi."""
    try:
        image = Image.open(source)
        if size:
            image.draft("RGB", (size, size))
        return image.convert("RGB")
    except Exception:
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


def _cover_files(directory: Path) -> list[Path]:
    try:
        return sorted(p for p in directory.iterdir()
                      if p.stem.lower() in COVER_NAMES and p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    except OSError:
        return []


def _folder_art(directory: Path, size: int | None = None) -> Image.Image | None:
    for p in _cover_files(directory):
        image = _open_image(p, size)
        if image is not None:
            return image
    return None


# ---- albums -------------------------------------------------------------------


@dataclass(frozen=True)
class Album:
    """A folder of tracks that has cover art, named from its first track's tags."""
    name: str
    artist: str
    folder: Path
    cover: Path  # the image file, or the track the picture is embedded in


def find_albums(root: Path) -> list[Album]:
    """Every folder below root with tracks in it and a cover, in alphabetical order of album
    name. Loose tracks in root itself aren't an album. The cover is a cover.jpg (or the like) in the folder, or a picture embedded in its
    first track. This reads one track per folder, so it takes a while on a big library."""
    albums = []
    for directory, subdirs, _ in os.walk(root):
        subdirs[:] = [d for d in subdirs if not d.startswith(".")]
        tracks = tracks_in(Path(directory)) if Path(directory) != root else []
        if not tracks:
            continue
        tags, pictures, _ = _read_tags(tracks[0])
        covers = _cover_files(Path(directory))
        if covers:
            cover = covers[0]
        elif pictures:
            cover = tracks[0]
        else:
            continue
        albums.append(Album(name=tags.get("album") or Path(directory).name,
                            artist=tags.get("albumartist") or tags.get("artist", ""),
                            folder=Path(directory), cover=cover))
    albums.sort(key=lambda a: (_natural_text(a.name), _natural_text(a.artist), _natural_key(a.folder)))
    return albums


def album_art(album: Album, size: int | None = None) -> Image.Image | None:
    """The album's cover, decoded at no less than size (if given) for speed."""
    if is_track(album.cover):
        return _open_picture(_read_tags(album.cover)[1], size)
    return _open_image(album.cover, size) or _folder_art(album.folder, size)


def _thumbnail_path(album: Album, size: int, cache_dir: Path) -> Path:
    """Named for the cover file and when it last changed, so a new cover gets a new name."""
    stat = album.cover.stat()
    key = f"{album.cover}:{stat.st_mtime_ns}:{stat.st_size}:{size}"
    return cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".rgb")


def album_thumbnail(album: Album, size: int, cache_dir: Path | None = None) -> Image.Image | None:
    """The album's cover cropped square at size. Decoding a full-size cover takes a Pi Zero a
    few hundred milliseconds, so the result is kept in cache_dir as raw pixels, which take it
    a few milliseconds to read back."""
    try:
        path = _thumbnail_path(album, size, cache_dir) if cache_dir else None
    except OSError:  # the cover has gone
        return None
    if path is not None:
        try:
            return Image.frombytes("RGB", (size, size), path.read_bytes())
        except (OSError, ValueError):  # not made yet, or cut short
            pass
    art = album_art(album, size)
    if art is None:
        return None
    thumbnail = ImageOps.fit(art, (size, size))
    if path is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{threading.get_native_id()}.tmp")  # threads may race
            tmp.write_bytes(thumbnail.tobytes())
            os.replace(tmp, path)
        except OSError as e:
            log.warning("Could not save a thumbnail of %s: %s", album.cover, e)
    return thumbnail


def prune_thumbnails(albums: list[Album], size: int, cache_dir: Path):
    """Delete thumbnails of covers that have changed or gone."""
    keep = set()
    for album in albums:
        try:
            keep.add(_thumbnail_path(album, size, cache_dir))
        except OSError:
            pass
    try:
        for path in cache_dir.iterdir():
            if path not in keep and path.suffix != ".tmp":
                path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not prune thumbnails: %s", e)
