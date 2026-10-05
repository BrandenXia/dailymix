import json
import subprocess
from pathlib import Path
from ..models import Track


def read_music():
    script = Path(__file__).with_name("music.js")
    result = subprocess.run(["osascript", "-l", "JavaScript", str(script)], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Music read failed: " + result.stderr.strip())
    payload = json.loads(result.stdout)
    load_catalog(payload)  # Validate before returning or saving.
    return payload


def load_catalog(payload):
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported catalog schema version")
    tracks = [Track(**row) for row in payload["tracks"]]
    if any(not t.id for t in tracks) or len({t.id for t in tracks}) != len(tracks):
        raise ValueError("Catalog track IDs must be nonempty and unique")
    return tracks
