from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest
from dailymix.features import Features, build_features
from dailymix.generator import generate
from dailymix.models import Event, Track
from dailymix.scoring import settings, utility
from dailymix.service import select_mix
from dailymix.state import Store
from test_core import CONFIG, catalog

DAY = date(2026, 10, 8)
CUTOFF = datetime(2026, 10, 8, tzinfo=timezone.utc)


class RecommendationTests(unittest.TestCase):
    def test_recent_artist_signal_favors_current_over_old_listening(self):
        tracks = [Track('a', 'Local', 'Current'), Track('b', 'Local', 'Past')]
        events = [Event('Current', 'External', '', int((CUTOFF-timedelta(days=2)).timestamp())),
                  Event('Past', 'External', '', int((CUTOFF-timedelta(days=200)).timestamp()))]
        f, _ = build_features(tracks, events, CUTOFF)
        self.assertGreater(f['a'].recent_artist_plays, f['b'].recent_artist_plays)
        self.assertEqual(f['a'].scrobbles, 0)
        self.assertIsNone(f['a'].last_played)
        current, _ = utility(tracks[0], f['a'], 'exploration', 200, float('inf'), 0, 0, settings(CONFIG))
        past, _ = utility(tracks[1], f['b'], 'exploration', 200, float('inf'), 0, 0, settings(CONFIG))
        self.assertGreater(current, past)

    def test_features_are_event_order_invariant_and_cutoff_aware(self):
        tracks = [Track('a', 'Song', 'Artist')]
        events = [Event('Artist', 'Song', '', int((CUTOFF-timedelta(days=d)).timestamp())) for d in (1,2,20,200)]
        events.append(Event('Artist', 'Song', '', int(CUTOFF.timestamp())))
        first, _ = build_features(tracks, events, CUTOFF)
        second, _ = build_features(tracks, list(reversed(events)), CUTOFF)
        self.assertEqual(first, second)
        self.assertEqual(first['a'].plays_yesterday, 1)
        self.assertEqual(first['a'].scrobbles, 4)

    def test_underexplored_pool_adapts_to_a_well_played_library(self):
        tracks = [Track(str(i), str(i), str(i), plays=10+i, added='2020-01-01T00:00:00Z') for i in range(40)]
        features, _ = build_features(tracks, [], CUTOFF)
        mix = generate(tracks, features, DAY, CONFIG, {}, timezone.utc)
        self.assertGreaterEqual(mix['pool_counts']['exploration'], 6)
        self.assertTrue(all(t['pool']=='exploration' for t in mix['tracks'] if t['requested_pool']=='exploration'))
        self.assertGreater(mix['adaptive_thresholds']['exploration'], 2)
        selected = [t for t in mix['tracks'] if t['pool']=='exploration']
        self.assertTrue(all(t['reason']['music_plays'] <= mix['adaptive_thresholds']['exploration'] for t in selected))

    def test_recent_content_changes_targets_and_external_songs_do_not_inherit_tags(self):
        tracks = [Track(str(i), f'Song {i}', f'Artist {i}', grouping='JP, A' if i<20 else 'EN, R') for i in range(40)]
        features, _ = build_features(tracks, [], CUTOFF)
        baseline = generate(tracks, features, DAY, CONFIG, {}, timezone.utc)
        events = [Event('Artist 21', 'Song 21', '', int((CUTOFF-timedelta(days=2, seconds=i)).timestamp())) for i in range(20)]
        features, _ = build_features(tracks, events, CUTOFF)
        recent = generate(tracks, features, DAY, CONFIG, {}, timezone.utc)
        self.assertGreater(recent['association_targets']['R'], baseline['association_targets']['R'])
        self.assertGreater(recent['primary_targets']['EN'], baseline['primary_targets']['EN'])
        external = [Event('Artist 21', 'Spotify-only', '', e.timestamp) for e in events]
        features, _ = build_features(tracks, external, CUTOFF)
        absent = generate(tracks, features, DAY, CONFIG, {}, timezone.utc)
        self.assertEqual(absent['association_targets'], baseline['association_targets'])
        self.assertGreater(features['21'].recent_artist_plays, 0)

    def test_yesterday_feedback_requires_confirmed_listening(self):
        tracks = [Track(str(i), f'Song {i}', f'Artist {i}', grouping='JP, A' if i<20 else 'PM, R') for i in range(40)]
        history = {'2026-10-07': {'tracks': [{'id': '21'}]}}
        features, _ = build_features(tracks, [], CUTOFF)
        unplayed = generate(tracks, features, DAY, CONFIG, history, timezone.utc)
        self.assertEqual(unplayed['previous_mix_feedback']['heard'], 0)
        event = Event('Artist 21', 'Song 21', '', int((CUTOFF-timedelta(hours=12)).timestamp()))
        features, _ = build_features(tracks, [event], CUTOFF)
        heard = generate(tracks, features, DAY, CONFIG, history, timezone.utc)
        self.assertEqual(heard['previous_mix_feedback']['heard_ids'], ['21'])
        self.assertFalse(heard['previous_mix_feedback']['playlist_origin_known'])
        without_mix = generate(tracks, features, DAY, CONFIG, {}, timezone.utc)
        self.assertGreater(heard['association_targets']['R'], without_mix['association_targets']['R'])
        self.assertTrue(all(t['id'] != '21' for t in heard['tracks']))

    def test_previous_mix_artist_feedback_and_exposure_are_separate(self):
        track = Track('a', 'Song', 'Artist', plays=20)
        feature = Features()
        baseline, _ = utility(track, feature, 'familiar', 200, 40, 0, 0, settings(CONFIG))
        feedback, components = utility(track, feature, 'familiar', 200, 40, 1, 0, settings(CONFIG))
        exposed, exposure_components = utility(track, feature, 'familiar', 200, 40, 0, 5, settings(CONFIG))
        self.assertGreater(feedback, baseline)
        self.assertLess(exposed, baseline)
        self.assertGreater(components['previous_mix_affinity'], 0)
        self.assertLess(exposure_components['mix_exposure_penalty'], 0)

    def test_invalid_profile_settings_are_rejected(self):
        cfg = deepcopy(CONFIG)
        cfg['recommendation']['recent_profile_weight'] = 1
        cfg['recommendation']['long_profile_weight'] = 1
        with self.assertRaises(ValueError):
            generate([], {}, DAY, cfg, {}, timezone.utc)

    def test_fresh_preview_leaves_frozen_mix_and_publication_untouched(self):
        store = Store(':memory:')
        frozen = {'date': DAY.isoformat(), 'timezone': 'UTC', 'algorithm_version': '0.1.0', 'tracks': [{'id':'old'}]}
        with store.transaction():
            store.save_mix(DAY.isoformat(), frozen)
        store.record_publication(DAY.isoformat(), 'Daily Mix', 'playlist', 'then')
        def loader():
            return {'schema_version':1, 'tracks':[asdict(t) for t in catalog()]}
        mix = select_mix(store, DAY, timezone.utc, 'UTC', CONFIG, loader, reuse_saved=False)
        self.assertEqual(mix['algorithm_version'], '0.2.0')
        self.assertEqual(store.mix(DAY.isoformat()), frozen)
        self.assertEqual(len(store.publications(DAY.isoformat())), 1)
        with self.assertRaises(ValueError):
            select_mix(store, DAY, timezone.utc, 'UTC', CONFIG, loader, save=True, reuse_saved=False)
        store.close()
