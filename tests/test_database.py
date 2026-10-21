import os
import threading

from pirateplayer.database import Database, Indexer, open_database, sync

from .conftest import make_wav


def test_sync_indexes_tags_and_skips_hidden(tmp_path, music):
    make_wav(music / "tagged.wav", info={b"INAM": "Song", b"IART": "Band", b"IPRD": "Record"})
    make_wav(music / ".hidden" / "x.wav")
    (music / "notes.txt").write_text("not music")
    db = Database(tmp_path / "lib.db")
    assert sync(db, music) == 6
    tracks = {t.path.name: t for t in db.tracks()}
    assert set(tracks) == {"01 First.wav", "02 Second.wav", "10 Tenth.wav", "a.wav",
                           "loose.wav", "tagged.wav"}
    t = tracks["tagged.wav"]
    assert (t.title, t.artist, t.album) == ("Song", "Band", "Record")
    assert t.length > 0
    assert tracks["loose.wav"].title == "loose"
    assert db.track_count() == 6


def test_sync_only_rereads_changed_files_and_drops_missing(tmp_path, music, monkeypatch):
    from pirateplayer import database

    db = Database(tmp_path / "lib.db")
    sync(db, music)
    added = {t.path.name: t.added for t in db.tracks()}

    read = []
    real = database.read_info
    monkeypatch.setattr(database, "read_info", lambda p, art: read.append(p.name) or real(p, art))
    make_wav(music / "loose.wav", info={b"INAM": "Retitled"})
    os.utime(music / "loose.wav", (1, 1))  # a different mtime, whatever the clock resolution
    (music / "Album 2" / "Disc 1" / "a.wav").unlink()
    assert sync(db, music) == 4
    assert read == ["loose.wav"]
    tracks = {t.path.name: t for t in db.tracks()}
    assert "a.wav" not in tracks
    assert tracks["loose.wav"].title == "Retitled"
    assert tracks["loose.wav"].added == added["loose.wav"]  # still when it first arrived


def test_stopped_sync_keeps_tracks_it_did_not_reach(tmp_path, music):
    db = Database(tmp_path / "lib.db")
    sync(db, music)
    stop = threading.Event()
    stop.set()
    assert sync(db, music, stop=stop) == 0
    assert db.track_count() == 5


def test_plays_are_counted_and_survive_reindexing(tmp_path, music):
    db = Database(tmp_path / "lib.db")
    sync(db, music)
    track = music / "loose.wav"
    db.record_play(track, at=100)
    db.record_play(track, at=200)
    db.record_play(track, at=300)
    assert db.play_count(track) == 3
    assert db.play_count(track, since=200) == 2
    t = next(t for t in db.tracks(since=250) if t.path == track)
    assert (t.plays, t.last_played) == (1, 300)

    moved = music / "elsewhere.wav"
    track.rename(moved)
    sync(db, music)
    assert db.play_count(track) == 3  # the history is kept...
    moved.rename(track)
    sync(db, music)
    assert next(t for t in db.tracks() if t.path == track).plays == 3  # ...for when it's back


def test_unplayed_tracks_have_no_plays(tmp_path, music):
    db = Database(tmp_path / "lib.db")
    sync(db, music)
    assert all(t.plays == 0 and t.last_played is None for t in db.tracks())


def test_database_persists(tmp_path, music):
    db = Database(tmp_path / "lib.db")
    sync(db, music)
    db.record_play(music / "loose.wav")
    db.close()
    db = Database(tmp_path / "lib.db")
    assert db.track_count() == 5
    assert db.play_count(music / "loose.wav") == 1


def test_broken_database_is_set_aside(tmp_path):
    path = tmp_path / "lib.db"
    path.write_bytes(b"this is not a database" * 100)
    db = open_database(path)
    assert db is not None
    assert db.track_count() == 0
    assert (tmp_path / "lib.db.broken").exists()


def test_unopenable_database_is_none(tmp_path):
    (tmp_path / "file").write_text("")
    assert open_database(tmp_path / "file" / "lib.db") is None


def test_indexer_runs_in_the_background_once_at_a_time(tmp_path, music):
    db = Database(tmp_path / "lib.db")
    indexer = Indexer(db, music)
    assert not indexer.running
    assert indexer.start()
    indexer.join()
    assert not indexer.running
    assert indexer.last_count == indexer.done == indexer.total == 5
    assert db.track_count() == 5
