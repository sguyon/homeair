import os
import sqlite3
import stat
from datetime import datetime, timezone

import pytest

import alert_store
from alert_rules import BASELINE_ALL, Sent
from alert_store import AlertStore, PhoneLimit

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc)


def sub(n):
    return {"endpoint": f"https://push.example/{n}", "keys": {"p256dh": f"p{n}", "auth": f"a{n}"}}


@pytest.fixture
def store(tmp_path):
    s = AlertStore(tmp_path / "alerts.sqlite")
    yield s
    s.close()


def test_limit_is_ten(store):
    for n in range(alert_store.MAX_PHONES):
        store.add_phone(sub(n), "iPhone", NOW)
    with pytest.raises(PhoneLimit):
        store.add_phone(sub(99), "iPhone", NOW)


def test_same_endpoint_keeps_the_choices_and_history_with_a_new_id(store):
    """The phone turned alerts on again after its page lost its id: a new id and secret, the rest as it was."""
    first, old_secret = store.add_phone(sub(1), "iPhone", NOW)
    store.set_choices(first, old_secret, {"co2", "offline"}, (23, 6))
    store.take_baselines(lambda phone: phone.sent.update({"rh": Sent(NOW)}))
    later = NOW.replace(day=22)
    second, secret = store.add_phone(sub(1), "iPhone", later)
    assert second != first and len(store.phones()) == 1
    back = store.phones()[0]
    assert back.id == second and back.muted == {"co2", "offline"} and back.quiet == (23, 6)
    assert back.sent == {"rh": Sent(NOW)} and back.since == NOW and back.baseline == frozenset()
    assert store.choices(second, secret) == {"muted": ["co2", "offline"], "quiet": [23, 6]}
    with pytest.raises(PermissionError):
        store.verify(first, old_secret)


def test_same_endpoint_replaces_even_at_the_limit(store):
    for n in range(alert_store.MAX_PHONES):
        store.add_phone(sub(n), "iPhone", NOW)
    store.add_phone(sub(0), "iPhone", NOW)
    assert len(store.phones()) == alert_store.MAX_PHONES


def test_sent_survives_reopen(tmp_path):
    path = tmp_path / "alerts.sqlite"
    store = AlertStore(path)
    pid, secret = store.add_phone(sub(1), "iPhone", NOW)
    store.set_choices(pid, secret, {"co2"}, (23, 6))
    phone = store.phones()[0]
    phone.sent = {"co2": Sent(NOW), "pm:2026-11-20T12:00:00+00:00": Sent(NOW, NOW), "offline:indoor": Sent(NOW)}
    store.save_sent([phone])
    store.close()
    again = AlertStore(path)
    back = again.phones()[0]
    again.close()
    assert back.sent == phone.sent
    assert back.muted == frozenset({"co2"}) and back.quiet == (23, 6) and back.since == NOW


def test_file_is_owner_only(tmp_path):
    path = tmp_path / "alerts.sqlite"
    store = AlertStore(path)
    store.add_phone(sub(1), "iPhone", NOW)
    store.close()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_wrong_secret_refused(store):
    pid, secret = store.add_phone(sub(1), "iPhone", NOW)
    with pytest.raises(PermissionError):
        store.set_choices(pid, "wrong", {"co2"}, (22, 7))
    with pytest.raises(PermissionError):
        store.remove(pid, "wrong")
    assert store.phones()[0].muted == frozenset()
    store.remove(pid, secret)
    assert store.phones() == []


def test_remove_without_secret_is_for_cleanup(store):
    pid, _ = store.add_phone(sub(1), "iPhone", NOW)
    store.remove(pid)
    assert store.phones() == []


def test_subscription_returns_the_stored_one(store):
    pid, _ = store.add_phone(sub(1), "iPhone", NOW)
    assert store.subscription(pid) == sub(1)


def test_listing_has_no_endpoint_or_keys(store):
    pid, secret = store.add_phone(sub(1), "iPhone", NOW)
    rows = store.listing()
    assert rows == [{"id": pid, "device": "iPhone", "since": "2026-11-20", "time": "09:30"}]
    assert secret not in str(rows) and "push.example" not in str(rows)
    assert store.has(pid) and not store.has("nobody")


def test_save_sent_merges_keys_written_meanwhile(store):
    phone_id, _ = store.add_phone(sub(1), "iPhone", NOW)
    stale = store.phones()[0]   # what a check read at its start
    other = store.phones()[0]
    other.sent["pm:x"] = Sent(NOW)
    store.save_sent([other])    # written meanwhile (a seed)
    stale.sent["co2"] = Sent(NOW)
    store.save_sent([stale])
    assert set(store.phones()[0].sent) == {"co2", "pm:x"}


def test_a_new_phone_waits_for_its_baseline(store):
    pid, secret = store.add_phone(sub(1), "iPhone", NOW)
    assert store.phones()[0].baseline == {BASELINE_ALL}
    seen = []
    store.take_baselines(lambda phone: (seen.append(phone.baseline), phone.sent.update({"co2": Sent(NOW)})))
    assert seen == [{BASELINE_ALL}]
    back = store.phones()[0]
    assert back.baseline == frozenset() and back.sent == {"co2": Sent(NOW)}
    store.take_baselines(lambda phone: seen.append(phone.id))   # nothing waits: not called
    assert len(seen) == 1


def test_unmuting_waits_for_a_baseline_in_the_same_write(store):
    pid, secret = store.add_phone(sub(1), "iPhone", NOW)
    store.take_baselines(lambda phone: None)
    store.set_choices(pid, secret, {"co2", "rh", "offline"}, (22, 7))
    assert store.phones()[0].baseline == frozenset()   # muting needs no baseline
    store.set_choices(pid, secret, {"rh"}, (22, 7))
    back = store.phones()[0]
    assert back.muted == {"rh"} and back.baseline == {"co2", "offline"}


def test_a_file_from_before_baselines_needs_none(tmp_path):
    path = tmp_path / "alerts.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE phones (id TEXT PRIMARY KEY, secret_hash TEXT, endpoint TEXT UNIQUE, "
               "subscription TEXT, device TEXT, since TEXT, muted TEXT, quiet TEXT, sent TEXT)")
    db.execute("INSERT INTO phones VALUES ('p1', 'h', 'https://push.example/1', '{}', 'iPhone', ?, '[]', '[22, 7]', '{}')",
               (NOW.isoformat(),))
    db.commit()
    db.close()
    store = AlertStore(path)
    assert store.phones()[0].baseline == frozenset()
    new, _ = store.add_phone(sub(2), "iPhone", NOW)
    assert {p.id: p.baseline for p in store.phones()} == {"p1": frozenset(), new: {BASELINE_ALL}}
    store.close()


def test_clear_is_kept_and_old_rows_count_as_cleared(tmp_path):
    """The margin rule's mark survives a save; an action stored before the rule existed counts as cleared, so the
    upgrade holds nothing back."""
    store = AlertStore(tmp_path / "alerts.sqlite")
    pid, _ = store.add_phone(sub(1), "iPhone", NOW)
    phone = store.phones()[0]
    phone.sent["co2"] = Sent(NOW, NOW, clear=True)
    phone.sent["rh"] = Sent(NOW, NOW)
    store.save_sent([phone])
    back = store.phones()[0].sent
    assert back["co2"].clear and not back["rh"].clear
    store._db.execute("UPDATE phones SET sent=? WHERE id=?", ('{"co2": {"at": "%s", "left": "%s"}}' % (NOW.isoformat(), NOW.isoformat()), pid))
    store._db.commit()
    assert store.phones()[0].sent["co2"].clear
    store.close()
