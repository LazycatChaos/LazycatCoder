from config_utils import load_config

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

    Currently does a shallow merge — this is the bug to fix.
    """
    config = load_config(DEFAULT_CONFIG)
    if user_overrides:
        config.update(user_overrides)
    return config
