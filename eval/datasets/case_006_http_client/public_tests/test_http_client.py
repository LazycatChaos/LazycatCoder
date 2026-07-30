import pytest
import json
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
    """Ensure existing API usage still works."""

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
        assert mock_urlopen.call_count == 1

    def test_http_error_returns_dict(self):
        """HTTP errors should return dict with status code."""
        client = APIClient("https://api.example.com")
        http_error = _make_http_error(404, b'{"error": "not found"}', "https://api.example.com/missing")

        with patch("urllib.request.urlopen", side_effect=http_error):
            result = client.request("GET", "/missing")

        assert result["status_code"] == 404
