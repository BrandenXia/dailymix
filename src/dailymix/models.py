from dataclasses import dataclass
from datetime import datetime
import unicodedata


def normalize(value: str) -> str:
    # Preserve punctuation and version qualifiers: instrumental/live are distinct.
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artist: str
    album: str = ""
    grouping: str = ""
    plays: int = 0
    skips: int = 0
    rating: int = 0
    added: str | None = None
    last_played: str | None = None
    duration: float = 0
    enabled: bool = True

    @property
    def tags(self):
        return frozenset(x.strip().upper() for x in self.grouping.split(",") if x.strip())


@dataclass(frozen=True)
class Event:
    artist: str
    title: str
    album: str
    timestamp: int
    source: str = "lastfm"
    account: str = ""

    @property
    def key(self):
        return (self.source, self.account, self.timestamp, self.artist, self.title, self.album)
