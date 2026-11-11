from server.application import create_app
import sqlite3

from server.database import (
    Database,
    DatabaseIntegrityError,
    DatabaseOperationalError,
    DatabaseProtocol,
    HybridRow,
    insert_id,
)
from server.config import Settings
from fastapi.testclient import TestClient


class RecordingDatabase:
    """Driver-neutral adapter wrapper used to prove application-level injection."""

    def __init__(self, inner):
        self.inner = inner
        self.initialized = False

    def read(self):
        return self.inner.read()

    def transaction(self):
        return self.inner.transaction()

    def initialize(self):
        self.inner.initialize()
        self.initialized = True

    def schema_version(self):
        return self.inner.schema_version()


def test_hybrid_row_supports_mapping_and_positional_access():
    row = HybridRow(("id", "name", "total"), (17, "Sample", 2500))

    assert row["id"] == 17
    assert row[0] == 17
    assert row[-1] == 2500
    assert dict(row) == {"id": 17, "name": "Sample", "total": 2500}
    assert list(row) == ["id", "name", "total"]


def test_hybrid_row_preserves_first_duplicate_column_and_rejects_invalid_access():
    row = HybridRow(("id", "id"), (17, 18))

    assert row["id"] == 17
    assert row[1] == 18
    try:
        row["missing"]
    except KeyError:
        pass
    else:
        raise AssertionError("Unknown column names must raise KeyError")
    try:
        row[1.5]
    except TypeError:
        pass
    else:
        raise AssertionError("Unsupported row key types must raise TypeError")


def test_hybrid_row_rejects_mismatched_column_and_value_counts():
    try:
        HybridRow(("id", "name"), (17,))
    except ValueError:
        pass
    else:
        raise AssertionError("Column and value counts must match")


def test_application_accepts_database_protocol_implementation(tmp_path):
    settings = Settings(
        env="development",
        db_path=tmp_path / "injected.sqlite3",
        data_dir=tmp_path,
        allowed_hosts=["testserver"],
        public_url="http://testserver",
        demo=False,
        email_mode="file",
        encryption_key="",
    )
    adapter = RecordingDatabase(Database(settings.db_path))
    assert isinstance(adapter, DatabaseProtocol)

    app = create_app(settings, database=adapter)

    assert adapter.initialized is True
    assert adapter.schema_version() == 6
    assert app.state.db is adapter
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["database"] == "ready"


def test_sqlite_exceptions_are_exposed_through_database_contract():
    assert DatabaseIntegrityError is sqlite3.IntegrityError
    assert DatabaseOperationalError is sqlite3.OperationalError


def test_insert_id_uses_returning_and_returns_generated_identifier(tmp_path):
    db = Database(tmp_path / "insert-returning.sqlite3")
    db.initialize()
    with db.transaction() as connection:
        connection.execute("CREATE TABLE generated_ids (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL)")
        first = insert_id(connection, "INSERT INTO generated_ids(value) VALUES(?)", ("first",))
        second = insert_id(connection, "INSERT INTO generated_ids(value) VALUES(?)", ("second",))
        assert first == 1
        assert second == 2
        assert connection.execute("SELECT value FROM generated_ids WHERE id=?", (second,)).fetchone()[0] == "second"


def test_insert_id_rejects_non_insert_and_existing_returning_clause(tmp_path):
    db = Database(tmp_path / "insert-returning-invalid.sqlite3")
    db.initialize()
    with db.transaction() as connection:
        for statement in ("SELECT 1", "INSERT INTO settings(key,value) VALUES(?,?) RETURNING id"):
            try:
                insert_id(connection, statement, ("key", "value") if statement.startswith("INSERT") else ())
            except ValueError:
                pass
            else:
                raise AssertionError("insert_id must reject unsupported statements")



