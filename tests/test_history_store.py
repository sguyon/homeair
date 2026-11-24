"""The store is the only copy of a token-free home's history: losing or rewriting it is silent."""
from datetime import datetime, timezone

import pytest

from history_store import HistoryStore

SERIAL = "84fce602549c"


def at(h, m):
    return datetime(2026, 11, 22, h, m, tzinfo=timezone.utc)


def row(h, m, pm=5.0, fmt="%Y-%m-%dT%H:%M:%SZ"):
    return {"timestamp": at(h, m).strftime(fmt), "pm02_corrected": pm, "rco2": 600, "atmp": 21.0}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "history.sqlite"


def test_rows_survive_reopening(path):
    store = HistoryStore(path)
    added = [row(10, 0, 1), row(10, 1, 2), row(10, 2, 3)]
    for r in added:
        store.add(SERIAL, r)
    store.close()
    again = HistoryStore(path)
    assert again.rows(SERIAL, at(9, 0), at(11, 0)) == added


def test_times_in_both_formats_sort_together(path):
    store = HistoryStore(path)
    store.add(SERIAL, row(10, 6))
    store.fill(SERIAL, [row(10, 0, fmt="%Y-%m-%dT%H:%M:%S.000Z")], [(at(9, 50), at(10, 6))])
    found = store.rows(SERIAL, at(9, 0), at(11, 0))
    assert [r["timestamp"] for r in found] == ["2026-11-22T10:00:00Z", "2026-11-22T10:06:00Z"]


def test_gaps_over_ten_minutes(path):
    store = HistoryStore(path)
    for h, m in [(10, 0), (10, 1), (10, 30), (10, 31)]:
        store.add(SERIAL, row(h, m))
    assert store.gaps(SERIAL, at(10, 0), at(10, 31)) == [(at(10, 1), at(10, 30))]
    other = HistoryStore(path.with_name("b.sqlite"))
    for h, m in [(10, 0), (10, 9), (10, 10)]:
        other.add(SERIAL, row(h, m))
    assert other.gaps(SERIAL, at(10, 0), at(10, 10)) == []


def test_fill_never_replaces_a_local_row(path):
    store = HistoryStore(path)
    for h, m in [(10, 0), (10, 30)]:
        store.add(SERIAL, row(h, m, pm=5.0))
    cloud = [row(10, 0, pm=99.0), row(10, 15, pm=7.0)]
    assert store.fill(SERIAL, cloud, store.gaps(SERIAL, at(10, 0), at(10, 30))) == 1
    found = store.rows(SERIAL, at(10, 0), at(10, 30))
    assert [r["pm02_corrected"] for r in found] == [5.0, 7.0, 5.0]


def test_two_stores_on_one_file_both_write(path):
    a, b = HistoryStore(path), HistoryStore(path)
    for i in range(10):
        (a if i % 2 else b).add(SERIAL, row(10, i))
    assert len(a.rows(SERIAL, at(10, 0), at(11, 0))) == 10


def test_every_measured_field_is_kept_and_no_address_is(path):
    """The store is the only copy for a home without a token: the NOx index, PM1, counts and Wi-Fi signal stay; an
    address, port, text or the identity the columns already hold do not."""
    full = {**row(10, 0), "noxIndex": 1, "tvocIndex": 98, "pm01": 3.1, "pm10": 5.2, "pm003Count": 612.5, "wifi": -52,
            "rhum_corrected": 44.9, "serialno": SERIAL, "locationId": 7, "locationName": "Living room", "via": "local",
            "model": "I-9PSL", "firmwareVersion": "3.1.4", "ip": "192.168.1.20", "host": "192.168.1.20", "port": 80}
    store = HistoryStore(path)
    store.add(SERIAL, full)
    store.close()
    found = HistoryStore(path).rows(SERIAL, at(9, 0), at(11, 0))[0]
    assert {k: found[k] for k in ("noxIndex", "tvocIndex", "pm01", "pm10", "pm003Count", "wifi", "rhum_corrected")} == {
        "noxIndex": 1, "tvocIndex": 98, "pm01": 3.1, "pm10": 5.2, "pm003Count": 612.5, "wifi": -52, "rhum_corrected": 44.9}
    assert found["timestamp"] == "2026-11-22T10:00:00Z"
    assert not {"ip", "host", "port", "serialno", "locationId", "locationName", "via", "model", "firmwareVersion"} & set(found)
    assert "192.168" not in path.read_bytes().decode("latin-1")


def test_a_cloud_row_covers_its_five_minutes_so_a_fill_neither_doubles_nor_freezes_a_partial_row(path):
    """A cloud row is the average of [t, t+5 min) (real home, 22 Nov). Rows whose 5 minutes hold a local reading would
    count that time twice, and the row whose 5 minutes haven't ended would be kept half-averaged for good (a stored row
    is never replaced); a stored cloud row's 5 minutes count as covered, so the next start finds no gap to ask for."""
    store = HistoryStore(path)
    half = lambda h, m: {**row(h, m), "timestamp": at(h, m).replace(second=30).strftime("%Y-%m-%dT%H:%M:%SZ")}
    store.add(SERIAL, half(10, 0))       # the app stopped after 10:00:30 ...
    store.add(SERIAL, half(10, 20))      # ... and read again at 10:20:30
    cloud = [row(10, m, pm=9.0) for m in (0, 5, 10, 15, 20)]
    assert store.fill(SERIAL, cloud, store.gaps(SERIAL, at(10, 0), at(10, 21))) == 3
    assert [r["timestamp"][11:19] for r in store.rows(SERIAL, at(10, 0), at(10, 30))] == [
        "10:00:30", "10:05:00", "10:10:00", "10:15:00", "10:20:30"]
    assert store.gaps(SERIAL, at(10, 0), at(10, 21)) == []
    other = HistoryStore(path.with_name("b.sqlite"))
    other.add(SERIAL, half(10, 0))       # stopped, and it is now 10:23: the 10:20 row is still averaging
    assert other.fill(SERIAL, cloud, other.gaps(SERIAL, at(10, 0), at(10, 23))) == 3
    assert [r["timestamp"][11:16] for r in other.rows(SERIAL, at(10, 0), at(10, 30))][-1] == "10:15"
