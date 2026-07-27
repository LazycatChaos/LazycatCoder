import copy


def load_config(data: dict) -> dict:
    """Load configuration from a dictionary.

    Returns a deep copy to prevent accidental mutation.
    """
    return copy.deepcopy(data)


def merge_configs(default: dict, override: dict) -> dict:
    """Deep merge two configuration dictionaries.

    Values from `override` take precedence. Nested dicts are merged
    recursively. Non-dict values are replaced entirely.

    Args:
        default: Base configuration.
        override: Override configuration.

    Returns:
        A new dict with merged values. Input dicts are not mutated.
    """
    result = copy.deepcopy(default)

    for key, override_value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(override_value, dict):
            result[key] = merge_configs(result[key], override_value)
        else:
            result[key] = copy.deepcopy(override_value)

    return result
