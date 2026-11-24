"""The home's own history: every reading Home Air takes from a monitor on the Wi-Fi, in SQLite next to config.toml.

One row per read per monitor, keyed by serial and time. Local rows are never replaced; the cloud's rows only fill gaps
(`fill`). Every time is stored as `%Y-%m-%dT%H:%M:%SZ` (UTC) so string order is time order. No network here."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

STORE_FILE = "history.sqlite"           # the store's file name
GAP = timedelta(minutes=10)             # a hole in a monitor's readings longer than this is a gap (a 1-minute poll missing 10 in a row)
# A cloud row is a 5-minute average stamped with the start of its 5 minutes: it covers [t, t + CLOUD_ROW) (checked
# against the local readings on the real home, 22 Nov). A local row is one reading at its time.
CLOUD_ROW = timedelta(minutes=5)
BUSY_TIMEOUT_MS = 5000                  # how long a write waits for another process on the same file before failing
PRIVATE_MODE = 0o600                    # the store's files: owner only (the readings show when someone is home)
# A stored row keeps every measured field (a number) a monitor sends; a home without a token has no other copy of them.
# Not kept: what the table's columns already hold (time, serial, source), what the config names (the cloud ids) and
# anything that says where the device is on the network. Text is never kept: the readings are numbers.
NOT_STORED = ("timestamp", "serialno", "serial", "via", "locationId", "port", "ip", "ipAddress", "host", "hostname",
              "address", "mac", "ssid", "bssid")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def store_path(env=os.environ, root=config.ROOT) -> Path:
    """HISTORY_FILE if set; else next to the config file loaded (CONFIG_FILE's folder, else root)."""
    if env.get("HISTORY_FILE", "").strip():
        return Path(env["HISTORY_FILE"].strip())
    if env.get("CONFIG_FILE", "").strip():
        return Path(env["CONFIG_FILE"].strip()).parent / STORE_FILE
    return Path(root) / STORE_FILE


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _key(value) -> str:
    return _parse(value).strftime(TIME_FORMAT)


def _trim(row: dict) -> dict:
    return {k: v for k, v in row.items()
            if k not in NOT_STORED and isinstance(v, (int, float)) and not isinstance(v, bool)}


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():   # the home's readings show when someone is home: readable by its owner only, like .env
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, PRIVATE_MODE))
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
        self._db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS readings (serial TEXT, ts TEXT, via TEXT, row TEXT, "
                         "PRIMARY KEY (serial, ts)) WITHOUT ROWID")
        self._db.commit()
        for part in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                os.chmod(part, PRIVATE_MODE)
            except OSError:
                pass

    def close(self):
        with self._lock:
            self._db.close()

    def _insert(self, serial, row, via) -> int:
        ts = _key(row["timestamp"])
        body = json.dumps(_trim(row), separators=(",", ":"))
        cur = self._db.execute("INSERT OR IGNORE INTO readings (serial, ts, via, row) VALUES (?, ?, ?, ?)",
                               (serial.lower(), ts, via, body))
        return cur.rowcount

    def add(self, serial: str, row: dict) -> None:
        """A local reading. A reading already stored at that second stays as it is."""
        with self._lock:
            self._insert(serial, row, "local")
            self._db.commit()

    def rows(self, serial: str, frm: datetime, to: datetime) -> list[dict]:
        with self._lock:
            found = self._db.execute("SELECT ts, row FROM readings WHERE serial=? AND ts>=? AND ts<=? ORDER BY ts",
                                     (serial.lower(), _key(frm), _key(to))).fetchall()
        return [{**json.loads(body), "timestamp": ts} for ts, body in found]

    def last(self, serial: str) -> dict | None:
        with self._lock:
            found = self._db.execute("SELECT ts, row FROM readings WHERE serial=? ORDER BY ts DESC LIMIT 1",
                                     (serial.lower(),)).fetchone()
        return {**json.loads(found[1]), "timestamp": found[0]} if found else None

    def holds(self, serial: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM readings WHERE serial=? LIMIT 1", (serial.lower(),)).fetchone() is not None

    def gaps(self, serial: str, since: datetime, until: datetime, longer_than=GAP) -> list[tuple[datetime, datetime]]:
        """Holes longer than `longer_than` between since and until, edges included: (where the rows before it stop
        covering, the start of the next row). A cloud row covers its 5 minutes (CLOUD_ROW), a local row its instant."""
        since, until = _parse(since), _parse(until)
        with self._lock:
            found = self._db.execute("SELECT ts, via FROM readings WHERE serial=? AND ts>=? AND ts<=? ORDER BY ts",
                                     (serial.lower(), _key(since - CLOUD_ROW), _key(until))).fetchall()
        out, covered = [], since
        for ts, via in found:
            start = _parse(ts)
            if start - covered > longer_than:
                out.append((covered, start))
            covered = max(covered, start + CLOUD_ROW if via == "cloud" else start)
        if until - covered > longer_than:
            out.append((covered, until))
        return out

    def _covered(self, serial, start, end) -> bool:
        """Whether a stored row already covers part of [start, end): a reading inside it, or a cloud row's 5 minutes
        overlapping it."""
        return self._db.execute(
            "SELECT 1 FROM readings WHERE serial=? AND ((ts>=? AND ts<?) OR (via='cloud' AND ts>? AND ts<?)) LIMIT 1",
            (serial, _key(start), _key(end), _key(start - CLOUD_ROW), _key(end))).fetchone() is not None

    def fill(self, serial: str, cloud_rows: list[dict], gaps) -> int:
        """Insert the cloud rows whose 5 minutes [t, t + CLOUD_ROW) lie inside a gap and overlap no stored row: a
        stored row is never replaced and no time is counted twice. A row whose 5 minutes haven't ended (the one at the
        gap's end, `until`) is left out: it is still averaging, and a stored row is never updated. Returns the count added."""
        spans = [(_parse(a), _parse(b)) for a, b in gaps]
        serial = serial.lower()
        added = 0
        with self._lock:
            for row in cloud_rows or []:
                try:
                    start = _parse(row["timestamp"])
                except (KeyError, ValueError, TypeError):
                    continue
                end = start + CLOUD_ROW
                if any(a <= start and end <= b for a, b in spans) and not self._covered(serial, start, end):
                    added += self._insert(serial, row, "cloud")
            self._db.commit()
        return added
