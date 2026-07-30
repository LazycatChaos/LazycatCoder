import copy


def load_config(data: dict) -> dict:
    """Load configuration from a dictionary.

    Returns a deep copy to prevent accidental mutation.
    """
    return copy.deepcopy(data)
