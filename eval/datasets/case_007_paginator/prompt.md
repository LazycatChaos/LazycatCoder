# Paginator: Fix Edge Cases

## Background

We have a `Paginator` class used across the application to handle paginated API
responses. Users are reporting crashes when dealing with empty results or unusual
page numbers.

## Task

Fix the `Paginator` class so it handles all edge cases gracefully without raising
exceptions.

## Requirements

1. **Page numbering**: Pages are 1-indexed (page 1 is the first page).
2. **page_size validation**: Must be > 0. If invalid (0 or negative), use default of 20.
3. **Page input validation**: `page` must be >= 1. If page is 0 or negative,
   treat it as page 1 (do NOT raise an exception).
4. **Total pages calculation**: Use ceiling division, not floor division.
   - Example: 25 items with page_size=10 → total_pages=3 (not 2).
   - Note: The current workspace uses floor division (`//`), which is incorrect.
5. **Zero total**: When `total == 0`, `total_pages` should be 0.
6. **Out-of-range page**: When page is out of range, do NOT raise an exception.
   Return empty items but keep meta information correct.
7. **has_next / has_prev**: Must correctly reflect whether there are more pages.
8. **Backward compatibility**: Do not change the public API (method signatures,
   return types, meta dict keys).

## What NOT to change

- Do not change `__init__` parameters or `get_meta()` return format.
- Do not change the meta dict keys: `page`, `page_size`, `total`, `total_pages`,
  `has_next`, `has_prev`.
