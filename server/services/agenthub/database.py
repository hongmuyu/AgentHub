"""SQLite connection foundation for AgentHub business data."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Iterator


_DEFAULT_DB_PATH = Path("data/agenthub.db")
_BUSY_TIMEOUT_MS = 5000


class AgentHubDatabase:
    """Own a separate SQLite file; entity tables are added by later tasks."""

    def __init__(self, path: Path | str = _DEFAULT_DB_PATH) -> None:
        self.path = Path(path).resolve()
        self._init_lock = Lock()
        self._initialized = False

    def initialize(self) -> None:
        with self._init_lock:
            if self._initialized and self.path.is_file():
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None
            )
            try:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode.lower() != "wal":
                    raise RuntimeError("AgentHub database requires WAL mode")
            finally:
                connection.close()
            self._initialized = True

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = sqlite3.connect(
            self.path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
