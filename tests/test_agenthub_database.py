"""SQLite connection and transaction baseline for AgentHub."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from server.services.agenthub.database import AgentHubDatabase
from server.services.vuegraphs_storage import fetch_vuegraph_content, save_vuegraph_content


def test_initialize_is_idempotent_and_uses_a_separate_persistent_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy_db = tmp_path / "data" / "vuegraphs.db"
    monkeypatch.setenv("VUEGRAPHS_DB_PATH", str(legacy_db))
    save_vuegraph_content("existing.yaml", "unchanged")
    legacy_bytes = legacy_db.read_bytes()

    database = AgentHubDatabase()
    database.initialize()
    database.initialize()

    assert (tmp_path / "data" / "agenthub.db").is_file()
    with database.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        timeout_ms = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        assert 1000 <= timeout_ms <= 10000
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vuegraphs'"
        ).fetchone() is None

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    assert legacy_db.read_bytes() == legacy_bytes
    assert fetch_vuegraph_content("existing.yaml") == "unchanged"


def test_transaction_commit_is_visible_after_reopening_database(tmp_path):
    path = tmp_path / "agenthub.db"
    database = AgentHubDatabase(path)
    with database.transaction() as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample (id, value) VALUES (1, 'saved')")

    reopened = AgentHubDatabase(path)
    with reopened.connection() as connection:
        assert connection.execute("SELECT value FROM sample WHERE id = 1").fetchone() == ("saved",)


def test_constraint_failure_rolls_back_prior_writes_in_same_transaction(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child (parent_id INTEGER NOT NULL REFERENCES parent(id))"
        )

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute("INSERT INTO parent (id) VALUES (1)")
            connection.execute("INSERT INTO child (parent_id) VALUES (2)")

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM parent").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM child").fetchone() == (0,)


def test_application_exception_rolls_back_transaction(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="stop this write"):
        with database.transaction() as connection:
            connection.execute("INSERT INTO sample (value) VALUES ('partial')")
            raise RuntimeError("stop this write")

    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone() == (0,)


def test_concurrent_writers_serialize_without_losing_updates(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE counter (value INTEGER NOT NULL CHECK (value >= 0))")
        connection.execute("INSERT INTO counter (value) VALUES (0)")

    barrier = Barrier(4)

    def increment():
        barrier.wait(timeout=5)
        with database.transaction() as connection:
            value = connection.execute("SELECT value FROM counter").fetchone()[0]
            time.sleep(0.05)
            connection.execute("UPDATE counter SET value = ?", (value + 1,))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(increment) for _ in range(4)]
        for future in futures:
            future.result(timeout=10)

    with database.connection() as connection:
        assert connection.execute("SELECT value FROM counter").fetchone() == (4,)
