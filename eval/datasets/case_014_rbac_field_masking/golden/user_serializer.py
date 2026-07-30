import copy


def _mask_phone(phone: str) -> str:
    """Mask phone number: keep first 3 and last 4 digits."""
    if not isinstance(phone, str) or len(phone) < 7:
        return phone
    return phone[:3] + "****" + phone[-4:]


def _deep_copy_and_mask(data: dict, role: str) -> dict:
    """Recursively copy and mask fields based on role."""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = _deep_copy_and_mask(value, role)
        elif isinstance(value, list):
            result[key] = [
                _deep_copy_and_mask(item, role) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def _apply_masking(user: dict, role: str) -> dict:
    """Apply role-based field masking to a user dict."""
    result = _deep_copy_and_mask(user, role)

    if role == "admin":
        return result

    if role == "member":
        # Remove id_card, mask phone
        result.pop("id_card", None)
        if "phone" in result:
            result["phone"] = _mask_phone(result["phone"])
        # Also handle nested contact fields
        if "contact" in result and isinstance(result["contact"], dict):
            result["contact"].pop("id_card", None)
            if "phone" in result["contact"]:
                result["contact"]["phone"] = _mask_phone(result["contact"]["phone"])
        return result

    # anonymous or unknown role: remove email, phone, id_card
    result.pop("email", None)
    result.pop("phone", None)
    result.pop("id_card", None)
    if "contact" in result and isinstance(result["contact"], dict):
        result["contact"].pop("email", None)
        result["contact"].pop("phone", None)
        result["contact"].pop("id_card", None)
    return result


def serialize_user(user, role: str):
    """Serialize a user (or list of users) with role-based field masking.
    
    Roles:
    - admin: all fields visible
    - member: id_card removed, phone masked
    - anonymous: email, phone, id_card removed
    - unknown: treated as anonymous (most restrictive)
    """
    if isinstance(user, list):
        return [_apply_masking(u, role) for u in user]
    return _apply_masking(user, role)
