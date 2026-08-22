"""Tests for persistent Chat store with bloat controls."""

import time

from mcptap.chat_store import PersistentChatStore


def test_persistent_store_roundtrip(tmp_path):
    store = PersistentChatStore(db_path=tmp_path / "test.db")
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    store.store("resp_1", messages)
    assert store.get("resp_1") == messages
    assert store.count() == 1


def test_persistent_store_survives_reopen(tmp_path):
    db = tmp_path / "test.db"
    s1 = PersistentChatStore(db_path=db)
    s1.store("resp_1", [{"role": "user", "content": "hello"}])
    s1.close()
    s2 = PersistentChatStore(db_path=db)
    assert s2.get("resp_1") == [{"role": "user", "content": "hello"}]


def test_ttl_expiry(tmp_path):
    store = PersistentChatStore(db_path=tmp_path / "test.db", ttl_seconds=1)
    store.store("resp_1", [{"role": "user", "content": "hello"}])
    # Expire manually
    store._connect().execute("UPDATE chat_histories SET expires_at = ?", (time.time() - 10,))
    store._connect().commit()
    assert store.get("resp_1") is None
    assert store.count() == 0


def test_max_rows_eviction(tmp_path):
    store = PersistentChatStore(db_path=tmp_path / "test.db", max_rows=2)
    store.store("resp_1", [{"role": "user", "content": "a"}])
    time.sleep(0.01)
    store.store("resp_2", [{"role": "user", "content": "b"}])
    time.sleep(0.01)
    store.store("resp_3", [{"role": "user", "content": "c"}])
    assert store.count() == 2
    assert store.get("resp_1") is None
    assert store.get("resp_3") is not None


def test_max_bytes_eviction(tmp_path):
    store = PersistentChatStore(db_path=tmp_path / "test.db", max_bytes=500)
    # Each entry ~ gzip of small message ~60 bytes, so 10 entries exceed 500
    for i in range(10):
        store.store(f"resp_{i}", [{"role": "user", "content": "x" * 200}])
    assert store.total_bytes() <= 500
    assert store.count() < 10


def test_oversized_entry_dropped(tmp_path):
    store = PersistentChatStore(db_path=tmp_path / "test.db", max_entry_bytes=100)
    # Use non-compressible payload to exceed limit after gzip
    import os

    large = [{"role": "user", "content": os.urandom(5000).hex()}]
    store.store("resp_big", large)
    assert store.get("resp_big") is None
    assert store.count() == 0


def test_store_response_persists(tmp_path):
    store = PersistentChatStore(db_path=tmp_path / "test.db")
    messages = [{"role": "user", "content": "hello"}]
    body = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
    store.store_response("resp_1", messages, body)
    loaded = store.get("resp_1")
    assert loaded is not None
    assert loaded[-1]["content"] == "hi"
    assert loaded[0]["content"] == "hello"


def test_purge_expired(tmp_path):
    store = PersistentChatStore(db_path=tmp_path / "test.db", ttl_seconds=1)
    store.store("resp_1", [{"role": "user", "content": "a"}])
    store._connect().execute("UPDATE chat_histories SET expires_at = ?", (time.time() - 10,))
    store._connect().commit()
    assert store.purge_expired() == 1
    assert store.count() == 0
