# RBAC Field Masking: Role-Based User Serialization

## Background

We have a user serialization function that returns user data as a dictionary.
Currently, it returns all fields regardless of who is requesting the data.
This means any user can see sensitive fields like `id_card`, `phone`, and `email`
of other users.

## Task

Implement role-based field masking so that different roles see different levels
of detail.

## Requirements

1. **admin**: Can see all fields (no masking).
2. **member**: Cannot see `id_card` (removed from output). `phone` should be
   masked (e.g. `138****5678` — keep first 3 and last 4 digits).
   If phone is shorter than 7 characters, return it as-is.
3. **anonymous**: Cannot see `email`, `phone`, or `id_card` (all removed).
4. **Nested fields**: Support dot-notation for nested fields, e.g.
   `contact.email`, `contact.phone`, `contact.id_card` should be masked
   according to the same role rules.
5. **Unknown roles**: Any unrecognized role should be treated as the most
   restrictive (same as anonymous).
6. **Do not mutate input**: The original user dict must not be modified.
   Return a new dict.
7. **Lists**: If the input is a list of user dicts, apply masking to each.

## What NOT to change

- The function signature: `serialize_user(user, role: str) -> dict`.
- Fields that are not mentioned in the masking rules should always be visible.