def test_qmark_translation_replaces_only_parameter_placeholders():
    from server.database import qmark_to_format

    sql = """SELECT ?, '?', "?", column
             FROM items
             WHERE id=? AND note='it''s ?'
             -- ignore ?
             AND label=? /* outer ? /* nested ? */ still ? */
             AND body=$tag$ ? is literal $tag$"""
    translated = qmark_to_format(sql)

    assert translated.count("%s") == 3
    assert "'?'" in translated
    assert '"?"' in translated
    assert "-- ignore ?" in translated
    assert "/* outer ? /* nested ? */ still ? */" in translated
    assert "$tag$ ? is literal $tag$" in translated


def test_qmark_translation_handles_escaped_quotes_and_empty_dollar_tag():
    from server.database import qmark_to_format

    sql = r'''SELECT ?, E'escaped\'? still literal', $$ ? $$, "identifier?"'''
    translated = qmark_to_format(sql)

    assert translated.startswith("SELECT %s,")
    assert r"E'escaped\'? still literal'" in translated
    assert "$$ ? $$" in translated
    assert '"identifier?"' in translated


def test_qmark_translation_leaves_question_mark_operators_out_of_scope():
    from server.database import qmark_to_format

    # PostgreSQL JSON question-mark operators are intentionally unsupported
    # in this SQLite-originated query contract and must be identified explicitly.
    assert qmark_to_format("SELECT payload ? 'key'") == "SELECT payload %s 'key'"

def test_cursor_contract_exposes_affected_row_count(tmp_path):
    db = Database(tmp_path / "cursor-rowcount.sqlite3")
    db.initialize()
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO settings(key,value) VALUES(?,?)",
            ("rowcount_contract_test", "before"),
        )
        result = connection.execute(
            "UPDATE settings SET value=? WHERE key=?",
            ("after", "rowcount_contract_test"),
        )
        assert result.rowcount == 1

        missing = connection.execute(
            "UPDATE settings SET value=? WHERE key=?",
            ("after", "missing-rowcount-contract-test"),
        )
        assert missing.rowcount == 0

def test_existing_sqlite_v1_database_is_upgraded_without_losing_data(tmp_path):
    from server.database import SCHEMA_PATH

    path = tmp_path / "existing-v1.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        SCHEMA_PATH.read_text(encoding="utf-8")
        + "\nINSERT INTO settings(key,value) VALUES('migration-fixture','preserved');"
        + "\nPRAGMA user_version=1;"
    )
    legacy.commit()
    legacy.close()

    db = Database(path)
    assert db.schema_version() == 1
    db.initialize()
    assert db.schema_version() == 6

    with db.read() as connection:
        assert connection.execute(
            "SELECT value FROM settings WHERE key='migration-fixture'"
        ).fetchone()[0] == "preserved"
        for table in ("products", "order_items"):
            columns = {row["name"]: row for row in connection.execute(f"PRAGMA table_info({table})")}
            assert columns["condition"]["notnull"] == 1
            assert columns["condition_note"]["notnull"] == 1
            assert columns["condition"]["dflt_value"] == "'new'"
        gallery = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='product_images'"
        ).fetchone()[0]
        assert gallery == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='product_variants'"
        ).fetchone()[0] == 1

def test_conversation_migration_creates_private_append_only_message_tables(tmp_path):
    db = Database(tmp_path / "messages-schema.sqlite3")
    db.initialize()
    assert db.schema_version() == 6
    with db.read() as connection:
        tables = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"conversations", "messages"} <= tables
        triggers = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )}
        assert {
            "messages_sender_participant",
            "immutable_messages_update",
            "immutable_messages_delete",
        } <= triggers



def test_verified_review_migration_creates_review_and_report_guards(tmp_path):
    db = Database(tmp_path / "reviews-schema.sqlite3")
    db.initialize()
    assert db.schema_version() == 6
    with db.read() as connection:
        tables = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"reviews","review_reports"} <= tables
        triggers = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert {
            "review_purchase_eligibility",
            "immutable_reviews_update",
            "immutable_reviews_delete",
            "review_moderation_authority",
            "review_reports_not_author",
            "immutable_review_reports_update",
            "immutable_review_reports_delete",
            "review_report_moderation_authority",
        } <= triggers

