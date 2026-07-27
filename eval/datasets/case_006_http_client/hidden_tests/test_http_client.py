import pytest
import json
import time
from unittest.mock import patch, MagicMock
from urllib.error import URLError, HTTPError
from http.client import HTTPMessage
from api_client import APIClient


def _make_mock_response(status=200, body=b'{}', headers=None):
    """Create a properly configured mock response for urlopen context manager."""
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = False
    mock_response.status = status
    mock_response.read.return_value = body
    if headers is not None:
        mock_response.headers = headers
    else:
        mock_response.headers = HTTPMessage()
    return mock_response


def _make_http_error(code=500, body=b'{}', url="https://example.com"):
    """Create a real HTTPError exception that can be raised."""
    import io
    fp = io.BytesIO(body)
    hdrs = HTTPMessage()
    return HTTPError(url, code, "Error", hdrs, fp)


class TestBackwardCompatibility:
    """Ensure existing API usage still works after adding timeout/retry."""

    def test_basic_get(self):
        """Basic GET request should still work."""
        client = APIClient("https://api.example.com")
        mock_response = _make_mock_response(200, b'{"ok": true}')

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = client.request("GET", "/users")

        assert result["status_code"] == 200
        assert result["body"] == {"ok": True}

    def test_post_with_data(self):
        """POST with data should still work."""
        client = APIClient("https://api.example.com")
        mock_response = _make_mock_response(201, b'{"id": 1}')

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = client.request("POST", "/users", data={"name": "Alice"})

        assert result["status_code"] == 201
        # Verify the request was sent (no retry for POST)
        assert mock_urlopen.call_count == 1

    def test_http_error_returns_dict(self):
        """HTTP errors should return dict with status code."""
        client = APIClient("https://api.example.com")
        http_error = _make_http_error(404, b'{"error": "not found"}', "https://api.example.com/missing")

        with patch("urllib.request.urlopen", side_effect=http_error):
            result = client.request("GET", "/missing")

        assert result["status_code"] == 404


class TestTimeout:
    """Test that timeout is applied to all requests."""

    def test_default_timeout_is_30(self):
        """Default timeout should be 30 seconds."""
        client = APIClient("https://api.example.com")
        mock_response = _make_mock_response(200, b'{}')

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            client.request("GET", "/test")

        # urlopen should be called with timeout=30
        call_kwargs = mock_urlopen.call_args
        timeout_val = call_kwargs.kwargs.get("timeout")
        if timeout_val is None and len(call_kwargs.args) > 1:
            timeout_val = call_kwargs.args[1]
        assert timeout_val == 30

    def test_timeout_passed_on_every_retry(self):
        """Timeout should be applied on every retry attempt."""
        client = APIClient("https://api.example.com")
        mock_response = _make_mock_response(200, b'{}')

        timeout_error = URLError("timed out")
        with patch("urllib.request.urlopen", side_effect=[
            timeout_error,
            mock_response,
        ]) as mock_urlopen:
            client.request("GET", "/test")

        # Both attempts should have timeout=30
        for call in mock_urlopen.call_args_list:
            timeout_val = call.kwargs.get("timeout")
            if timeout_val is None and len(call.args) > 1:
                timeout_val = call.args[1]
            assert timeout_val == 30


class TestRetryLogic:
    """Test retry behavior for GET/HEAD requests."""

    def test_get_retries_on_timeout(self):
        """GET should retry up to 3 times on timeout."""
        client = APIClient("https://api.example.com")
        mock_response = _make_mock_response(200, b'{"ok": true}')

        timeout_error = URLError("timed out")
        with patch("urllib.request.urlopen", side_effect=[
            timeout_error,
            timeout_error,
            mock_response,
        ]) as mock_urlopen:
            result = client.request("GET", "/test")

        assert result["status_code"] == 200
        assert mock_urlopen.call_count == 3

    def test_get_retries_on_connection_error(self):
        """GET should retry on connection errors."""
        client = APIClient("https://api.example.com")
        mock_response = _make_mock_response(200, b'{}')

        conn_error = URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=[
            conn_error,
            mock_response,
        ]) as mock_urlopen:
            result = client.request("GET", "/test")

        assert result["status_code"] == 200
        assert mock_urlopen.call_count == 2

    def test_post_does_not_retry(self):
        """POST should NOT retry on failure."""
        client = APIClient("https://api.example.com")
        timeout_error = URLError("timed out")

        with patch("urllib.request.urlopen", side_effect=timeout_error) as mock_urlopen:
            result = client.request("POST", "/submit", data={"key": "value"})

        # POST should only be attempted once (no retry)
        assert mock_urlopen.call_count == 1

    def test_delete_does_not_retry(self):
        """DELETE should NOT retry on failure."""
        client = APIClient("https://api.example.com")
        timeout_error = URLError("timed out")

        with patch("urllib.request.urlopen", side_effect=timeout_error) as mock_urlopen:
            result = client.request("DELETE", "/resource/1")

        assert mock_urlopen.call_count == 1

    def test_head_retries(self):
        """HEAD should retry like GET (idempotent)."""
        client = APIClient("https://api.example.com")
        mock_response = _make_mock_response(200, b'')

        timeout_error = URLError("timed out")
        with patch("urllib.request.urlopen", side_effect=[
            timeout_error,
            mock_response,
        ]) as mock_urlopen:
            result = client.request("HEAD", "/test")

        assert result["status_code"] == 200
        assert mock_urlopen.call_count == 2

    def test_retry_exhausted_returns_error(self):
        """After 3 failed attempts, should return error info."""
        client = APIClient("https://api.example.com")
        timeout_error = URLError("timed out")

        with patch("urllib.request.urlopen", side_effect=[
            timeout_error,
            timeout_error,
            timeout_error,
        ]) as mock_urlopen:
            result = client.request("GET", "/test")

        assert mock_urlopen.call_count == 3
        # Should indicate failure: status_code is None (no response) or error field present
        sc = result.get("status_code")
        assert sc is None or sc >= 400 or result.get("error") is not None

    def test_http_error_does_not_retry(self):
        """HTTP errors (404, 500) should NOT trigger retry."""
        client = APIClient("https://api.example.com")
        http_error = _make_http_error(500, b'{"error": "server error"}', "https://api.example.com/broken")

        with patch("urllib.request.urlopen", side_effect=http_error) as mock_urlopen:
            result = client.request("GET", "/broken")

        # HTTPError is caught and returned, not retried
        assert mock_urlopen.call_count == 1
        assert result["status_code"] == 500

    def test_put_does_not_retry(self):
        """PUT should NOT retry (not idempotent in general)."""
        client = APIClient("https://api.example.com")
        timeout_error = URLError("timed out")

        with patch("urllib.request.urlopen", side_effect=timeout_error) as mock_urlopen:
            result = client.request("PUT", "/resource/1", data={"name": "updated"})

        assert mock_urlopen.call_count == 1
