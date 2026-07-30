import copy

import pytest
from user_serializer import serialize_user


SAMPLE_USER = {
    "id": 1,
    "name": "Alice",
    "email": "alice@example.com",
    "phone": "13812345678",
    "id_card": "110101199001011234",
    "role": "user",
    "contact": {
        "email": "alice.work@example.com",
        "phone": "13987654321",
        "address": "Beijing",
    },
}


def test_admin_sees_all():
    """Admin role should see all fields."""
    result = serialize_user(SAMPLE_USER, "admin")
    assert result["email"] == "alice@example.com"
    assert result["phone"] == "13812345678"
    assert result["id_card"] == "110101199001011234"
    assert result["contact"]["email"] == "alice.work@example.com"
    assert result["contact"]["phone"] == "13987654321"


def test_member_cannot_see_id_card():
    """Member role should not see id_card."""
    result = serialize_user(SAMPLE_USER, "member")
    assert "id_card" not in result
    assert "id_card" not in result.get("contact", {})


def test_member_sees_email():
    """Member role should still see email."""
    result = serialize_user(SAMPLE_USER, "member")
    assert result["email"] == "alice@example.com"


def test_non_sensitive_fields_visible():
    """Non-sensitive fields should always be visible."""
    result = serialize_user(SAMPLE_USER, "anonymous")
    assert result["id"] == 1
    assert result["name"] == "Alice"
    assert result["role"] == "user"


def test_missing_fields_no_error():
    """User without sensitive fields should not cause errors."""
    minimal_user = {"id": 1, "name": "Bob"}
    result = serialize_user(minimal_user, "member")
    assert result == {"id": 1, "name": "Bob"}
