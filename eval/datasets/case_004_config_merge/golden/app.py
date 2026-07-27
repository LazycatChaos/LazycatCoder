from config_utils import load_config, merge_configs

DEFAULT_CONFIG = {
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "myapp",
    },
    "logging": {
        "level": "INFO",
        "format": "%(message)s",
    },
    "debug": False,
}


def get_config(user_overrides: dict | None = None) -> dict:
    """Get application configuration.

    Uses deep merge to properly handle nested overrides.
    """
    config = load_config(DEFAULT_CONFIG)
    if user_overrides:
        config = merge_configs(config, user_overrides)
    return config
