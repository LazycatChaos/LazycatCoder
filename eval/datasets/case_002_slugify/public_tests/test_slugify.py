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
