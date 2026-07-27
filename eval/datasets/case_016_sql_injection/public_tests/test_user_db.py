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
    conn.executemany(
        "INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
        [
            ("alice", "alice@example.com", "user"),
            ("bob", "bob@example.com", "admin"),
            ("charlie", "charlie@example.com", "user"),
        ],
    )
    conn.commit()
    conn.close()
    return path


class TestGetUser:
    """Tests for get_user function."""

    def test_get_existing_user(self, db_path):
        """Should return user data for existing username."""
        user = get_user(db_path, "alice")
        assert user is not None
        assert user["username"] == "alice"
        assert user["email"] == "alice@example.com"

    def test_get_nonexistent_user(self, db_path):
        """Should return None for non-existent username."""
        user = get_user(db_path, "nonexistent")
        assert user is None

    def test_get_user_returns_all_fields(self, db_path):
        """Should return all user fields."""
        user = get_user(db_path, "bob")
        assert user is not None
        assert user["id"] == 2
        assert user["role"] == "admin"


class TestSearchUsers:
    """Tests for search_users function."""

    def test_search_exact_match(self, db_path):
        """Should find user with exact keyword match."""
        results = search_users(db_path, "alice")
        assert len(results) == 1
        assert results[0]["username"] == "alice"

    def test_search_partial_match(self, db_path):
        """Should find users with partial keyword match."""
        results = search_users(db_path, "a")
        assert len(results) >= 2  # alice, charlie

    def test_search_no_match(self, db_path):
        """Should return empty list for no matches."""
        results = search_users(db_path, "zzz")
        assert results == []


class TestBasicInjection:
    """Basic SQL injection tests."""

    def test_injection_get_user(self, db_path):
        """Simple injection should not return extra users."""
        user = get_user(db_path, "' OR '1'='1")
        assert user is None

    def test_injection_search_users(self, db_path):
        """Injection in search should not return all users."""
        results = search_users(db_path, "' OR '1'='1")
        assert len(results) == 0
