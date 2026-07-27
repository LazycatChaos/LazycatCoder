import json
import os
import stat
import tempfile

import pytest
from file_utils import write_json_atomic


def test_write_new_file(tmp_path):
    """Writing to a new file should create it with correct content."""
    path = tmp_path / "data.json"
    data = {"name": "test", "value": 42}
    write_json_atomic(str(path), data)
    
    assert path.exists()
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    assert result == data


def test_replace_existing_file(tmp_path):
    """Replacing an existing file should update content."""
    path = tmp_path / "data.json"
    
    # Write initial data
    write_json_atomic(str(path), {"version": 1})
    
    # Replace with new data
    write_json_atomic(str(path), {"version": 2, "extra": "field"})
    
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    assert result == {"version": 2, "extra": "field"}


def test_exception_leaves_no_temp_file(tmp_path):
    """If writing fails, no temporary file should be left behind."""
    path = tmp_path / "data.json"
    
    # Create a custom object that can't be serialized
    class Unserializable:
        pass
    
    with pytest.raises(TypeError):
        write_json_atomic(str(path), {"bad": Unserializable()})
    
    # No temp files should remain
    temp_files = list(tmp_path.glob(".tmp_*"))
    assert len(temp_files) == 0, f"Leftover temp files: {temp_files}"


def test_target_not_corrupted_on_failure(tmp_path):
    """If write fails, existing target file should be unchanged."""
    path = tmp_path / "data.json"
    
    # Write valid initial data
    write_json_atomic(str(path), {"version": 1})
    
    # Try to write invalid data
    class Unserializable:
        pass
    
    with pytest.raises(TypeError):
        write_json_atomic(str(path), {"bad": Unserializable()})
    
    # Original file should be intact
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    assert result == {"version": 1}


@pytest.mark.skipif(os.name == "nt", reason="File permissions work differently on Windows")
def test_file_permissions_preserved(tmp_path):
    """If target file exists, its permissions should be preserved."""
    path = tmp_path / "data.json"
    
    # Create file with specific permissions
    with open(path, "w") as f:
        f.write("{}")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)  # 0o640
    
    # Write new data
    write_json_atomic(str(path), {"new": "data"})
    
    # Check permissions are preserved
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP


def test_write_unicode_json(tmp_path):
    """Unicode data should be written correctly."""
    path = tmp_path / "unicode.json"
    data = {
        "name": "日本語",
        "emoji": "🐱",
        "chinese": "中文测试",
    }
    write_json_atomic(str(path), data)
    
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    assert result == data


def test_nested_directory(tmp_path):
    """Should work when target is in a subdirectory."""
    subdir = tmp_path / "sub" / "dir"
    subdir.mkdir(parents=True)
    path = subdir / "data.json"
    
    write_json_atomic(str(path), {"nested": True})
    
    assert path.exists()
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    assert result == {"nested": True}


def test_empty_data(tmp_path):
    """Empty dict and list should be written correctly."""
    path = tmp_path / "empty.json"
    write_json_atomic(str(path), {})
    
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    assert result == {}
