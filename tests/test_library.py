from mutagen.id3 import APIC, TALB, TIT2, TPE1, TPE2
from mutagen.wave import WAVE

from pirateplayer.library import (_natural_key, _riff_info, album_art, album_thumbnail,
                                  find_albums, is_track, list_dir, prune_thumbnails, read_info,
                                  tracks_in)

from .conftest import make_wav, png_bytes


def test_natural_order(tmp_path):
    names = ["10 x", "2 x", "1 x", "B", "a"]
    paths = sorted((tmp_path / n for n in names), key=_natural_key)
    assert [p.name for p in paths] == ["1 x", "2 x", "10 x", "a", "B"]


def test_is_track(tmp_path):
    for name in ("a.mp3", "b.FLAC", "c.wav", "d.ogg"):
        (tmp_path / name).touch()
    (tmp_path / "dir.mp3").mkdir()
    assert is_track(tmp_path / "a.mp3")
    assert is_track(tmp_path / "b.FLAC")
    assert is_track(tmp_path / "c.wav")
    assert not is_track(tmp_path / "d.ogg")
    assert not is_track(tmp_path / "dir.mp3")
    assert not is_track(tmp_path / "missing.mp3")


def test_list_dir_folders_first_and_skips_hidden_and_other_files(music):
    (music / ".hidden").mkdir()
    (music / ".secret.wav").touch()
    (music / "notes.txt").touch()
    assert [p.name for p in list_dir(music)] == ["Album 1", "Album 2", "loose.wav"]
    assert [p.name for p in list_dir(music / "Album 1")] == ["01 First.wav", "02 Second.wav", "10 Tenth.wav"]


def test_list_dir_missing(tmp_path):
    assert list_dir(tmp_path / "missing") == []


def test_tracks_in(music):
    assert [p.name for p in tracks_in(music)] == ["loose.wav"]
    assert [p.name for p in tracks_in(music, recursive=True)] == [
        "01 First.wav", "02 Second.wav", "10 Tenth.wav", "a.wav", "loose.wav"]


def test_read_info_falls_back_to_file_name(tmp_path):
    info = read_info(make_wav(tmp_path / "Some Song.wav", seconds=0.5))
    assert info.title == "Some Song"
    assert info.artist == info.album == ""
    assert abs(info.length - 0.5) < 0.01
    assert info.art is None


def test_read_info_unreadable_file(tmp_path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"not really an mp3")
    info = read_info(path)
    assert info.title == "broken"
    assert info.length == 0.0


def test_riff_info_tags(tmp_path):
    path = make_wav(tmp_path / "t.wav", info={b"INAM": "Title", b"IART": "Artist", b"IPRD": "Album",
                                             b"ICMT": "ignored"})
    assert _riff_info(path) == {"title": "Title", "artist": "Artist", "album": "Album"}
    info = read_info(path)
    assert (info.title, info.artist, info.album) == ("Title", "Artist", "Album")


def test_riff_info_not_riff(tmp_path):
    path = tmp_path / "x.wav"
    path.write_bytes(b"garbage")
    assert _riff_info(path) == {}


def test_id3_tags_and_embedded_art(tmp_path):
    path = make_wav(tmp_path / "t.wav", info={b"INAM": "Riff title", b"IART": "Riff artist"})
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="ID3 title"))
    audio.tags.add(TPE1(encoding=3, text=""))
    audio.tags.add(TALB(encoding=3, text="ID3 album"))
    audio.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=png_bytes((0, 255, 0))))
    audio.save()

    info = read_info(path)
    assert info.title == "ID3 title"
    assert info.artist == "Riff artist"  # empty ID3 value doesn't hide the RIFF one
    assert info.album == "ID3 album"
    assert info.art.getpixel((0, 0)) == (0, 255, 0)


def test_folder_art(tmp_path):
    path = make_wav(tmp_path / "t.wav")
    (tmp_path / "Cover.PNG").write_bytes(png_bytes((0, 0, 255)))
    (tmp_path / "other.png").write_bytes(png_bytes((255, 0, 0)))
    assert read_info(path).art.getpixel((0, 0)) == (0, 0, 255)


def test_bad_folder_art_is_ignored(tmp_path):
    path = make_wav(tmp_path / "t.wav")
    (tmp_path / "cover.jpg").write_bytes(b"not an image")
    assert read_info(path).art is None


def tag(path, album="", artist="", album_artist="", picture=None):
    audio = WAVE(path)
    audio.add_tags()
    if album:
        audio.tags.add(TALB(encoding=3, text=album))
    if artist:
        audio.tags.add(TPE1(encoding=3, text=artist))
    if album_artist:
        audio.tags.add(TPE2(encoding=3, text=album_artist))
    if picture:
        audio.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=picture))
    audio.save()


def test_find_albums(tmp_path):
    root = tmp_path / "Music"
    tag(make_wav(root / "x" / "1.wav"), album="zebra", artist="Someone", picture=png_bytes((0, 255, 0)))
    tag(make_wav(root / "y" / "1.wav"), album="Apple", artist="Track artist", album_artist="Band")
    (root / "y" / "folder.jpg").write_bytes(png_bytes((0, 0, 255)))
    make_wav(root / "Untagged" / "1.wav")
    (root / "Untagged" / "cover.png").write_bytes(png_bytes((255, 0, 0)))
    make_wav(root / "No cover" / "1.wav")
    make_wav(root / ".hidden" / "1.wav")
    (root / ".hidden" / "cover.png").write_bytes(png_bytes())
    (root / "Just art").mkdir()
    (root / "Just art" / "cover.png").write_bytes(png_bytes())

    albums = find_albums(root)
    assert [(a.name, a.artist, a.folder.name) for a in albums] == [
        ("Apple", "Band", "y"), ("Untagged", "", "Untagged"), ("zebra", "Someone", "x")]
    assert [album_art(a).getpixel((0, 0)) for a in albums] == [(0, 0, 255), (255, 0, 0), (0, 255, 0)]


def test_album_art_gone(tmp_path):
    make_wav(tmp_path / "a" / "1.wav")
    (tmp_path / "a" / "cover.png").write_bytes(png_bytes())
    album = find_albums(tmp_path)[0]
    (tmp_path / "a" / "cover.png").write_bytes(b"broken")
    assert album_art(album) is None


def test_album_thumbnail_is_cached(tmp_path):
    make_wav(tmp_path / "a" / "1.wav")
    cover = tmp_path / "a" / "cover.png"
    cover.write_bytes(png_bytes((255, 0, 0), (300, 200)))
    album = find_albums(tmp_path)[0]
    cache = tmp_path / "cache"
    assert album_thumbnail(album, 50).size == (50, 50)  # no cache: just made
    assert not cache.exists()

    assert album_thumbnail(album, 50, cache).getpixel((0, 0)) == (255, 0, 0)
    [saved] = cache.iterdir()
    assert album_thumbnail(album, 50, cache).getpixel((0, 0)) == (255, 0, 0)
    assert list(cache.iterdir()) == [saved]  # read back, not made again

    cover.write_bytes(png_bytes((0, 0, 255), (300, 300)))  # a new cover: a new thumbnail
    assert album_thumbnail(album, 50, cache).getpixel((0, 0)) == (0, 0, 255)
    assert len(list(cache.iterdir())) == 2
    prune_thumbnails([album], 50, cache)
    assert len(list(cache.iterdir())) == 1
    prune_thumbnails([], 50, cache)
    assert list(cache.iterdir()) == []

    cover.unlink()
    assert album_thumbnail(album, 50, cache) is None
