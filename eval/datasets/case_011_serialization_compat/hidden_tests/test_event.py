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


def test_old_payload_missing_new_field():
    """Old payload without 'source' should use default."""
    data = {
        "event_type": "user.login",
        "message": "User logged in",
        "timestamp": "2024-01-15T10:30:00",
        "severity": "WARNING",
    }
    event = deserialize_event(data)
    assert event.source == "unknown"
    assert event.severity == "WARNING"


def test_old_payload_missing_severity():
    """Old payload without 'severity' should default to INFO."""
    data = {
        "event_type": "user.login",
        "message": "User logged in",
        "timestamp": "2024-01-15T10:30:00",
    }
    event = deserialize_event(data)
    assert event.severity == "INFO"
    assert event.source == "unknown"


def test_new_payload_with_unknown_fields():
    """Payload with extra unknown fields should not crash."""
    data = {
        "event_type": "user.login",
        "message": "User logged in",
        "timestamp": "2024-01-15T10:30:00",
        "source": "auth-service",
        "severity": "INFO",
        "extra_field": "should be ignored",
        "another_extra": 123,
    }
    event = deserialize_event(data)
    assert event.event_type == "user.login"
    assert not hasattr(event, "extra_field")


def test_unknown_enum_value():
    """Unknown severity should fall back to INFO."""
    data = {
        "event_type": "user.login",
        "message": "User logged in",
        "timestamp": "2024-01-15T10:30:00",
        "severity": "SUPER_CRITICAL",
    }
    event = deserialize_event(data)
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


def test_unix_timestamp():
    """Unix timestamp (int) should parse correctly."""
    data = {
        "event_type": "test",
        "message": "test",
        "timestamp": 1705312200,  # 2024-01-15 10:30:00 UTC
    }
    event = deserialize_event(data)
    assert event.timestamp.year == 2024
    assert event.timestamp.month == 1
    assert event.timestamp.day == 15


def test_unix_timestamp_float():
    """Unix timestamp (float) should parse correctly."""
    data = {
        "event_type": "test",
        "message": "test",
        "timestamp": 1705312200.123,
    }
    event = deserialize_event(data)
    assert event.timestamp.year == 2024


def test_invalid_datetime_raises_clean_error():
    """Invalid timestamp should raise ValueError with clear message."""
    data = {
        "event_type": "test",
        "message": "test",
        "timestamp": "not-a-date",
    }
    with pytest.raises(ValueError, match="Invalid timestamp"):
        deserialize_event(data)


def test_missing_event_type_raises_error():
    """Missing event_type should raise ValueError."""
    data = {
        "message": "test",
        "timestamp": "2024-01-15T10:30:00",
    }
    with pytest.raises(ValueError, match="event_type"):
        deserialize_event(data)


def test_roundtrip_stability():
    """Serializing and deserializing should be stable."""
    original = Event(
        event_type="test.event",
        message="Test message",
        timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        source="test-service",
        severity="WARNING",
    )
    # Simulate serialization
    data = {
        "event_type": original.event_type,
        "message": original.message,
        "timestamp": original.timestamp.isoformat(),
        "source": original.source,
        "severity": original.severity,
    }
    # Deserialize
    restored = deserialize_event(data)
    assert restored.event_type == original.event_type
    assert restored.message == original.message
    assert restored.source == original.source
    assert restored.severity == original.severity


def test_empty_string_severity():
    """Empty string severity should fall back to INFO."""
    data = {
        "event_type": "test",
        "message": "test",
        "timestamp": "2024-01-15T10:30:00",
        "severity": "",
    }
    event = deserialize_event(data)
    assert event.severity == "INFO"
