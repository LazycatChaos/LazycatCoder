import pytest
from config_utils import merge_configs, load_config
from app import get_config


class TestMergeConfigsDeep:
    """Test deep merge behavior."""

    def test_nested_merge(self):
        default = {"db": {"host": "localhost", "port": 5432}, "debug": False}
        override = {"db": {"port": 3306}, "debug": True}
        result = merge_configs(default, override)
        assert result == {"db": {"host": "localhost", "port": 3306}, "debug": True}

    def test_deeply_nested(self):
        default = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 10}}}
        result = merge_configs(default, override)
        assert result == {"a": {"b": {"c": 10, "d": 2}}}

    def test_no_mutation_of_inputs(self):
        default = {"db": {"host": "localhost"}}
        override = {"db": {"port": 3306}}
        result = merge_configs(default, override)
        assert default == {"db": {"host": "localhost"}}
        assert override == {"db": {"port": 3306}}
        assert result is not default
        assert result is not override

    def test_override_non_dict_value(self):
        default = {"key": "value"}
        override = {"key": 42}
        result = merge_configs(default, override)
        assert result == {"key": 42}

    def test_default_dict_override_non_dict(self):
        default = {"key": {"nested": 1}}
        override = {"key": "flat"}
        result = merge_configs(default, override)
        assert result == {"key": "flat"}

    def test_empty_override(self):
        default = {"a": 1, "b": {"c": 2}}
        result = merge_configs(default, {})
        assert result == default

    def test_empty_default(self):
        override = {"a": 1, "b": {"c": 2}}
        result = merge_configs({}, override)
        assert result == override

    def test_new_keys_added(self):
        default = {"a": 1}
        override = {"b": 2}
        result = merge_configs(default, override)
        assert result == {"a": 1, "b": 2}

    def test_list_values_replaced(self):
        """Lists should be replaced, not merged."""
        default = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = merge_configs(default, override)
        assert result == {"items": [4, 5]}


class TestGetConfigIntegration:
    """Test that app.py uses deep merge correctly."""

    def test_get_config_deep_merge(self):
        """Override only database port, keep other database settings."""
        config = get_config({"database": {"port": 3306}})
        assert config["database"]["port"] == 3306
        assert config["database"]["host"] == "localhost"
        assert config["database"]["name"] == "myapp"

    def test_get_config_nested_override(self):
        """Override nested logging format."""
        config = get_config({"logging": {"format": "%(asctime)s %(message)s"}})
        assert config["logging"]["format"] == "%(asctime)s %(message)s"
        assert config["logging"]["level"] == "INFO"

    def test_get_config_no_mutation(self):
        """Calling get_config should not mutate DEFAULT_CONFIG."""
        from app import DEFAULT_CONFIG
        original_host = DEFAULT_CONFIG["database"]["host"]
        get_config({"database": {"host": "remote"}})
        assert DEFAULT_CONFIG["database"]["host"] == original_host
