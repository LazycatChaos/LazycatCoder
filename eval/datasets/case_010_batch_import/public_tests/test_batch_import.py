import sqlite3
import pytest
from batch_import import batch_import_users


@pytest.fixture
def db_conn(tmp_path):
    """Create a test database with users table."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        )
    """)
    conn.commit()
    yield conn
    conn.close()


class TestBasicImport:
    """Basic import tests."""

    def test_import_single_user(self, db_conn):
        """Should import a single user."""
        result = batch_import_users(db_conn, [
            {"username": "alice", "email": "alice@example.com", "role": "user"},
        ])
        assert result["imported"] == 1
        assert result["errors"] == []

        row = db_conn.execute("SELECT * FROM users WHERE username='alice'").fetchone()
        assert row is not None

    def test_import_multiple_users(self, db_conn):
        """Should import multiple users."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "bob", "email": "bob@example.com", "role": "admin"},
            {"username": "charlie", "email": "charlie@example.com"},
        ]
        result = batch_import_users(db_conn, users)
        assert result["imported"] == 3

        count = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 3

    def test_import_default_role(self, db_conn):
        """Should use 'user' as default role when not specified."""
        batch_import_users(db_conn, [
            {"username": "alice", "email": "alice@example.com"},
        ])
        row = db_conn.execute("SELECT role FROM users WHERE username='alice'").fetchone()
        assert row[0] == "user"


class TestAtomicMode:
    """Tests for atomic mode (continue_on_error=False)."""

    def test_rollback_on_error(self, db_conn):
        """Should rollback all inserts when one fails."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "bob", "email": "bob@example.com"},
            {"username": "alice", "email": "duplicate@example.com"},  # duplicate!
            {"username": "charlie", "email": "charlie@example.com"},
        ]
        with pytest.raises(Exception):
            batch_import_users(db_conn, users, continue_on_error=False)

        # All should be rolled back
        count = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 0

    def test_empty_list(self, db_conn):
        """Should handle empty list gracefully."""
        result = batch_import_users(db_conn, [])
        assert result["imported"] == 0
        assert result["errors"] == []
