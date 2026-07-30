"""A simple HTTP API client with timeout and retry support."""

import json
import urllib.request
import urllib.error


class APIClient:
    """A basic HTTP client for making API requests."""

    DEFAULT_TIMEOUT = 30
    MAX_RETRIES = 3

    def __init__(self, base_url: str = ""):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, data: dict | None = None,
                headers: dict | None = None) -> dict:
        """Make an HTTP request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: URL path (appended to base_url)
            data: Optional dict to send as JSON body (for POST/PUT)
            headers: Optional dict of additional headers

        Returns:
            dict with keys: status_code, body, headers
        """
        url = f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path

        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)

        # Determine if this method is retryable (idempotent)
        is_retryable = method.upper() in ("GET", "HEAD")

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=self.DEFAULT_TIMEOUT) as response:
                    response_body = response.read().decode("utf-8")
                    return {
                        "status_code": response.status,
                        "body": json.loads(response_body) if response_body else None,
                        "headers": dict(response.headers),
                    }
            except urllib.error.HTTPError as e:
                # HTTP errors (4xx, 5xx) should NOT be retried
                error_body = e.read().decode("utf-8") if e.fp else ""
                return {
                    "status_code": e.code,
                    "body": json.loads(error_body) if error_body else None,
                    "headers": dict(e.headers),
                }
            except (urllib.error.URLError, OSError) as e:
                # Connection errors and timeouts - retry if retryable
                last_error = e
                if not is_retryable:
                    break  # Don't retry non-idempotent methods

        # All retries exhausted
        return {
            "status_code": 503,
            "body": None,
            "headers": {},
            "error": f"Request failed after {self.MAX_RETRIES} attempts: {last_error}",
        }
