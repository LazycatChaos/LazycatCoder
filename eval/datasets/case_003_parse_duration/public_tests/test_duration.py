import pytest
from time_utils import parse_duration


class TestParseDurationPublic:
    """Public tests for basic duration parsing."""

    def test_single_unit_hours(self):
        assert parse_duration("2h") == 7200

    def test_single_unit_minutes(self):
        assert parse_duration("30m") == 1800

    def test_single_unit_seconds(self):
        assert parse_duration("45s") == 45

    def test_single_unit_days(self):
        assert parse_duration("1d") == 86400

    def test_combined_units_no_spaces(self):
        assert parse_duration("1h30m") == 5400

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            parse_duration("invalid")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_duration("")
