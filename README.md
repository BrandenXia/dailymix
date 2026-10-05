# Daily Mix

A local, dependency-free Python CLI that reads Apple Music, previews deterministic
recommendations, and retains optional Last.fm history. Requires Python 3.11+;
reading Music requires macOS and permission to automate Music.

This version never changes Music metadata or playlists. `generate` freezes a
selection in the local database only. Playlist publication and scheduling are
future steps.

## Quick start

Run from the project directory without installation:

```sh
export PYTHONPATH=src
python3 -m dailymix snapshot --output work/music-snapshot.json
python3 -m dailymix inspect --catalog work/music-snapshot.json
python3 -m dailymix preview --catalog work/music-snapshot.json --output work/preview.json
python3 -m dailymix generate --catalog work/music-snapshot.json --output work/daily-mix.json
```

Create `work/` first if using another checkout. `snapshot` reads Music through a
bundled JXA script. `preview` does not save a daily selection, but returns an
already frozen selection if one exists. `generate` saves that day's selection
once; subsequent runs return it even if the input catalog changes. Both report
selection reasons, source matching counts, quota fallbacks, and shortfalls.

`--date YYYY-MM-DD` chooses a day; `--timezone` defaults to
`America/Indiana/Indianapolis`. Day boundaries use midnight in that zone. A saved
day cannot be reused under a different zone. Historical runs use today's catalog
aggregates, not a reconstructed historical library. For exact replay retain the
original snapshot, config, database, and algorithm version.

Global options precede the command:

```sh
python3 -m dailymix --config config.toml --state state/history.sqlite3 preview --catalog work/music-snapshot.json
```

## Last.fm

Set `lastfm.username` in `config.toml` or supply `--username`. Supply the API key
through `LASTFM_API_KEY` in your environment; don't put it in configuration,
source control, or command arguments. The read-only adapter uses Last.fm's
[user.getRecentTracks](https://www.last.fm/api/show/user.getRecentTracks) method.
It makes no scrobble or account changes.

```sh
python3 -m dailymix sync-lastfm --username YOUR_USERNAME --max-pages 10
```

Imports have a fixed upper timestamp and bounded page count. If `complete` is
false, continue with the reported `next_page`, `from`, and `to` values:

```sh
python3 -m dailymix sync-lastfm --username YOUR_USERNAME --start-page 11 --from-timestamp 0 --to-timestamp UPPER_TIMESTAMP --max-pages 10
```

Imports are idempotent. No automatic polling is configured. For incremental
imports use `--from-timestamp` with a small overlap; duplicate events are ignored.
A failed fetch does not commit a partial batch: retry that batch. Last.fm can
revise older history, so fixed pagination is best-effort against such revisions.
Stored events are namespaced by account. Set the configured username or use
`preview --username YOUR_USERNAME` to select an account; without either, events
from all imported accounts are used.

You can also import a saved API JSON response:

```sh
python3 -m dailymix import-lastfm work/lastfm-page.json --username YOUR_USERNAME
```

Or import normalized JSON:

```json
{"events": [{"artist": "Artist", "title": "Song", "album": "Album", "timestamp": 1791100000}]}
```

Now-playing entries are skipped. Source is Last.fm, and the original playback
service is unknown. Spotify-only songs remain valid history without a local ID.

## Matching and recommendation rules

The resolver normalizes Unicode, whitespace and case, but preserves punctuation
and recording/version qualifiers. It first checks artist/title/album; unique
artist/title matches are permitted when the event has no album. Conflicting
albums and duplicate candidates are ambiguous rather than guessed. Matching is
recomputed against the current catalog, so newly imported songs can match older
events.

Manual overrides are a JSON array, supplied with `--overrides PATH`:

```json
[{"artist": "Artist", "title": "Song", "album": "External Album", "track_id": "MUSIC_PERSISTENT_ID"}]
```

Matched events provide song recency and play evidence. All events can provide a
small exact-artist affinity boost. Music counts and Last.fm counts are never
summed: the larger count is used for pool eligibility, with both kept in the
explanation. Artist affinity never updates song play counts or recency. Music
skips and ratings remain separate signals. Events on or after the generation
day's midnight are excluded; absent scrobbles are not dislikes.

Configuration defines pool quotas, tag balance, cooldowns and diversity limits.
Grouping is parsed as comma-separated uppercase tags while preserving the raw
value. JP/PM/CN/EN are configurable primary categories; A/R/V are association
codes. Missing or conflicting primary tags become unknown. Tag meanings remain
assumptions from the library inspection, not inferred mood or energy labels.

Selection scans bounded candidate sets using stable date/ID hashes and soft tag
deficits. It keeps artist and album caps, avoids already selected tracks and
recent mixes, and prefers different adjacent artists where possible. If a pool
cannot supply a slot, it tries the other pools in configured order, then all
eligible local tracks. It reports a shortfall instead of relaxing cooldowns or
hard caps. Configured pool quotas must total the mix size; adjust tag targets
when changing size. This is an initial heuristic, to be tuned from previews.

## Architecture

- `sources/apple_music.py` and `music.js`: read-only catalog adapter.
- `sources/lastfm.py`: bounded history fetch and response parsing.
- `models.py`: independent catalog and listening event models.
- `matching.py`: conservative resolver and explicit overrides.
- `features.py`: source-independent, cutoff-aware listening features.
- `generator.py`: pure deterministic selection; no external I/O.
- `state.py`: SQLite event history and frozen daily mixes.
- `cli.py`: orchestration, validation, input fingerprints and output.

Generation uses a SQLite transaction to serialize simultaneous saves. The saved
mix includes display metadata and reasons, so it is reviewable without Music.
A future playlist writer should consume this saved selection and keep its own
publication status, allowing failed publication to retry without regenerating.
Future sources should emit `Event` records; they should not alter the generator
or require every event to match a local track.

Private snapshots, previews and history are excluded from source control.

## Verification

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
