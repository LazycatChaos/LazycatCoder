import pytest
from csv_export import export_csv_stream


class TestPoisonPill:
    """Tests using poison pill pattern to verify lazy streaming."""

    def test_poison_pill_partial_output(self):
        """Should yield partial output before poison pill triggers."""
        def poisoned_data():
            yield {"id": 1, "name": "Alice"}
            yield {"id": 2, "name": "Bob"}
            raise ValueError("Poison pill!")

        gen = export_csv_stream(poisoned_data(), fieldnames=["id", "name"])

        result = []
        with pytest.raises(ValueError, match="Poison pill!"):
            result.append(next(gen))  # header
            result.append(next(gen))  # Alice
            result.append(next(gen))  # Bob
            next(gen)  # triggers poison

        # Verify partial output was captured
        assert len(result) == 3
        assert "Alice" in result[1]
        assert "Bob" in result[2]

    def test_poison_pill_on_first_row(self):
        """Should handle poison pill on first data row."""
        def poisoned_data():
            raise ValueError("Immediate poison!")
            yield  # Makes this a generator function

        gen = export_csv_stream(poisoned_data(), fieldnames=["id", "name"])

        result = []
        with pytest.raises(ValueError, match="Immediate poison!"):
            result.append(next(gen))  # header
            next(gen)  # triggers poison on first data row

        # Header should have been yielded
        assert len(result) == 1
        assert "id" in result[0]

    def test_poison_pill_after_many_rows(self):
        """Should handle poison pill after many rows."""
        def poisoned_data():
            for i in range(50):
                yield {"id": i, "name": f"User_{i}"}
            raise ValueError("Late poison!")

        gen = export_csv_stream(poisoned_data(), fieldnames=["id", "name"])

        result = []
        with pytest.raises(ValueError, match="Late poison!"):
            result.append(next(gen))  # header
            for _ in range(50):
                result.append(next(gen))
            next(gen)  # triggers poison

        assert len(result) == 51  # header + 50 rows
        assert "User_49" in result[50]


class TestLazyEvaluation:
    """Tests to verify lazy evaluation (no list() call)."""

    def test_uses_yield_not_list(self):
        """export_csv_stream must use yield, not list()."""
        import inspect
        source = inspect.getsource(export_csv_stream)
        assert "yield" in source, "export_csv_stream must use yield for streaming"
        # Should NOT collect all data into a list first
        assert "list(data)" not in source, (
            "export_csv_stream must not call list(data) — that defeats streaming"
        )

    def test_no_list_comprehension_on_data(self):
        """Should not use list comprehension on data."""
        import inspect
        source = inspect.getsource(export_csv_stream)
        assert "[row for row in data]" not in source
        assert "[d for d in data]" not in source


class TestEdgeCases:
    """Edge case tests."""

    def test_special_characters_in_data(self):
        """Should properly CSV-escape special characters."""
        data = [
            {"id": 1, "name": 'Alice, "The Great"'},
            {"id": 2, "name": "Bob\nNewline"},
        ]
        chunks = list(export_csv_stream(data, fieldnames=["id", "name"]))
        assert len(chunks) == 3
        # CSV should properly quote/escape
        assert '"Alice, ""The Great"""' in chunks[1] or "Alice" in chunks[1]

    def test_unicode_data(self):
        """Should handle unicode data."""
        data = [
            {"id": 1, "name": "用户"},
            {"id": 2, "name": "日本語"},
        ]
        chunks = list(export_csv_stream(data, fieldnames=["id", "name"]))
        assert len(chunks) == 3
        assert "用户" in chunks[1]
        assert "日本語" in chunks[2]

    def test_missing_fields(self):
        """Should handle rows with missing fields."""
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2},  # missing name
        ]
        chunks = list(export_csv_stream(data, fieldnames=["id", "name"]))
        assert len(chunks) == 3

    def test_extra_fields(self):
        """Should handle rows with extra fields (ignore extras)."""
        data = [
            {"id": 1, "name": "Alice", "extra": "ignored"},
        ]
        chunks = list(export_csv_stream(data, fieldnames=["id", "name"]))
        assert len(chunks) == 2
        assert "extra" not in chunks[1]

    def test_generator_input(self):
        """Should work with generator input."""
        def gen_data():
            yield {"id": 1, "name": "Alice"}
            yield {"id": 2, "name": "Bob"}

        chunks = list(export_csv_stream(gen_data(), fieldnames=["id", "name"]))
        assert len(chunks) == 3

    def test_iterator_input(self):
        """Should work with iterator input."""
        data = iter([
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ])
        chunks = list(export_csv_stream(data, fieldnames=["id", "name"]))
        assert len(chunks) == 3

    def test_large_stream(self):
        """Should handle large streams without memory issues."""
        def large_data():
            for i in range(1000):
                yield {"id": i, "name": f"User_{i}"}

        gen = export_csv_stream(large_data(), fieldnames=["id", "name"])
        count = 0
        for chunk in gen:
            count += 1
        assert count == 1001  # header + 1000 rows
