import os
import tempfile

import pytest
from file_reader import read_user_file


@pytest.fixture
def sandbox_dir(tmp_path):
    """Create a sandbox directory with test files."""
    (tmp_path / "readme.txt").write_text("Hello, world!", encoding="utf-8")
    subdir = tmp_path / "docs"
    subdir.mkdir()
    (subdir / "guide.txt").write_text("User guide content", encoding="utf-8")
    (tmp_path / "data.csv").write_text("a,b,c\n1,2,3", encoding="utf-8")
    return tmp_path


class TestBasicRead:
    """Basic file reading tests."""

    def test_read_file_in_base_dir(self, sandbox_dir):
        """Should read a file that exists in the base directory."""
        content = read_user_file(str(sandbox_dir), "readme.txt")
        assert content == "Hello, world!"

    def test_read_file_in_subdirectory(self, sandbox_dir):
        """Should read a file in a subdirectory."""
        content = read_user_file(str(sandbox_dir), "docs/guide.txt")
        assert content == "User guide content"

    def test_file_not_found(self, sandbox_dir):
        """Should raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            read_user_file(str(sandbox_dir), "nonexistent.txt")


class TestPathTraversal:
    """Tests for path traversal attacks."""

    def test_simple_traversal_blocked(self, sandbox_dir):
        """Simple ../ traversal should be blocked."""
        with pytest.raises(ValueError):
            read_user_file(str(sandbox_dir), "../secret.txt")

    def test_double_traversal_blocked(self, sandbox_dir):
        """Multiple ../ should be blocked."""
        with pytest.raises(ValueError):
            read_user_file(str(sandbox_dir), "../../etc/passwd")

    def test_traversal_with_valid_prefix(self, sandbox_dir):
        """Traversal hidden behind a valid prefix should be blocked."""
        with pytest.raises(ValueError):
            read_user_file(str(sandbox_dir), f"docs{os.sep}..{os.sep}..{os.sep}etc{os.sep}passwd")

    def test_absolute_path_blocked(self, sandbox_dir):
        """Absolute paths should be blocked."""
        with pytest.raises((ValueError, OSError)):
            read_user_file(str(sandbox_dir), "/etc/passwd")
