import pytest

from pirateplayer.database import Track
from pirateplayer.playlists import (ALBUM, DAY, MOST_PLAYED, load_all, parse, parse_duration)

NOW = 1_000_000_000.0


def track(root, path, plays=0, last=None, added=NOW - 100 * DAY, **tags):
    tags = {"title": path.rsplit("/", 1)[-1], "artist": "", "album": "", "genre": "",
            "year": ""} | tags
    return Track(root / path, length=200.0, added=added, plays=plays, last_played=last, **tags)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "Music"


@pytest.fixture
def library(root):
    return [
        track(root, "Jazz/Miles/1.flac", plays=10, last=NOW - 200 * DAY, artist="Miles Davis",
              album="Kind of Blue", genre="Jazz"),
        track(root, "Jazz/Miles/2.flac", plays=3, last=NOW - DAY, artist="Miles Davis",
              album="Kind of Blue", genre="Jazz"),
        track(root, "Rock/10 b.mp3", plays=5, last=NOW - 2 * DAY, artist="Band", album="Loud",
              title="Zebra"),
        track(root, "Rock/2 a.mp3", artist="Band", album="Loud", added=NOW - DAY,
              title="Apple"),
    ]


def names(paths):
    return [p.name for p in paths]


@pytest.mark.parametrize("text,seconds", [("30 days", 30 * DAY), ("1 day", DAY), ("7", 7 * DAY),
                                          ("2 weeks", 14 * DAY), ("12 hours", DAY / 2),
                                          ("6 Months", 180 * DAY), ("1 year", 365 * DAY)])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "soon", "3 fortnights", "-1 days"])
def test_parse_duration_rejects(text):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_parse():
    pl = parse("""
        # a comment
        Sort: Most Played
        limit = 50
        period: 30 days   # inline comment
        min_plays: 2
        artist: AC/DC
        artist: Queen
        folder: Rock: Classic
    """, "Top")
    assert pl.name == "Top"
    assert pl.sort == MOST_PLAYED
    assert pl.limit == 50
    assert pl.period == 30 * DAY
    assert pl.min_plays == 2
    assert pl.filters == {"artist": ["AC/DC", "Queen"], "folder": ["Rock: Classic"]}
    assert pl.problems == []


def test_parse_skips_bad_lines():
    pl = parse("sort: loudest\nlimit: lots\ncolour: red\njust words\nlimit: 5\nname: Five", "x")
    assert pl.sort == ALBUM
    assert pl.limit == 5
    assert pl.name == "Five"
    assert len(pl.problems) == 4
    assert pl.problems[0].startswith("line 1:")


def test_most_played(library, root):
    pl = parse("sort: most played\nmin plays: 1", "x")
    assert names(pl.select(library, root, NOW)) == ["1.flac", "10 b.mp3", "2.flac"]


def test_most_played_in_a_period_uses_the_plays_passed_in(library, root):
    pl = parse("sort: most played\nperiod: 30 days\nlimit: 2", "x")
    assert pl.since(NOW) == NOW - 30 * DAY
    library[0].plays = 0  # as Database.tracks(since) would count them
    assert names(pl.select(library, root, NOW)) == ["10 b.mp3", "2.flac"]


def test_album_order_is_natural(library, root):
    assert names(parse("", "x").select(library, root, NOW)) == ["2 a.mp3", "10 b.mp3", "1.flac",
                                                               "2.flac"]


def test_filters(library, root):
    def pick(text):
        return names(parse(text, "x").select(library, root, NOW))

    assert pick("artist: miles") == ["1.flac", "2.flac"]
    assert pick("genre: jazz\ngenre: blues\nplayed in: 1 week") == ["2.flac"]
    assert pick("not played in: 1 month") == ["2 a.mp3", "1.flac"]
    assert pick("max plays: 0") == ["2 a.mp3"]
    assert pick("added in: 1 week") == ["2 a.mp3"]
    assert pick("folder: rock") == ["2 a.mp3", "10 b.mp3"]
    assert pick("folder: rock\nartist: miles") == []
    assert pick("sort: recently played\nlimit: 2") == ["2.flac", "10 b.mp3"]
    assert pick("sort: least recently played") == ["2 a.mp3", "1.flac", "10 b.mp3", "2.flac"]
    assert pick("sort: recently added\nlimit: 1") == ["2 a.mp3"]
    assert pick("sort: title") == ["1.flac", "2.flac", "2 a.mp3", "10 b.mp3"]  # Apple, Zebra
    assert sorted(pick("sort: random")) == sorted(pick(""))


def test_load_all(tmp_path):
    (tmp_path / "10 Ten.txt").write_text("sort: random")
    (tmp_path / "2 Two.txt").write_text("name: Second\nbad line")
    (tmp_path / "README.txt").write_text("explains things: yes")
    (tmp_path / ".hidden.txt").write_text("")
    (tmp_path / "notes.md").write_text("")
    playlists = load_all(tmp_path)
    assert [p.name for p in playlists] == ["Second", "10 Ten"]
    assert playlists[0].problems
    assert load_all(tmp_path / "missing") == []
