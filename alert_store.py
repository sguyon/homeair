"""The phones that get alerts, and what was sent to each: SQLite beside the history store.
The web requests (add, choices, remove) and the checker thread (read the phones, save what was sent) share one
connection behind a lock. A phone's push subscription (its address and keys) never leaves this file except to
`subscription()`, which the sender uses. No network here."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import config
import history_store
from alert_rules import BASELINE_ALL, QUIET_DEFAULT, Phone, Sent

ALERTS_FILE = "alerts.sqlite"   # the file's name, beside the history store
MAX_PHONES = 10                 # phones one home can add: the page is public to anyone who can reach it, so this caps what a stranger can store


class PhoneLimit(Exception):
    """The home already has MAX_PHONES phones."""


def alerts_path(env=os.environ, root=config.ROOT) -> Path:
    """ALERTS_FILE if set; else alerts.sqlite next to the history store."""
    if env.get("ALERTS_FILE", "").strip():
        return Path(env["ALERTS_FILE"].strip())
    return history_store.store_path(env, root).parent / ALERTS_FILE


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _when(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _phone(i, muted, quiet, since, sent, baseline) -> Phone:
    return Phone(id=i, muted=frozenset(json.loads(muted)), quiet=tuple(json.loads(quiet)), since=datetime.fromisoformat(since),
                 sent={k: Sent(datetime.fromisoformat(v["at"]), _when(v["left"]), v.get("clear", True))   # sent before the margin rule: cleared
                       for k, v in json.loads(sent).items()},
                 baseline=frozenset(json.loads(baseline)))


def _sent_json(sent: dict) -> dict:
    return {k: {"at": _iso(s.at), "left": _iso(s.left), "clear": s.clear} for k, s in sent.items()}


class AlertStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():   # a phone's address and keys: readable by the owner only, like .env
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, history_store.PRIVATE_MODE))
        self._db = sqlite3.connect(str(self.path), check_same_thread=False,
                                   timeout=history_store.BUSY_TIMEOUT_MS / 1000)
        self._db.execute(f"PRAGMA busy_timeout={history_store.BUSY_TIMEOUT_MS}")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS phones (id TEXT PRIMARY KEY, secret_hash TEXT, endpoint TEXT UNIQUE, "
                         "subscription TEXT, device TEXT, since TEXT, muted TEXT, quiet TEXT, sent TEXT, baseline TEXT)")
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(phones)")}
        if "baseline" not in columns:   # a file from before baselines: its phones have theirs already
            self._db.execute("ALTER TABLE phones ADD COLUMN baseline TEXT")
        self._db.execute("UPDATE phones SET baseline='[]' WHERE baseline IS NULL")
        self._db.commit()
        for part in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                os.chmod(part, history_store.PRIVATE_MODE)
            except OSError:
                pass

    def close(self):
        with self._lock:
            self._db.close()

    def add_phone(self, subscription: dict, device: str, now: datetime) -> tuple[str, str]:
        """Add a phone: (its id, its secret). The secret is shown once and kept hashed. A new phone waits for its
        baseline (BASELINE_ALL): the next check records what is due then, unsent. The same address (the phone subscribed
        again, after its page lost its id) keeps its row, choices, sent history and baseline, with a new id and secret.
        A new phone past MAX_PHONES raises PhoneLimit."""
        phone_id, secret = secrets.token_urlsafe(8), secrets.token_urlsafe(24)
        with self._lock:
            if self._db.execute("SELECT 1 FROM phones WHERE endpoint=?", (subscription["endpoint"],)).fetchone():
                self._db.execute("UPDATE phones SET id=?, secret_hash=?, subscription=? WHERE endpoint=?",
                                 (phone_id, _hash(secret), json.dumps(subscription), subscription["endpoint"]))
            elif self._db.execute("SELECT COUNT(*) FROM phones").fetchone()[0] >= MAX_PHONES:
                raise PhoneLimit
            else:
                self._db.execute("INSERT INTO phones VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (phone_id, _hash(secret), subscription["endpoint"], json.dumps(subscription), device,
                                  now.isoformat(), "[]", json.dumps(list(QUIET_DEFAULT)), "{}", json.dumps([BASELINE_ALL])))
            self._db.commit()
        return phone_id, secret

    def phones(self) -> list[Phone]:
        with self._lock:
            found = self._db.execute("SELECT id, muted, quiet, since, sent, baseline FROM phones ORDER BY since, id").fetchall()
        return [_phone(*row) for row in found]

    def subscription(self, phone_id: str) -> dict:
        with self._lock:
            found = self._db.execute("SELECT subscription FROM phones WHERE id=?", (phone_id,)).fetchone()
        if not found:
            raise KeyError(phone_id)
        return json.loads(found[0])

    def listing(self) -> list[dict]:
        """What the page may show: each phone's id, device, and the day and time ("HH:MM") it was added, both in the
        home's time as stored (the time tells apart two phones of one kind added the same day). Never the address or keys."""
        with self._lock:
            found = self._db.execute("SELECT id, device, since FROM phones ORDER BY since, id").fetchall()
        return [{"id": i, "device": d, "since": s[:10], "time": s[11:16]} for i, d, s in found]

    def has(self, phone_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM phones WHERE id=?", (phone_id,)).fetchone() is not None

    def _check(self, phone_id, secret) -> None:
        found = self._db.execute("SELECT secret_hash FROM phones WHERE id=?", (phone_id,)).fetchone()
        if not found or not hmac.compare_digest(found[0], _hash(secret or "")):
            raise PermissionError("wrong phone or secret")

    def verify(self, phone_id: str, secret: str) -> None:
        """PermissionError unless `secret` is this phone's."""
        with self._lock:
            self._check(phone_id, secret)

    def choices(self, phone_id: str, secret: str) -> dict:
        """This phone's choices as stored: {"muted": [...], "quiet": [from, to]}. PermissionError unless the secret is its."""
        with self._lock:
            self._check(phone_id, secret)
            muted, quiet = self._db.execute("SELECT muted, quiet FROM phones WHERE id=?", (phone_id,)).fetchone()
        return {"muted": json.loads(muted), "quiet": json.loads(quiet)}

    def set_choices(self, phone_id: str, secret: str, muted, quiet) -> None:
        """Save the choices. An item unmuted here waits for its baseline, in the same write: no check can see it
        unmuted before that, so what is due for it now is recorded at the next check, not sent."""
        with self._lock:
            self._check(phone_id, secret)
            old_muted, baseline = self._db.execute("SELECT muted, baseline FROM phones WHERE id=?", (phone_id,)).fetchone()
            baseline = set(json.loads(baseline)) | (set(json.loads(old_muted)) - set(muted))
            self._db.execute("UPDATE phones SET muted=?, quiet=?, baseline=? WHERE id=?",
                             (json.dumps(sorted(muted)), json.dumps(list(quiet)), json.dumps(sorted(baseline)), phone_id))
            self._db.commit()

    def take_baselines(self, record) -> None:
        """For each phone waiting for a baseline: `record(phone)` adds to `phone.sent` what is due now, then the store
        keeps it and clears the wait, all under the lock, so a choice saved meanwhile comes before or after, whole."""
        with self._lock:
            found = self._db.execute("SELECT id, muted, quiet, since, sent, baseline FROM phones WHERE baseline != '[]'").fetchall()
            for row in found:
                phone = _phone(*row)
                record(phone)
                self._db.execute("UPDATE phones SET sent=?, baseline='[]' WHERE id=?", (json.dumps(_sent_json(phone.sent)), phone.id))
            self._db.commit()

    def remove_if_present(self, phone_id: str) -> bool:
        """Remove a phone with no secret (another phone or computer removing it): True if this call removed it, False
        when it was already gone, so two removals at once send one notice."""
        with self._lock:
            gone = self._db.execute("DELETE FROM phones WHERE id=?", (phone_id,)).rowcount
            self._db.commit()
        return gone == 1

    def remove(self, phone_id: str, secret: str | None = None) -> None:
        """A phone turning its own alerts off gives its secret. None: the Phones list removing another phone (after its
        notice), or the sender dropping a phone the push service says is gone."""
        with self._lock:
            if secret is not None:
                self._check(phone_id, secret)
            self._db.execute("DELETE FROM phones WHERE id=?", (phone_id,))
            self._db.commit()

    def save_sent(self, phones) -> None:
        """Merge each phone's `sent` into the stored one, key by key: a key written meanwhile (a phone turned alerts on,
        an item unmuted) stays; the keys given here are added or updated. The choices are left as they are."""
        with self._lock:
            for p in phones:
                found = self._db.execute("SELECT sent FROM phones WHERE id=?", (p.id,)).fetchone()
                if not found:
                    continue
                body = json.loads(found[0])
                body.update(_sent_json(p.sent))
                self._db.execute("UPDATE phones SET sent=? WHERE id=?", (json.dumps(body), p.id))
            self._db.commit()
