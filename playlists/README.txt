Smart playlists
===============

Each .txt file in this folder is a playlist, named after the file, and shows up under
Playlists on the player. It picks tracks from your music using the rules in the file, every
time you play it, so it keeps up with what you add and listen to. Edit them on a computer
(over USB, they're in the Playlists drive) or on the Pi with nano. This file is ignored.

Each line is "setting: value". Lines starting with # are notes for you and are ignored.
Settings you leave out don't filter anything. For example:

    # The 50 tracks I've played most this month
    sort: most played
    period: 30 days
    min plays: 1
    limit: 50

Settings
--------

sort            How to order the tracks (default: album). One of:
                  most played, least played, recently played, least recently played,
                  recently added, random, album (artist, then album, then track), title
limit           At most this many tracks, e.g. 50
period          Only count plays this recent, e.g. 30 days. Affects min plays, max plays
                and the most/least played order. Without it, every play counts.
min plays       Only tracks played at least this many times (in the period)
max plays       Only tracks played at most this many times; 0 for never played
played in       Only tracks last played within this long, e.g. 1 week
not played in   Only tracks not played for this long, or never, e.g. 6 months
added in        Only tracks added to the player within this long, e.g. 2 weeks
artist          Only tracks whose artist contains this text, ignoring case
album           The same for the album name
genre           The same for the genre
folder          Only tracks under this folder of your music, e.g. Jazz/Miles Davis
name            The name to show, if not the file name

Lengths of time are a number and one of hours, days, weeks, months, years ("30 days",
"1 year"). A bare number is days.

You can give artist, album, genre and folder more than once: a track matches if it matches
any of them. Different settings must all match. For example, this plays jazz or blues
you haven't heard for a year:

    genre: jazz
    genre: blues
    not played in: 1 year
    sort: random

A track counts as played once it has played for half its length or 4 minutes, whichever is
sooner. Play counts start from when the player first ran with this feature.

If a line doesn't make sense the player ignores it, marks the playlist "Has errors", and
says why in its log (journalctl -u pirateplayer).
