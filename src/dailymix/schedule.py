from pathlib import Path
import plistlib
import sys


def write_schedule(output, config, state, hour=3, minute=0, publish=False,
                   playlist='Daily Mix', timezone='America/Indiana/Indianapolis'):
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('Schedule hour/minute is out of range')
    root = Path(__file__).resolve().parents[1]
    state_path = Path(state).resolve()
    logs = state_path.parent / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, '-m', 'dailymix', '--config', str(Path(config).resolve()),
            '--state', str(state_path), 'run', '--timezone', timezone, '--playlist', playlist]
    if publish:
        args.append('--publish')
    payload = {'Label': 'com.brandenxia.dailymix', 'ProgramArguments': args,
               'WorkingDirectory': str(Path.cwd()),
               'EnvironmentVariables': {'PYTHONPATH': str(root)},
               'StartCalendarInterval': {'Hour': hour, 'Minute': minute},
               'RunAtLoad': False,
               'StandardOutPath': str(logs / 'daily.log'),
               'StandardErrorPath': str(logs / 'daily-error.log')}
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(payload))
    return {'plist': str(path.resolve()), 'publishes': publish,
            'hour': hour, 'minute': minute, 'installed': False}
