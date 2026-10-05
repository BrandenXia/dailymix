"""Read local credentials as data, without executing or expanding shell syntax."""
import os
from pathlib import Path
import shlex


def lastfm_api_key(env_file, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('LASTFM_API_KEY'):
        return environ['LASTFM_API_KEY']
    path = Path(env_file)
    if not path.is_file():
        return None
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].lstrip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name = name.strip().casefold()
        # Only read the key. The shared secret is unnecessary for history reads.
        if name not in ('lastfm_api_key', 'api_key'):
            continue
        try:
            parts = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f'Invalid API key quoting in env file at line {number}') from None
        if len(parts) > 1:
            raise ValueError(f'Invalid API key assignment in env file at line {number}')
        values[name] = parts[0] if parts else ''
    return values.get('lastfm_api_key') or values.get('api_key') or None
