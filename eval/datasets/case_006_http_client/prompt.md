# HTTP Client: Add Timeout & Retry

## Background

We have a simple `APIClient` class that makes HTTP requests. Currently it has no
timeout protection and no retry logic, which causes the application to hang
indefinitely when the remote server is slow or unresponsive.

## Task

Add **timeout** and **retry** functionality to the `APIClient` class.

## Requirements

1. **Timeout**: All requests must have a default timeout of 30 seconds.
2. **Retry**: Automatically retry failed requests (up to 3 attempts total).
   - Retry **only** for `GET` and `HEAD` methods (they are idempotent).
   - Do NOT retry `POST`, `PUT`, `DELETE`, or any other method.
   - Retry only on connection errors and timeout errors, NOT on HTTP error
     status codes (e.g. 404, 500 should return immediately without retry).
3. **Retry exhausted behavior**: When all retry attempts fail:
   - Do NOT raise an exception (the caller should not need try/except).
   - Return a dict with the same structure as a successful response.
   - Since no valid HTTP response was received, `status_code` and `body`
     should be `None`.
   - Include an `error` field (str type) describing the failure reason.
4. **Backward Compatibility**: The existing `request(method, url, ...)` signature
   must remain unchanged. Existing code that calls `client.request("POST", url, data=...)`
   must continue to work without any modification.
5. **No New Dependencies**: Use only the standard library (`urllib.request`,
   `urllib.error`, `http.client`, etc.). Do not introduce `requests` or any
   third-party package.

## What NOT to change

- Do not change the method signature of `request()`.
- Do not change the return type or format.
- Do not remove any existing functionality.
