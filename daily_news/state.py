"""Local single-host outbox with durable snapshots and explicit uncertain outcomes."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import sqlite3
from .models import Digest


class StateError(RuntimeError):
    pass


@contextmanager
def run_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "run.lock").open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StateError("已有日报任务正在运行，本次未执行") from None
        yield
    finally:
        # Closing releases either OS lock, including when the process exits unexpectedly.
        handle.close()


class State:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "daily-news.sqlite3"
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise StateError("运行记录数据库版本较新，请使用匹配的程序版本")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS editions (
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            edition TEXT NOT NULL REFERENCES editions(id), channel TEXT NOT NULL,
            target TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, retryable INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL, next_at TEXT, error TEXT,
            PRIMARY KEY (edition, channel)
        );
        CREATE TABLE IF NOT EXISTS jobs (
            edition TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL,
            updated_at TEXT NOT NULL, next_at TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS seen (article TEXT PRIMARY KEY, delivered_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feeds (
            url TEXT PRIMARY KEY, body BLOB NOT NULL, etag TEXT NOT NULL, modified TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, edition TEXT NOT NULL,
            at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
        );
        PRAGMA user_version=1;
        """)
        if os.name != "nt":
            path.chmod(0o600)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def event(self, edition: str, now: datetime, action: str, detail: str = ""):
        self.db.execute("INSERT INTO events(edition,at,action,detail) VALUES (?,?,?,?)",
                        (edition, now.isoformat(), action, detail))

    def get_feed(self, url):
        row = self.db.execute("SELECT * FROM feeds WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None

    def save_feed(self, url, body, etag, modified):
        self.db.execute("INSERT OR REPLACE INTO feeds VALUES (?,?,?,?)", (url, body, etag, modified))

    def seen_articles(self, now, days):
        since = (now - timedelta(days=days)).isoformat()
        return {r[0] for r in self.db.execute("SELECT article FROM seen WHERE delivered_at>=?", (since,))}

    def get_digest(self, edition):
        row = self.db.execute("SELECT * FROM editions WHERE id=?", (edition,)).fetchone()
        if row:
            return Digest.from_dict(json.loads(row["payload"])), row["fingerprint"]
        return None

    def start_generation(self, edition, now, max_attempts, manual_retry=False):
        job = self.db.execute("SELECT * FROM jobs WHERE edition=?", (edition,)).fetchone()
        attempts = job["attempts"] if job else 0
        if job and not manual_retry:
            if attempts >= max_attempts or job["next_at"] and now.isoformat() < job["next_at"]:
                return False
        if manual_retry:
            attempts = 0
        self.db.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?)",
            (edition, "preparing", attempts + 1, now.isoformat(), None, None),
        )
        self.event(edition, now, "generation_started")
        return True

    def generation_failed(self, edition, now, retry_minutes, error):
        self.db.execute(
            "UPDATE jobs SET status='failed',updated_at=?,next_at=?,error=? WHERE edition=?",
            (now.isoformat(), (now + timedelta(minutes=retry_minutes)).isoformat(), error, edition),
        )
        self.event(edition, now, "generation_failed", error)

    def save_digest(self, digest, content_fingerprint, targets: dict[str, str], now):
        with self.transaction():
            self.db.execute("INSERT INTO editions VALUES (?,?,?,?)",
                            (digest.edition, content_fingerprint,
                             json.dumps(digest.to_dict(), ensure_ascii=False), now.isoformat()))
            for name, identity in targets.items():
                self.db.execute(
                    "INSERT INTO deliveries(edition,channel,target,updated_at) VALUES (?,?,?,?)",
                    (digest.edition, name, identity, now.isoformat()),
                )
            self.db.execute("UPDATE jobs SET status='ready',error=NULL,next_at=NULL WHERE edition=?",
                            (digest.edition,))
            self.event(digest.edition, now, "snapshot_saved")

    def deliveries(self, edition):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM deliveries WHERE edition=? ORDER BY channel", (edition,))]

    def recover_interrupted(self, edition, now):
        rows = self.db.execute(
            "SELECT channel FROM deliveries WHERE edition=? AND status='sending'", (edition,)
        ).fetchall()
        for row in rows:
            self.outcome(edition, row["channel"], "uncertain", now, False,
                         "上次发送过程中断，需确认是否已收到后 resolve", None)

    def requeue_failed(self, edition, now):
        self.db.execute(
            "UPDATE deliveries SET status='pending',attempts=0,retryable=1,next_at=NULL,error=NULL "
            "WHERE edition=? AND status='failed'", (edition,))
        self.event(edition, now, "manual_retry")

    def begin_send(self, edition, channel, now):
        self.db.execute(
            "UPDATE deliveries SET status='sending',attempts=attempts+1,updated_at=?,next_at=NULL,error=NULL "
            "WHERE edition=? AND channel=?", (now.isoformat(), edition, channel))
        self.event(edition, now, "send_started", channel)

    def outcome(self, edition, channel, status, now, retryable=False, error=None, next_at=None):
        with self.transaction():
            self.db.execute(
                "UPDATE deliveries SET status=?,updated_at=?,retryable=?,error=?,next_at=? "
                "WHERE edition=? AND channel=?",
                (status, now.isoformat(), int(retryable), error,
                 next_at.isoformat() if next_at else None, edition, channel),
            )
            self.event(edition, now, status, channel + (": " + error if error else ""))
            rows = self.deliveries(edition)
            if rows and all(row["status"] == "sent" for row in rows):
                digest, _ = self.get_digest(edition)
                for article in digest.article_ids():
                    self.db.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (article, now.isoformat()))
                self.db.execute("UPDATE jobs SET status='complete' WHERE edition=?", (edition,))

    def resolve(self, edition, channel, action, now, note):
        row = next((r for r in self.deliveries(edition) if r["channel"] == channel), None)
        if not row or row["status"] != "uncertain":
            raise StateError("resolve 仅适用于结果不确定的投递记录")
        if not note.strip():
            raise StateError("请提供 --note 记录核对结果")
        if action == "delivered":
            self.outcome(edition, channel, "sent", now)
        elif action == "retry":
            self.db.execute(
                "UPDATE deliveries SET status='pending',attempts=0,next_at=NULL,error=NULL,retryable=1 "
                "WHERE edition=? AND channel=?", (edition, channel))
        else:
            raise StateError("未知的处理动作")
        self.event(edition, now, "resolved_" + action, channel + ": " + note)

    def status(self):
        return {
            "jobs": [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 20")],
            "deliveries": [
                {k: r[k] for k in ("edition", "channel", "status", "attempts", "updated_at", "next_at", "error")}
                for r in self.db.execute("SELECT * FROM deliveries ORDER BY updated_at DESC LIMIT 40")
            ],
            "events": [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")],
        }

    def backup(self, path: Path):
        if path.exists():
            raise StateError("备份文件已存在，拒绝覆盖")
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as target:
            self.db.backup(target)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
