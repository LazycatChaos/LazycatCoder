from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Event:
    """Represents a system event."""
    event_type: str
    message: str
    timestamp: datetime
    source: str = "unknown"
    severity: str = "INFO"


VALID_SEVERITIES = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _parse_timestamp(value) -> datetime:
    """Parse timestamp from ISO string or Unix timestamp."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        # Try ISO format
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            # Try parsing as numeric string (unix timestamp)
            try:
                ts = float(value)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, OSError):
                raise ValueError(f"Invalid timestamp format: {value}")
    raise ValueError(f"Invalid timestamp type: {type(value).__name__}")


def deserialize_event(data: dict) -> Event:
    """Deserialize an event from a dict payload.
    
    Handles backward compatibility:
    - Missing new fields use defaults
    - Unknown fields are ignored
    - Unknown severity falls back to INFO
    - Timestamp supports ISO string and Unix timestamp
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    # Required fields
    if "event_type" not in data:
        raise ValueError("Missing required field: event_type")
    if "message" not in data:
        raise ValueError("Missing required field: message")
    if "timestamp" not in data:
        raise ValueError("Missing required field: timestamp")

    event_type = data["event_type"]
    message = data["message"]
    timestamp = _parse_timestamp(data["timestamp"])

    # Optional fields with defaults (backward compat)
    source = data.get("source", "unknown")

    # Severity with fallback for unknown values
    raw_severity = data.get("severity", "INFO")
    severity = raw_severity if raw_severity in VALID_SEVERITIES else "INFO"

    return Event(
        event_type=event_type,
        message=message,
        timestamp=timestamp,
        source=source,
        severity=severity,
    )
