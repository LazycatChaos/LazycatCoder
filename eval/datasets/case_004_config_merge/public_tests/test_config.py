import pytest
from config_utils import merge_configs


class TestMergeConfigsPublic:
    """Public tests for basic config merge behavior."""

    def test_simple_merge(self):
        default = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = merge_configs(default, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        default = {"db": {"host": "localhost", "port": 5432}, "debug": False}
        override = {"db": {"port": 3306}, "debug": True}
        result = merge_configs(default, override)
        assert result == {"db": {"host": "localhost", "port": 3306}, "debug": True}

    def test_empty_override(self):
        default = {"a": 1, "b": {"c": 2}}
        result = merge_configs(default, {})
        assert result == default

    def test_empty_default(self):
        override = {"a": 1, "b": {"c": 2}}
        result = merge_configs({}, override)
        assert result == override

    def test_no_mutation_of_inputs(self):
        default = {"db": {"host": "localhost"}}
        override = {"db": {"port": 3306}}
        original_default = {"db": {"host": "localhost"}}
        original_override = {"db": {"port": 3306}}
        merge_configs(default, override)
        assert default == original_default
        assert override == original_override
