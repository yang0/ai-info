import json
import sqlite3
from pathlib import Path
from .models import Settings, Post, utcnow


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'intel.db'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS posts (mode TEXT, id TEXT, created REAL, body TEXT,
              PRIMARY KEY(mode,id));
            CREATE INDEX IF NOT EXISTS posts_time ON posts(mode,created);
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started TEXT, state TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT, time TEXT, agent TEXT, state TEXT, message TEXT);
            CREATE TABLE IF NOT EXISTS snapshots (mode TEXT PRIMARY KEY, body TEXT);
            ''')
            # An interrupted process must never leave a run looking active.
            for row in db.execute("SELECT id,body FROM runs WHERE state='running'").fetchall():
                body = json.loads(row['body'])
                body.update(state='interrupted', finished=utcnow().isoformat())
                db.execute('UPDATE runs SET state=?,body=? WHERE id=?',
                           ('interrupted', json.dumps(body), row['id']))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def settings(self) -> Settings:
        with self.connect() as db:
            row = db.execute('SELECT body FROM settings WHERE id=1').fetchone()
        return Settings.model_validate_json(row['body']) if row else Settings()

    def save_settings(self, settings: Settings):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (settings.model_dump_json(),))

    def put_posts(self, mode: str, posts: list[Post]):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO posts VALUES(?,?,?,?)', [
                (mode, p.id, p.created_at.timestamp(), p.model_dump_json()) for p in posts])
            db.execute('DELETE FROM posts WHERE created < ?', (utcnow().timestamp() - 7 * 86400,))

    def posts(self, mode: str, since: float):
        with self.connect() as db:
            rows = db.execute('SELECT body FROM posts WHERE mode=? AND created>=? ORDER BY created',
                              (mode, since)).fetchall()
        return [Post.model_validate_json(r['body']) for r in rows]

    def run(self, body: dict):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?)',
                       (body['id'], body['started'], body['state'], json.dumps(body, ensure_ascii=False)))

    def runs(self):
        with self.connect() as db:
            return [json.loads(r['body']) for r in db.execute(
                'SELECT body FROM runs ORDER BY started DESC LIMIT 30').fetchall()]

    def event(self, run_id, agent, state, message):
        with self.connect() as db:
            cur = db.execute('INSERT INTO events(run_id,time,agent,state,message) VALUES(?,?,?,?,?)',
                             (run_id, utcnow().isoformat(), agent, state, message))
            return cur.lastrowid

    def events(self, after=0):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM events WHERE id>? ORDER BY id LIMIT 500', (after,)).fetchall()
        return [dict(r) for r in rows]

    def recent_events(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 80').fetchall()
        return [dict(r) for r in reversed(rows)]

    def publish(self, mode, snapshot):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?)',
                       (mode, json.dumps(snapshot, ensure_ascii=False)))

    def snapshot(self, mode):
        with self.connect() as db:
            row = db.execute('SELECT body FROM snapshots WHERE mode=?', (mode,)).fetchone()
        return json.loads(row['body']) if row else None
