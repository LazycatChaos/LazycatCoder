import json
import os
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


def test_empty_data(tmp_path):
    """Empty dict and list should be written correctly."""
    path = tmp_path / "empty.json"
    write_json_atomic(str(path), {})
    
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    assert result == {}
