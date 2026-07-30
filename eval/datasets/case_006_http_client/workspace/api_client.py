"""A simple HTTP API client."""

import json
import urllib.request
import urllib.error


class APIClient:
    """A basic HTTP client for making API requests."""

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

        try:
            with urllib.request.urlopen(req) as response:
                response_body = response.read().decode("utf-8")
                return {
                    "status_code": response.status,
                    "body": json.loads(response_body) if response_body else None,
                    "headers": dict(response.headers),
                }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            return {
                "status_code": e.code,
                "body": json.loads(error_body) if error_body else None,
                "headers": dict(e.headers),
            }
