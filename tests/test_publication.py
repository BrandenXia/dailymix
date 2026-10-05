from datetime import date, timezone
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from dailymix.publication import publish_mix, publication_lock
from dailymix.schedule import write_schedule
from dailymix.service import select_mix
from dailymix.state import Store
from test_core import CONFIG, catalog
from dataclasses import asdict


class PublicationTests(unittest.TestCase):
    def test_dry_run_never_mutates_or_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'history.sqlite3')
            requests = []
            def bridge(request):
                requests.append(request)
                return {'playlist_id': None, 'previous_ids': [], 'ids': request['ids'], 'action': 'create'}
            result = publish_mix(store, Path(directory) / 'history.sqlite3',
                                 {'date': '2026-10-05', 'tracks': [{'id': 'a'}]}, 'Daily Mix', True, bridge)
            self.assertTrue(result['dry_run'])
            self.assertEqual(len(requests), 1)
            self.assertTrue(requests[0]['dry_run'])
            self.assertEqual(store.publications('2026-10-05'), [])
            self.assertFalse((Path(directory) / 'publication-backups').exists())
            store.close()

    def test_backup_precedes_mutation_and_failure_retries_frozen_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.sqlite3'
            store = Store(path)
            mix = {'date': '2026-10-05', 'tracks': [{'id': 'a'}]}
            with store.transaction():
                store.save_mix(mix['date'], mix)
            requests = []
            failed = True
            def bridge(request):
                requests.append(request)
                if request['dry_run']:
                    return {'playlist_id': 'p', 'previous_ids': ['old'], 'ids': request['ids']}
                backup = list((path.parent / 'publication-backups').glob('*.json'))
                self.assertTrue(backup)
                self.assertEqual(json.loads(backup[0].read_text())['previous_ids'], ['old'])
                if failed:
                    raise RuntimeError('simulated failure')
                return {'playlist_id': 'p', 'verified': True, 'ids': request['ids']}
            with self.assertRaisesRegex(RuntimeError, 'simulated failure'):
                publish_mix(store, path, mix, 'Daily Mix', bridge=bridge)
            self.assertEqual(store.publications(mix['date']), [])
            self.assertEqual(store.mix(mix['date']), mix)
            failed = False
            publish_mix(store, path, mix, 'Daily Mix', bridge=bridge)
            self.assertEqual(len(store.publications(mix['date'])), 1)
            self.assertEqual(requests[-1]['ids'], ['a'])
            store.close()

    def test_publication_lock_and_empty_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.sqlite3'
            store = Store(path)
            with publication_lock(path):
                with self.assertRaisesRegex(RuntimeError, 'Another playlist'):
                    with publication_lock(path):
                        pass
            with self.assertRaises(ValueError):
                publish_mix(store, path, {'tracks': []}, 'Daily Mix')
            store.close()

    def test_schedule_has_absolute_paths_and_no_embedded_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daily.plist'
            state = Path(directory) / 'state.sqlite3'
            result = write_schedule(path, 'config.toml', state, 3, 15, True)
            payload = plistlib.loads(path.read_bytes())
            self.assertEqual(payload['StartCalendarInterval'], {'Hour': 3, 'Minute': 15})
            self.assertIn('--publish', payload['ProgramArguments'])
            self.assertTrue(Path(payload['ProgramArguments'][0]).is_absolute())
            source_path = Path(payload['EnvironmentVariables']['PYTHONPATH'])
            self.assertTrue(source_path.is_absolute())
            self.assertTrue((source_path / 'dailymix' / '__init__.py').is_file())
            self.assertNotIn('LASTFM_API_KEY', payload['EnvironmentVariables'])
            self.assertFalse(result['installed'])
            self.assertFalse(payload['RunAtLoad'])

    def test_daily_retry_does_not_read_music_again(self):
        store = Store(':memory:')
        day = date(2026, 10, 5)
        def loader():
            return {'schema_version': 1, 'tracks': [asdict(t) for t in catalog()]}
        mix = select_mix(store, day, timezone.utc, 'UTC', CONFIG, loader, save=True)
        def unexpected_read():
            self.fail('Frozen mix should not reread the library')
        second = select_mix(store, day, timezone.utc, 'UTC', CONFIG, unexpected_read, save=True)
        self.assertTrue(second.pop('cached'))
        self.assertEqual(second, mix)
        store.close()

    @unittest.skipUnless(shutil.which('node'), 'Node needed only for the publisher bridge tests')
    def test_publisher_bridge(self):
        subprocess.run(['node', 'tests/publish.test.js'], check=True, capture_output=True, text=True)
