import os
import sys
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


@pytest.fixture
def sandbox_with_symlink(tmp_path):
    """Create a sandbox with a symlink pointing outside. Skip if symlinks not available."""
    (tmp_path / "readme.txt").write_text("Hello, world!", encoding="utf-8")

    outside_file = tmp_path.parent / "outside_secret_symlink.txt"
    outside_file.write_text("SECRET", encoding="utf-8")

    symlink_path = tmp_path / "evil_link"
    try:
        symlink_path.symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks not available (requires admin on Windows)")

    yield tmp_path

    # Cleanup
    if symlink_path.is_symlink():
        symlink_path.unlink()
    if outside_file.exists():
        outside_file.unlink()


class TestSymlinkEscape:
    """Tests for symlink-based escape attacks."""

    def test_symlink_escape_blocked(self, sandbox_with_symlink):
        """Symlink pointing outside base_dir should be blocked."""
        with pytest.raises(ValueError):
            read_user_file(str(sandbox_with_symlink), "evil_link")

    def test_symlink_to_inside_allowed(self, sandbox_dir):
        """Symlink pointing to a file inside base_dir should be allowed."""
        target = sandbox_dir / "readme.txt"
        symlink_path = sandbox_dir / "link_to_readme"
        try:
            symlink_path.symlink_to(target)
        except OSError:
            pytest.skip("Symlinks not available (requires admin on Windows)")

        content = read_user_file(str(sandbox_dir), "link_to_readme")
        assert content == "Hello, world!"


class TestAdvancedTraversal:
    """Advanced path traversal tests."""

    def test_double_encoded_traversal(self, sandbox_dir):
        """Double-encoded traversal should be blocked or result in file not found."""
        # The path should either be blocked as traversal or result in FileNotFoundError
        # (since the literal file doesn't exist)
        with pytest.raises((ValueError, FileNotFoundError, OSError)):
            read_user_file(str(sandbox_dir), "..%252F..%252Fetc%252Fpasswd")

    def test_null_byte_injection(self, sandbox_dir):
        """Null byte injection should be blocked or handled safely."""
        with pytest.raises((ValueError, OSError)):
            read_user_file(str(sandbox_dir), "readme.txt\x00../secret")

    def test_traversal_in_middle_of_path(self, sandbox_dir):
        """Traversal embedded in the middle of a path should be blocked."""
        with pytest.raises(ValueError):
            read_user_file(str(sandbox_dir), f"docs{os.sep}..{os.sep}..{os.sep}etc{os.sep}passwd")

    def test_windows_style_traversal(self, sandbox_dir):
        """Backslash traversal on any platform should be blocked or handled."""
        with pytest.raises((ValueError, FileNotFoundError, OSError)):
            read_user_file(str(sandbox_dir), "..\\..\\etc\\passwd")


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_filename(self, sandbox_dir):
        """Empty filename should raise an error."""
        with pytest.raises((ValueError, OSError)):
            read_user_file(str(sandbox_dir), "")

    def test_dot_filename(self, sandbox_dir):
        """Reading '.' (current directory) should raise an error."""
        with pytest.raises((ValueError, OSError, IsADirectoryError, PermissionError)):
            read_user_file(str(sandbox_dir), ".")

    def test_filename_with_leading_slash(self, sandbox_dir):
        """Filename starting with / should be blocked."""
        with pytest.raises((ValueError, FileNotFoundError, OSError)):
            read_user_file(str(sandbox_dir), "/etc/passwd")

    def test_base_dir_with_trailing_slash(self, sandbox_dir):
        """Base dir with trailing slash should still work."""
        content = read_user_file(str(sandbox_dir) + os.sep, "readme.txt")
        assert content == "Hello, world!"

    def test_unicode_filename(self, sandbox_dir):
        """Unicode filename should work if file exists."""
        (sandbox_dir / "文件.txt").write_text("中文内容", encoding="utf-8")
        content = read_user_file(str(sandbox_dir), "文件.txt")
        assert content == "中文内容"

    def test_large_file_read(self, sandbox_dir):
        """Large file should be readable."""
        large_content = "x" * 100000
        (sandbox_dir / "large.txt").write_text(large_content, encoding="utf-8")
        content = read_user_file(str(sandbox_dir), "large.txt")
        assert content == large_content

    def test_binary_file_raises_no_error(self, sandbox_dir):
        """Reading a binary file should not crash (may decode with errors)."""
        (sandbox_dir / "binary.dat").write_bytes(bytes(range(256)))
        # Should not raise — just read the content
        content = read_user_file(str(sandbox_dir), "binary.dat")
        assert isinstance(content, str)
