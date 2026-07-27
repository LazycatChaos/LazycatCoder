import pytest
from string_utils import slugify


class TestSlugifyBasic:
    """Basic slugify functionality tests."""

    def test_simple_sentence(self):
        assert slugify("Hello World") == "hello-world"

    def test_single_word(self):
        assert slugify("Python") == "python"

    def test_with_numbers(self):
        assert slugify("Python 3.14") == "python-314"

    def test_multiple_spaces(self):
        assert slugify("hello   world") == "hello-world"

    def test_leading_trailing_spaces(self):
        assert slugify("  hello world  ") == "hello-world"


class TestSlugifySpecialChars:
    """Special character handling tests."""

    def test_remove_special_chars(self):
        assert slugify("hello! world?") == "hello-world"

    def test_unicode_to_ascii(self):
        result = slugify("Café & Restaurant")
        assert result == "cafe-restaurant"

    def test_ampersand(self):
        assert slugify("rock & roll") == "rock-roll"

    def test_dots_and_commas(self):
        assert slugify("file.name, version 2") == "filename-version-2"


class TestSlugifyEdgeCases:
    """Edge case tests."""

    def test_empty_string(self):
        assert slugify("") == ""

    def test_only_special_chars(self):
        assert slugify("!!!@@@###") == ""

    def test_only_spaces(self):
        assert slugify("     ") == ""

    def test_consecutive_hyphens(self):
        assert slugify("---test---") == "test"

    def test_mixed_hyphens_and_spaces(self):
        assert slugify("hello - - world") == "hello-world"

    def test_already_slug(self):
        assert slugify("hello-world") == "hello-world"
