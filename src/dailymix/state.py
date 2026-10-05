import json
import sqlite3
from contextlib import contextmanager
from .models import Event


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            source TEXT NOT NULL, account TEXT NOT NULL, timestamp INTEGER NOT NULL,
            artist TEXT NOT NULL, title TEXT NOT NULL, album TEXT NOT NULL,
            PRIMARY KEY(source, account, timestamp, artist, title, album));
        CREATE TABLE IF NOT EXISTS publications (
            date TEXT NOT NULL, name TEXT NOT NULL, playlist_id TEXT NOT NULL,
            published_at TEXT NOT NULL, PRIMARY KEY(date, name));
        CREATE TABLE IF NOT EXISTS mixes (
            date TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def import_events(self, events):
        before = self.db.total_changes
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)", [e.key for e in events])
        return self.db.total_changes - before

    def events(self, account=None):
        query = "SELECT artist,title,album,timestamp,source,account FROM events"
        rows = self.db.execute(query + (" WHERE account=?" if account is not None else "") + " ORDER BY timestamp,artist,title,album", (account,) if account is not None else ())
        return [Event(*r) for r in rows]

    def mix(self, day):
        row = self.db.execute("SELECT payload FROM mixes WHERE date=?", (day,)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, before):
        return {day: json.loads(payload) for day, payload in self.db.execute("SELECT date,payload FROM mixes WHERE date<? ORDER BY date", (before,))}

    def save_mix(self, day, payload):
        self.db.execute("INSERT INTO mixes VALUES(?,?)", (day, json.dumps(payload, ensure_ascii=False)))

    def record_publication(self, day, name, playlist_id, published_at):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO publications VALUES(?,?,?,?)",
                            (day, name, playlist_id, published_at))

    def publications(self, day):
        return [dict(name=name, playlist_id=identifier, published_at=at)
                for name, identifier, at in self.db.execute(
                    "SELECT name,playlist_id,published_at FROM publications WHERE date=? ORDER BY name", (day,))]

    def close(self):
        self.db.close()
