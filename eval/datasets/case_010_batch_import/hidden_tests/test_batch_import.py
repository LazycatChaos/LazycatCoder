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


class TestContinueOnError:
    """Tests for continue_on_error=True mode."""

    def test_partial_success(self, db_conn):
        """Should import valid rows and skip invalid ones."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "bob", "email": "bob@example.com"},
            {"username": "alice", "email": "duplicate@example.com"},  # duplicate!
            {"username": "charlie", "email": "charlie@example.com"},
        ]
        result = batch_import_users(db_conn, users, continue_on_error=True)

        assert result["imported"] == 3
        assert len(result["errors"]) == 1
        assert "alice" in result["errors"][0]

        # Verify 3 users were actually inserted
        count = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 3

    def test_all_fail(self, db_conn):
        """Should report all errors when all rows fail."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "alice", "email": "dup1@example.com"},
            {"username": "alice", "email": "dup2@example.com"},
        ]
        # First one succeeds, rest fail
        result = batch_import_users(db_conn, users, continue_on_error=True)
        assert result["imported"] == 1
        assert len(result["errors"]) == 2

    def test_no_errors_on_success(self, db_conn):
        """Should return empty errors list when all succeed."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "bob", "email": "bob@example.com"},
        ]
        result = batch_import_users(db_conn, users, continue_on_error=True)
        assert result["imported"] == 2
        assert result["errors"] == []

    def test_errors_contain_username(self, db_conn):
        """Error messages should identify which user failed."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "alice", "email": "dup@example.com"},
        ]
        result = batch_import_users(db_conn, users, continue_on_error=True)
        assert len(result["errors"]) == 1
        assert "alice" in result["errors"][0]


class TestAtomicModeAdvanced:
    """Advanced tests for atomic mode."""

    def test_rollback_preserves_existing_data(self, db_conn):
        """Rollback should not affect pre-existing data."""
        # Insert a user first
        db_conn.execute(
            "INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
            ("existing", "existing@example.com", "admin"),
        )
        db_conn.commit()

        # Try to batch import with a duplicate
        users = [
            {"username": "new1", "email": "new1@example.com"},
            {"username": "existing", "email": "dup@example.com"},  # duplicate!
            {"username": "new2", "email": "new2@example.com"},
        ]
        with pytest.raises(Exception):
            batch_import_users(db_conn, users, continue_on_error=False)

        # Existing user should still be there
        count = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 1

    def test_commit_on_success(self, db_conn):
        """Should commit when all inserts succeed."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "bob", "email": "bob@example.com"},
        ]
        result = batch_import_users(db_conn, users, continue_on_error=False)
        assert result["imported"] == 2

        # Verify data persists (not just in uncommitted transaction)
        count = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 2

    def test_missing_required_field(self, db_conn):
        """Missing required field should trigger rollback."""
        users = [
            {"username": "alice", "email": "alice@example.com"},
            {"username": "bob"},  # missing email!
            {"username": "charlie", "email": "charlie@example.com"},
        ]
        with pytest.raises(Exception):
            batch_import_users(db_conn, users, continue_on_error=False)

        count = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 0


class TestEdgeCases:
    """Edge case tests."""

    def test_large_batch(self, db_conn):
        """Should handle large batches efficiently."""
        users = [
            {"username": f"user_{i}", "email": f"user_{i}@example.com"}
            for i in range(100)
        ]
        result = batch_import_users(db_conn, users)
        assert result["imported"] == 100

    def test_special_characters(self, db_conn):
        """Should handle special characters in user data."""
        users = [
            {"username": "user'name", "email": "special@example.com"},
            {"username": "user; DROP TABLE users;--", "email": "inject@example.com"},
        ]
        result = batch_import_users(db_conn, users)
        assert result["imported"] == 2

        # Verify table still exists
        count = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 2

    def test_unicode_data(self, db_conn):
        """Should handle unicode data."""
        users = [
            {"username": "用户", "email": "unicode@example.com"},
            {"username": "日本語", "email": "jp@example.com"},
        ]
        result = batch_import_users(db_conn, users)
        assert result["imported"] == 2

    def test_empty_list_continue_on_error(self, db_conn):
        """Empty list with continue_on_error=True should work."""
        result = batch_import_users(db_conn, [], continue_on_error=True)
        assert result["imported"] == 0
        assert result["errors"] == []

    def test_mixed_valid_invalid_continue_on_error(self, db_conn):
        """Interleaved valid/invalid rows with continue_on_error."""
        users = [
            {"username": "alice", "email": "alice@example.com"},  # valid
            {"username": "alice", "email": "dup1@example.com"},   # invalid
            {"username": "bob", "email": "bob@example.com"},      # valid
            {"username": "bob", "email": "dup2@example.com"},     # invalid
            {"username": "charlie", "email": "charlie@example.com"},  # valid
        ]
        result = batch_import_users(db_conn, users, continue_on_error=True)
        assert result["imported"] == 3
        assert len(result["errors"]) == 2
