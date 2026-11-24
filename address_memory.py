"""The last address and port each monitor answered at, kept across restarts in a small file next to the history store, so the
poller tries it before browsing. A hint only: it is read back through the home-network check, and a wrong or stale
one costs a failed read, then the usual search. Never in config.toml."""
import ipaddress
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import config
import history_store

log = logging.getLogger("aq.local")

ADDRESS_FILE = "local-addresses.json"   # the file's name, in the history store's folder
FILE_MODE = 0o600                       # owner only: it lists the addresses of the home's devices


def default_path(env=os.environ) -> Path:
    return history_store.store_path(env).parent / ADDRESS_FILE


DEFAULT_PORT = 80   # what a file written before the port was kept implies


def _valid_port(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536


def _own_ipv4(text) -> bool:
    try:
        return config.is_own_network(ipaddress.IPv4Address(text))
    except ValueError:
        return False


class AddressMemory:
    def __init__(self, path):
        self.path = Path(path)
        self._seen = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {serial: entry for serial, entry in data.items()
                if isinstance(serial, str) and config.SERIAL_RE.fullmatch(serial) and isinstance(entry, dict)
                and isinstance(entry.get("address"), str) and _own_ipv4(entry["address"])
                and _valid_port(entry.get("port", DEFAULT_PORT))}

    def get(self, serial):
        """The remembered (IPv4 address, port) for this serial, or None."""
        entry = self._seen.get(serial)
        return (entry["address"], entry.get("port", DEFAULT_PORT)) if entry else None

    def remember(self, serial, address, port=DEFAULT_PORT, now=None):
        """Keep `address` and `port` for `serial` when they differ from what is kept and the address is on the
        home's network. A file that can't be written is logged (its class only) and ignored."""
        if (not _own_ipv4(address) or not _valid_port(port) or not config.SERIAL_RE.fullmatch(serial)
                or self.get(serial) == (address, port)):
            return
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated = {**self._seen, serial: {"address": address, "port": port, "at": stamp}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=ADDRESS_FILE + ".")   # created 0600
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(updated, handle)
                os.chmod(tmp, FILE_MODE)
                os.replace(tmp, self.path)
            except BaseException:
                os.unlink(tmp)
                raise
        except OSError as error:
            log.warning("saving the monitors' last addresses failed (%s)", type(error).__name__)
            return
        self._seen = updated
