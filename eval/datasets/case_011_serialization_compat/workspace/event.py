from dataclasses import dataclass
from datetime import datetime


@dataclass
class Event:
    """Represents a system event."""
    event_type: str
    message: str
    timestamp: datetime
    source: str = "unknown"
    severity: str = "INFO"


VALID_SEVERITIES = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def deserialize_event(data: dict) -> Event:
    """Deserialize an event from a dict payload."""
    event_type = data["event_type"]
    message = data["message"]
    timestamp_str = data["timestamp"]
    source = data["source"]
    severity = data["severity"]

    timestamp = datetime.fromisoformat(timestamp_str)

    return Event(
        event_type=event_type,
        message=message,
        timestamp=timestamp,
        source=source,
        severity=severity,
    )
