from pathlib import Path
import tempfile
import unittest
from dailymix.credentials import lastfm_api_key


class CredentialTests(unittest.TestCase):
    def test_lastfm_generated_names_and_unused_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('API_key = example-key\nshared_secret = "ignored even with bad quoting\nregistered_to = user\n')
            self.assertEqual(lastfm_api_key(path, {}), 'example-key')

    def test_environment_precedence_missing_file_and_empty_values(self):
        self.assertEqual(lastfm_api_key('missing', {'LASTFM_API_KEY': 'environment'}), 'environment')
        self.assertIsNone(lastfm_api_key('missing', {}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('API_KEY=\nLASTFM_API_KEY=""\n')
            self.assertIsNone(lastfm_api_key(path, {}))

    def test_quoted_values_comments_and_no_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.env'
            marker = root / 'must-not-exist'
            path.write_text(f'export LASTFM_API_KEY="$(touch {marker})" # literal data\n')
            self.assertEqual(lastfm_api_key(path, {}), f'$(touch {marker})')
            self.assertFalse(marker.exists())

    def test_bad_quoting_error_does_not_leak_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('API_key="private-value\n')
            with self.assertRaises(ValueError) as captured:
                lastfm_api_key(path, {})
            self.assertNotIn('private-value', str(captured.exception))


class LastfmIntegrationTests(unittest.TestCase):
    def test_cli_import_loads_dotenv_key(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        from dailymix.cli import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.toml').write_text('[lastfm]\nusername="user"\n')
            (root / '.env').write_text('API_key=example-key\n')
            with patch('dailymix.credentials.os.environ', {}), patch('dailymix.cli.fetch_history', return_value=([], {'complete': True})) as fetch:
                with redirect_stdout(StringIO()):
                    main(['--config', str(root / 'config.toml'), '--state', str(root / 'history.sqlite3'), 'sync-lastfm'])
                self.assertEqual(fetch.call_args.args[:2], ('user', 'example-key'))

    def test_api_error_redacts_key(self):
        from unittest.mock import patch
        from io import BytesIO
        import json
        from dailymix.sources.lastfm import fetch_history
        response = BytesIO(json.dumps({'error': 10, 'message': 'invalid example-secret'}).encode())
        with patch('dailymix.sources.lastfm.urlopen', return_value=response):
            with self.assertRaises(ValueError) as captured:
                fetch_history('user', 'example-secret', max_pages=1)
        self.assertNotIn('example-secret', str(captured.exception))
