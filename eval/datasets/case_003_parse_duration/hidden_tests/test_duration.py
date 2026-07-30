import pytest
from time_utils import parse_duration


class TestParseDurationHidden:
    """Hidden tests that the Agent cannot see."""

    def test_minutes_with_spaces(self):
        """Bug: the reconstruction check fails when input has spaces between units."""
        # The bug is in the reconstruction validation:
        # "1h 30m" -> matches are [('1','h'), ('30','m')]
        # reconstructed = "1h30m" != "1h 30m" -> raises ValueError
        # But this should be a valid input!
        assert parse_duration("1h 30m") == 5400

    def test_spaces_between_units(self):
        """Spaces between time units should be allowed."""
        assert parse_duration("2d 3h") == 183600

    def test_spaces_around_units(self):
        """Spaces around the number-unit pair should be handled."""
        assert parse_duration("30 s") == 30

    def test_mixed_spaces(self):
        """Mixed spacing should work."""
        assert parse_duration("1h 30m 15s") == 5415

    def test_simple_with_space(self):
        """Simple case with space."""
        assert parse_duration("5 m") == 300

    def test_no_space_still_works(self):
        """Ensure no-space format still works after fix."""
        assert parse_duration("1h30m") == 5400
        assert parse_duration("2d3h") == 183600

    def test_single_unit_with_space(self):
        """Single unit with space."""
        assert parse_duration("2 h") == 7200

    def test_complex_with_spaces(self):
        """Complex duration with various spacing."""
        assert parse_duration("1d 2h 30m 15s") == 95415
