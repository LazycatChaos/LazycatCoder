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


def test_member_phone_masked():
    """Member role should see masked phone."""
    result = serialize_user(SAMPLE_USER, "member")
    assert result["phone"] == "138****5678"
    assert result["contact"]["phone"] == "139****4321"


def test_member_sees_email():
    """Member role should still see email."""
    result = serialize_user(SAMPLE_USER, "member")
    assert result["email"] == "alice@example.com"


def test_anonymous_cannot_see_sensitive_fields():
    """Anonymous role should not see email, phone, or id_card."""
    result = serialize_user(SAMPLE_USER, "anonymous")
    assert "email" not in result
    assert "phone" not in result
    assert "id_card" not in result


def test_anonymous_nested_masked():
    """Anonymous role should mask nested contact fields too."""
    result = serialize_user(SAMPLE_USER, "anonymous")
    assert "contact" in result
    assert "email" not in result["contact"]
    assert "phone" not in result["contact"]
    assert result["contact"]["address"] == "Beijing"


def test_unknown_role_default_deny():
    """Unknown role should be treated as anonymous (most restrictive)."""
    result = serialize_user(SAMPLE_USER, "super_admin_v2")
    assert "email" not in result
    assert "phone" not in result
    assert "id_card" not in result


def test_original_user_not_mutated():
    """Original user dict must not be modified."""
    original = copy.deepcopy(SAMPLE_USER)
    serialize_user(SAMPLE_USER, "anonymous")
    assert SAMPLE_USER == original


def test_list_of_users():
    """List of users should all be masked."""
    users = [
        {"name": "Alice", "email": "a@test.com", "phone": "13811112222", "id_card": "111"},
        {"name": "Bob", "email": "b@test.com", "phone": "13833334444", "id_card": "222"},
    ]
    result = serialize_user(users, "member")
    assert len(result) == 2
    assert result[0]["email"] == "a@test.com"
    assert "id_card" not in result[0]
    assert result[0]["phone"] == "138****2222"
    assert result[1]["email"] == "b@test.com"
    assert "id_card" not in result[1]


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


def test_short_phone_not_over_masked():
    """Short phone numbers should not be over-masked."""
    user = {"name": "Test", "phone": "12345"}
    result = serialize_user(user, "member")
    # Short phone: should return as-is (less than 7 chars)
    assert result["phone"] == "12345"
