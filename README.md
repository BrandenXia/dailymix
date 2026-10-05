# Daily Mix

A local, dependency-free Python CLI that reads Apple Music, previews deterministic
recommendations, and retains optional Last.fm history. Requires Python 3.11+;
reading Music requires macOS and permission to automate Music.

Reading, previewing, and generating do not modify Music. Playlist writes require
`publish` or `run --publish`; they update only a dedicated managed user playlist.
`generate` freezes a selection in the local database only. Scheduling exports a
launchd job without installing or starting it.

## Quick start

Run from the project directory without installation:

```sh
export PYTHONPATH=src
python3 -m dailymix snapshot --output work/music-snapshot.json
python3 -m dailymix inspect --catalog work/music-snapshot.json
python3 -m dailymix preview --catalog work/music-snapshot.json --output work/preview.json
python3 -m dailymix generate --catalog work/music-snapshot.json --output work/daily-mix.json
```

`snapshot` reads Music through a
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
- `service.py`: frozen selection and reproducible input fingerprints.
- `publication.py` and `sources/publish.js`: preflight, backup and managed playlist updates.
- `schedule.py`: portable per-user launchd job export.
- `cli.py`: orchestration, validation and output.

Generation uses a SQLite transaction to serialize simultaneous saves. The saved
mix includes display metadata and reasons, so it is reviewable without Music.
The playlist writer consumes this saved selection and records publication
separately, so a failed publication can retry without regenerating.
Future sources should emit `Event` records; they should not alter the generator
or require every event to match a local track.

Private snapshots, previews and history are excluded from source control.


## Daily run and playlist publication

Read the current Music library and freeze today's mix with one command:

```sh
python3 -m dailymix run --output outputs/daily-mix.json
```

Optionally add `--sync-lastfm` to refresh up to ten pages of history from the
last 45 days before freezing. Missing credentials or network failures produce
warnings and use cached history. This refresh is not a full-history backfill.
An already frozen day reuses its selection and skips refresh and catalog reads.

Inspect publication without writing to Music:

```sh
python3 -m dailymix publish --dry-run --output outputs/publication-plan.json
```

To actually create/update the managed playlist:

```sh
python3 -m dailymix publish
# Or perform the daily generation and publication together:
python3 -m dailymix run --publish
```

The default destination is `Daily Mix`; choose another with `--playlist NAME`.
If an unrelated playlist already has that name, publication refuses to overwrite
it. A playlist created by this tool has description `dailymix:managed:v1`.
Don't remove that description if you want the tool to maintain it.

Publication validates that every selected track still exists, stages the new
contents in a temporary playlist, and verifies membership and order. Existing
managed playlists keep their persistent ID. Replacement removes references from
that user playlist, never tracks from the library. A caught replacement error
attempts to restore its old contents. Every write first saves the previous IDs in
`state/publication-backups/`; interruption or timeout may require inspecting
those backups and the playlist before retrying. These backups are retained.

A process lock prevents concurrent writes. A retry verifies Music's actual
contents, even when the database already records publication. An unchanged
playlist does not get cleared and rebuilt. Empty mixes are never published.
Generation is committed before publication, so publication failure leaves the
selected mix intact. Publication history is separate from the mix payload;
`preview`/`run` output includes a `publications` list.

## Daily schedule

Export a job to generate and publish at 03:00 each day:

```sh
python3 -m dailymix schedule --publish --hour 3 --output outputs/com.brandenxia.dailymix.plist
```

The job has absolute interpreter/config/state paths and a source import path.
Keep the checkout and interpreter at those paths, or regenerate the file after
moving them. Omit `--publish` for local-only daily generation. Exporting a job
does not activate it.

After reviewing the job, install and load it in your user session:

```sh
mkdir -p "$HOME/Library/LaunchAgents"
cp outputs/com.brandenxia.dailymix.plist "$HOME/Library/LaunchAgents/com.brandenxia.dailymix.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.brandenxia.dailymix.plist"
```

Unload it before replacing an already loaded job:

```sh
launchctl bootout "gui/$(id -u)/com.brandenxia.dailymix"
```

Launchd uses the Mac's local clock for the schedule; `--timezone` controls the
mix date boundary, not launchd's scheduling timezone. The job runs in your logged-in
user session. It does not wake a sleeping Mac; calendar jobs missed during sleep
run on wake, as described in Apple's
[Scheduling Timed Jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html).
It has no immediate run-on-load behavior. First run the CLI interactively to grant
Music automation access; macOS may require access for the scheduled context too.

Logs go to `state/logs/daily.log` and `state/logs/daily-error.log`. The exported
job uses cached Last.fm history; it contains no API key and does not inherit your
shell's environment. Sync history interactively before generation. Nothing here
triggers Finder or changes iPhone synchronization settings.

## Validation scope

The adapter and publication preflight have been exercised against the actual
728-track library. Playlist creation/replacement and rollback are covered using
a simulated Music interface, including protection against deleting library
tracks. Actual playlist writes and launchd activation have not been performed
against the user's library. Verify the first actual publication before enabling
the schedule.

## Verification

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The publisher bridge test uses Node when available; the CLI itself needs only Python and macOS.
