import os
import sqlite3
import tempfile

import pytest
from user_db import get_user, search_users


@pytest.fixture
def db_path(tmp_path):
    """Create a test database with sample users."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        )
    """)
    conn.execute("""
        CREATE TABLE secrets (
            id INTEGER PRIMARY KEY,
            secret_key TEXT NOT NULL
        )
    """)
    conn.executemany(
        "INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
        [
            ("alice", "alice@example.com", "user"),
            ("bob", "bob@example.com", "admin"),
            ("charlie", "charlie@example.com", "user"),
        ],
    )
    conn.execute("INSERT INTO secrets (secret_key) VALUES ('TOP_SECRET_KEY_12345')")
    conn.commit()
    conn.close()
    return path


class TestUnionInjection:
    """Tests for UNION-based SQL injection."""

    def test_union_injection_get_user(self, db_path):
        """UNION injection should not leak data from other tables."""
        user = get_user(db_path, "' UNION SELECT 1, username, secret_key, role FROM secrets --")
        # Should return None, not leak secrets
        assert user is None

    def test_union_injection_search(self, db_path):
        """UNION injection in search should not leak data."""
        results = search_users(db_path, "' UNION SELECT 1, username, secret_key, role FROM secrets --")
        # Should return empty list, not leak secrets
        assert len(results) == 0

    def test_union_injection_leak_users(self, db_path):
        """UNION injection should not allow enumerating all users."""
        # Try to get all users via UNION
        results = search_users(db_path, "' UNION SELECT * FROM users --")
        # Should return empty, not all users
        assert len(results) == 0


class TestCommentInjection:
    """Tests for comment-based injection."""

    def test_comment_bypass_get_user(self, db_path):
        """Comment injection should not bypass authentication."""
        user = get_user(db_path, "admin' --")
        assert user is None

    def test_comment_bypass_search(self, db_path):
        """Comment injection in search should not return all users."""
        results = search_users(db_path, "' OR 1=1 --")
        assert len(results) == 0

    def test_hash_comment_injection(self, db_path):
        """Hash comment injection should be blocked."""
        user = get_user(db_path, "' OR 1=1 #")
        assert user is None


class TestStackedQueries:
    """Tests for stacked query injection."""

    def test_stacked_query_insert(self, db_path):
        """Stacked INSERT should not work."""
        try:
            get_user(db_path, "'; INSERT INTO users (username, email, role) VALUES ('hacker', 'h@ck.com', 'admin') --")
        except Exception:
            pass  # May raise exception, that's fine
        # Verify no hacker user was created
        user = get_user(db_path, "hacker")
        assert user is None

    def test_stacked_query_update(self, db_path):
        """Stacked UPDATE should not work."""
        try:
            get_user(db_path, "'; UPDATE users SET role='admin' WHERE username='alice' --")
        except Exception:
            pass
        # Verify alice's role wasn't changed
        user = get_user(db_path, "alice")
        assert user is not None
        assert user["role"] == "user"

    def test_stacked_query_delete(self, db_path):
        """Stacked DELETE should not work."""
        try:
            get_user(db_path, "'; DELETE FROM users WHERE username='bob' --")
        except Exception:
            pass
        # Verify bob still exists
        user = get_user(db_path, "bob")
        assert user is not None


class TestParameterizedQuery:
    """Tests to verify parameterized queries are used."""

    def test_uses_parameterized_query_get_user(self):
        """get_user must use parameterized queries (? placeholders)."""
        import inspect
        source = inspect.getsource(get_user)
        assert "?" in source, "get_user must use parameterized queries with ? placeholders"
        # Should NOT use f-string or .format() for SQL construction
        assert "f\"" not in source and "f'" not in source, (
            "get_user must not use f-strings for SQL queries"
        )

    def test_uses_parameterized_query_search(self):
        """search_users must use parameterized queries."""
        import inspect
        source = inspect.getsource(search_users)
        assert "?" in source, "search_users must use parameterized queries with ? placeholders"
        assert "f\"" not in source and "f'" not in source, (
            "search_users must not use f-strings for SQL queries"
        )


class TestEdgeCases:
    """Edge case tests."""

    def test_username_with_special_chars(self, db_path):
        """Username with special characters should work safely."""
        # Add a user with special chars
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
                     ("user'name", "special@example.com", "user"))
        conn.commit()
        conn.close()

        user = get_user(db_path, "user'name")
        assert user is not None
        assert user["email"] == "special@example.com"

    def test_empty_username(self, db_path):
        """Empty username should return None."""
        user = get_user(db_path, "")
        assert user is None

    def test_empty_search_keyword(self, db_path):
        """Empty search keyword should return all users (safely)."""
        results = search_users(db_path, "")
        assert len(results) == 3  # alice, bob, charlie

    def test_unicode_username(self, db_path):
        """Unicode username should work safely."""
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
                     ("用户", "unicode@example.com", "user"))
        conn.commit()
        conn.close()

        user = get_user(db_path, "用户")
        assert user is not None
        assert user["email"] == "unicode@example.com"

    def test_null_byte_injection(self, db_path):
        """Null byte in username should be handled safely."""
        user = get_user(db_path, "admin\x00' OR '1'='1")
        assert user is None

    def test_like_wildcard_injection(self, db_path):
        """LIKE wildcards in keyword should be treated literally."""
        # Add a user with literal % in name
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
                     ("100%_user", "wildcard@example.com", "user"))
        conn.commit()
        conn.close()

        # Searching for "%" should not match all users
        results = search_users(db_path, "%")
        # Should only match the user with literal % in name
        assert len(results) == 1
        assert results[0]["username"] == "100%_user"
