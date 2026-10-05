from dataclasses import dataclass
from .models import normalize


@dataclass(frozen=True)
class Match:
    status: str
    track_id: str | None = None
    confidence: str | None = None


class Resolver:
    def __init__(self, tracks, overrides=None):
        self.index = {}
        self.ids = {t.id for t in tracks}
        self.overrides = overrides or {}
        for t in tracks:
            self.index.setdefault((normalize(t.artist), normalize(t.title)), []).append(t)

    def resolve(self, event):
        key = (normalize(event.artist), normalize(event.title), normalize(event.album))
        if key in self.overrides:
            identifier = self.overrides[key]
            if identifier not in self.ids:
                raise ValueError(f"Override refers to unknown local track: {identifier}")
            return Match("matched", identifier, "manual")
        candidates = self.index.get(key[:2], [])
        if not candidates:
            return Match("absent")
        if key[2]:
            exact = [t for t in candidates if normalize(t.album) == key[2]]
            if len(exact) == 1:
                return Match("matched", exact[0].id, "artist-title-album")
            # A conflicting album can indicate an alternate recording.
            return Match("ambiguous")
        if len(candidates) == 1:
            return Match("matched", candidates[0].id, "artist-title")
        return Match("ambiguous")
