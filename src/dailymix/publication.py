from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile
import uuid


@contextmanager
def publication_lock(state_path):
    path = Path(str(state_path) + '.publish.lock')
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another playlist publication is running') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def music_request(request):
    script = Path(__file__).parent / 'sources' / 'publish.js'
    with tempfile.TemporaryDirectory(prefix='dailymix-publish-') as directory:
        path = Path(directory) / 'request.json'
        path.write_text(json.dumps(request))
        try:
            result = subprocess.run(['osascript', '-l', 'JavaScript', str(script), str(path)],
                                    capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Music publication timed out; inspect the managed playlist and saved backup before retrying') from None
    if result.returncode:
        raise RuntimeError('Music publication failed: ' + result.stderr.strip())
    return json.loads(result.stdout)


def publish_mix(store, state_path, mix, name, dry_run=False, bridge=music_request):
    if not name.strip():
        raise ValueError('Playlist name cannot be empty')
    ids = [item['id'] for item in mix['tracks']]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Cannot publish an empty mix or duplicate track IDs')
    request = {'name': name, 'ids': ids, 'dry_run': True}
    with publication_lock(state_path):
        plan = bridge(request)
        if dry_run:
            return {**plan, 'dry_run': True}
        # Backup exists before any Music mutations; keep it if the process is interrupted.
        backup_dir = Path(state_path).parent / 'publication-backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / (uuid.uuid4().hex + '.json')
        backup.write_text(json.dumps({'date': mix['date'], **plan}, indent=2))
        result = bridge({**request, 'dry_run': False,
                         'expected_playlist_id': plan['playlist_id'],
                         'expected_previous_ids': plan['previous_ids'],
                         'staging_name': 'Daily Mix staging ' + uuid.uuid4().hex})
        if not result.get('verified') or result.get('ids') != ids:
            raise RuntimeError('Music did not confirm the requested playlist contents')
        store.record_publication(mix['date'], name, result['playlist_id'], datetime.now(timezone.utc).isoformat())
        return {**result, 'backup': str(backup)}
