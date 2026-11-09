from pathlib import Path

import pytest

from server.database import Database, DatabaseProtocol


def test_sqlite_adapter_satisfies_application_database_contract(tmp_path):
    db = Database(tmp_path / "contract.sqlite3")
    assert isinstance(db, DatabaseProtocol)
    db.initialize()

    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO rate_limits(bucket, window_start, count) VALUES(?, ?, ?)",
            ("committed", 100, 1),
        )

    with db.read() as connection:
        row = connection.execute(
            "SELECT window_start, count FROM rate_limits WHERE bucket=?",
            ("committed",),
        ).fetchone()
        assert (row["window_start"], row["count"]) == (100, 1)


def test_sqlite_adapter_rolls_back_failed_transaction(tmp_path):
    db = Database(tmp_path / "rollback.sqlite3")
    db.initialize()

    with pytest.raises(RuntimeError, match="abort transaction"):
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO rate_limits(bucket, window_start, count) VALUES(?, ?, ?)",
                ("rolled-back", 200, 1),
            )
            raise RuntimeError("abort transaction")

    with db.read() as connection:
        assert connection.execute(
            "SELECT 1 FROM rate_limits WHERE bucket=?",
            ("rolled-back",),
        ).fetchone() is None
