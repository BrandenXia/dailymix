import json
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import tomllib
import unittest
from dailymix.models import Track, Event
from dailymix.matching import Resolver
from dailymix.features import build_features
from dailymix.generator import generate
from dailymix.state import Store
from dailymix.sources.lastfm import parse_page
from dailymix.cli import main

DAY = date(2026, 10, 5)
CUTOFF = datetime(2026, 10, 5, tzinfo=timezone.utc)
CONFIG = tomllib.loads(Path("config.toml").read_text())


def catalog():
    return [Track(str(i), f"Song {i}", f"Artist {i // 2}", f"Album {i}",
                  ("JP, A", "PM, R", "CN", "EN", "JP, V")[i % 5],
                  plays=i % 12, added="2026-09-10T00:00:00Z") for i in range(80)]


class CoreTests(unittest.TestCase):
    def test_matching_preserves_versions_and_ambiguity(self):
        tracks = [Track("1", "Song", "Artist", "Album"), Track("2", "Song - Instrumental", "Artist", "Album"),
                  Track("3", "Song", "Artist", "Other")]
        resolver = Resolver(tracks)
        self.assertEqual(resolver.resolve(Event("artist", "Ｓｏｎｇ", "Album", 1)).track_id, "1")
        self.assertEqual(resolver.resolve(Event("Artist", "Song", "", 1)).status, "ambiguous")
        self.assertEqual(resolver.resolve(Event("Artist", "Song", "Different", 1)).status, "ambiguous")
        self.assertEqual(resolver.resolve(Event("Artist", "Song Live", "", 1)).status, "absent")
        self.assertEqual(resolver.resolve(Event("Artist", "Song - Instrumental", "Album", 1)).track_id, "2")

    def test_external_song_affinity_does_not_become_local_plays(self):
        tracks = [Track("1", "Local", "Artist")]
        features, statuses = build_features(tracks, [Event("Artist", "Spotify song", "", 100)], CUTOFF)
        self.assertEqual(features["1"].artist_plays, 1)
        self.assertEqual(features["1"].scrobbles, 0)
        self.assertIsNone(features["1"].last_played)
        self.assertEqual(statuses, {"absent": 1})

    def test_future_events_ignored(self):
        features, _ = build_features([Track("1", "Local", "Artist")],
            [Event("Artist", "Local", "", int(CUTOFF.timestamp()))], CUTOFF)
        self.assertEqual(features["1"].scrobbles, 0)

    def test_determinism_diversity_and_local_eligibility(self):
        tracks = catalog()
        features, _ = build_features(tracks, [], CUTOFF)
        first = generate(tracks, features, DAY, CONFIG, {}, timezone.utc)
        second = generate(list(reversed(tracks)), features, DAY, CONFIG, {}, timezone.utc)
        self.assertEqual(first, second)
        self.assertEqual(first["actual_size"], 30)
        self.assertEqual(len({t["id"] for t in first["tracks"]}), 30)
        from collections import Counter
        self.assertLessEqual(max(Counter(t["artist"] for t in first["tracks"]).values()), 2)
        third = generate(tracks, features, date(2026, 10, 6), CONFIG, {DAY.isoformat(): first}, timezone.utc)
        self.assertFalse({t["id"] for t in first["tracks"]} & {t["id"] for t in third["tracks"]})

    def test_empty_and_exhausted_catalog_terminate(self):
        self.assertEqual(generate([], {}, DAY, CONFIG, {}, timezone.utc)["shortfall"], 30)
        tracks = [Track("1", "Song", "Artist")]
        features, _ = build_features(tracks, [], CUTOFF)
        self.assertEqual(generate(tracks, features, DAY, CONFIG, {}, timezone.utc)["actual_size"], 1)

    def test_repeated_import_and_accounts(self):
        store = Store(":memory:")
        event = Event("Artist", "Song", "Album", 10, account="one")
        self.assertEqual(store.import_events([event, event]), 1)
        self.assertEqual(store.import_events([event]), 0)
        store.import_events([Event("Artist", "Song", "Album", 10, account="two")])
        self.assertEqual(len(store.events()), 2)
        self.assertEqual(len(store.events("one")), 1)
        with store.transaction():
            store.save_mix("2026-10-05", {"tracks": [{"id": "1"}]})
        self.assertEqual(store.mix("2026-10-05")["tracks"][0]["id"], "1")
        store.close()

    def test_lastfm_skips_now_playing_and_reads_completed_events(self):
        payload = {"recenttracks": {"track": [
            {"artist": {"#text": "Artist"}, "name": "Live", "@attr": {"nowplaying": "true"}},
            {"artist": {"#text": "Artist"}, "name": "Song", "album": {"#text": "Album"}, "date": {"uts": "10"}}],
            "@attr": {"totalPages": "3"}}}
        events, pages = parse_page(payload, "user")
        self.assertEqual(pages, 3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].account, "user")

    def test_cooldowns_enabled_and_album_limits(self):
        tracks = [Track("1", "Song", "Artist", "Album"),
                  Track("2", "Other", "Artist", "Album"),
                  Track("3", "Disabled", "Else", enabled=False),
                  Track("4", "Yesterday", "Else", last_played="2026-10-04T00:00:00Z")]
        features, _ = build_features(tracks, [], CUTOFF)
        mix = generate(tracks, features, DAY, CONFIG, {}, timezone.utc)
        self.assertEqual(mix["actual_size"], 1)
        self.assertIn(mix["tracks"][0]["id"], {"1", "2"})

    def test_lastfm_resume_keeps_fixed_time_window(self):
        from unittest.mock import patch
        from io import BytesIO
        from urllib.parse import urlparse, parse_qs
        from dailymix.sources.lastfm import fetch_history
        def response(url, timeout):
            args = parse_qs(urlparse(url).query)
            self.assertEqual(args["to"], ["1000"])
            self.assertEqual(args["from"], ["100"])
            self.assertEqual(args["page"], ["3"])
            return BytesIO(json.dumps({"recenttracks": {"track": [], "@attr": {"totalPages": "8"}}}).encode())
        with patch("dailymix.sources.lastfm.urlopen", side_effect=response):
            events, progress = fetch_history("user", "test-key", 100, 1, 1000, 3)
        self.assertEqual(events, [])
        self.assertFalse(progress["complete"])
        self.assertEqual(progress["next_page"], 4)
        self.assertEqual(progress["pages_fetched"], 1)

    def test_cli_freezes_daily_selection(self):
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "catalog.json"
            snapshot.write_text(json.dumps({"schema_version": 1, "tracks": [asdict(t) for t in catalog()]}))
            output = root / "mix.json"
            args = ["--state", str(root / "state.sqlite3"), "generate", "--catalog", str(snapshot),
                    "--date", DAY.isoformat(), "--output", str(output)]
            main(args)
            first = json.loads(output.read_text())
            snapshot.write_text("{}")
            main(args)
            second = json.loads(output.read_text())
            self.assertTrue(second.pop("cached"))
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
