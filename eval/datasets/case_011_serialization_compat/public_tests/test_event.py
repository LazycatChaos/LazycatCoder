import pytest
from datetime import datetime, timezone
from event import deserialize_event, Event


def test_full_payload():
    """Complete payload with all fields should deserialize correctly."""
    data = {
        "event_type": "user.login",
        "message": "User logged in",
        "timestamp": "2024-01-15T10:30:00",
        "source": "auth-service",
        "severity": "INFO",
    }
    event = deserialize_event(data)
    assert event.event_type == "user.login"
    assert event.message == "User logged in"
    assert event.source == "auth-service"
    assert event.severity == "INFO"


def test_iso_datetime():
    """ISO 8601 timestamp should parse correctly."""
    data = {
        "event_type": "test",
        "message": "test",
        "timestamp": "2024-01-15T10:30:00",
    }
    event = deserialize_event(data)
    assert event.timestamp.year == 2024
    assert event.timestamp.month == 1
    assert event.timestamp.day == 15
    assert event.timestamp.hour == 10
    assert event.timestamp.minute == 30


def test_missing_event_type_raises_error():
    """Missing event_type should raise ValueError."""
    data = {
        "message": "test",
        "timestamp": "2024-01-15T10:30:00",
    }
    with pytest.raises(ValueError, match="event_type"):
        deserialize_event(data)
