# Event Deserialization: Fix Backward Compatibility

## Background

We have an event system that serializes/deserializes events to JSON. A recent
update added new fields (`source`, `severity`) to the `Event` class. Now,
deserializing old event payloads (from before the update) crashes because the
new fields are missing.

## Task

Fix the `deserialize_event` function to handle old payloads gracefully.

## Requirements

1. **Missing new fields**: When `source` or `severity` are missing from the
   payload, use default values (`source="unknown"`, `severity="INFO"`).
2. **Unknown fields**: Extra fields in the payload should be ignored (not cause
   a crash).
3. **Unknown enum values**: If `severity` has an unrecognized value (including
   empty string), fall back to `"INFO"`.
4. **Time format support**: The `timestamp` field can be either:
   - ISO 8601 string: `"2024-01-15T10:30:00"`
   - Unix timestamp (int/float): `1705312200`
5. **Clean errors**: If the payload is fundamentally invalid (e.g. missing
   required `event_type`), raise a `ValueError` with a clear message.
   If the `timestamp` field cannot be parsed, raise a `ValueError` with a
   message containing `"Invalid timestamp"`.
6. **Backward compatibility**: Do not change the function signature or the
   `Event` class attributes.

## What NOT to change

- Do not change `deserialize_event(data: dict) -> Event` signature.
- Do not change the `Event` class fields.
