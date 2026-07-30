import re


def parse_duration(duration_str: str) -> int:
    """Parse a human-readable duration string into total seconds.

    Supported formats:
        "30s"    -> 30 seconds
        "5m"     -> 300 seconds
        "2h"     -> 7200 seconds
        "1d"     -> 86400 seconds
        "1h30m"  -> 5400 seconds
        "2d3h"   -> 183600 seconds

    Args:
        duration_str: A string like "1h30m", "45s", "2d", etc.

    Returns:
        Total number of seconds as an integer.

    Raises:
        ValueError: If the string cannot be parsed.
    """
    if not duration_str or not duration_str.strip():
        raise ValueError("Empty duration string")

    duration_str = duration_str.strip().lower()
    total_seconds = 0

    # Match number + unit pairs
    pattern = r'(\d+)([smhd])'
    matches = re.findall(pattern, duration_str)

    if not matches:
        raise ValueError(f"Cannot parse duration: {duration_str}")

    # Verify the entire string was consumed
    reconstructed = ''.join(f'{num}{unit}' for num, unit in matches)
    if reconstructed != duration_str:
        raise ValueError(f"Invalid duration format: {duration_str}")

    unit_multipliers = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400,
    }

    for value, unit in matches:
        total_seconds += int(value) * unit_multipliers[unit]

    return total_seconds
