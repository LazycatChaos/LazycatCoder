import pytest
from csv_export import export_csv_stream


class TestBasicExport:
    """Basic CSV export tests."""

    def test_export_header(self):
        """Should yield a header row."""
        data = [
            {"id": 1, "name": "Alice"},
        ]
        gen = export_csv_stream(data, fieldnames=["id", "name"])
        header = next(gen)
        assert "id" in header
        assert "name" in header

    def test_export_single_row(self):
        """Should export a single row."""
        data = [
            {"id": 1, "name": "Alice"},
        ]
        chunks = list(export_csv_stream(data, fieldnames=["id", "name"]))
        assert len(chunks) == 2  # header + 1 row
        assert "Alice" in chunks[1]

    def test_export_multiple_rows(self):
        """Should export multiple rows."""
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Charlie"},
        ]
        chunks = list(export_csv_stream(data, fieldnames=["id", "name"]))
        assert len(chunks) == 4  # header + 3 rows
        assert "Alice" in chunks[1]
        assert "Bob" in chunks[2]
        assert "Charlie" in chunks[3]

    def test_export_empty_data(self):
        """Should yield only header for empty data."""
        chunks = list(export_csv_stream([], fieldnames=["id", "name"]))
        assert len(chunks) == 1  # header only


class TestStreaming:
    """Tests for streaming behavior."""

    def test_returns_generator(self):
        """Should return a generator, not a list."""
        gen = export_csv_stream([], fieldnames=["id"])
        assert hasattr(gen, "__next__")

    def test_lazy_evaluation(self):
        """Should not consume data until iterated."""
        consumed = []

        def lazy_data():
            consumed.append("started")
            yield {"id": 1, "name": "Alice"}

        gen = export_csv_stream(lazy_data(), fieldnames=["id", "name"])
        # At this point, the generator should exist but not have consumed data yet
        assert consumed == []

        # Only when we iterate should data be consumed
        next(gen)
        assert consumed == ["started"]
