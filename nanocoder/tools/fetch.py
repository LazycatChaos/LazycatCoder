"""
FetchTool - Fetch content from URLs

Features:
- Fetch web page content
- Automatic content extraction
- Support for multiple formats (HTML, JSON, text)
- Timeout and size limits
"""

import os
import time
import re
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
from .base import Tool


class FetchTool(Tool):
    """Fetch content from URLs"""
    
    name = "fetch_url"
    description = """Fetch and extract content from a URL.
Use this tool when you need to:
- Get full content of a web page from search results
- Read documentation or articles
- Fetch API responses (JSON)
- Download text files

The tool automatically extracts main content and removes navigation/ads."""
    
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch. Must start with http:// or https://"
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds (default: 10, max: 30)",
                "default": 10
            },
            "max_content_length": {
                "type": "integer",
                "description": "Maximum content length in bytes (default: 100KB, max: 1MB)",
                "default": 102400
            },
            "extract_type": {
                "type": "string",
                "description": "Content extraction type",
                "enum": ["auto", "html", "text", "json"],
                "default": "auto"
            },
            "follow_redirects": {
                "type": "boolean",
                "description": "Follow HTTP redirects (default: true)",
                "default": True
            }
        },
        "required": ["url"]
    }
    
    def __init__(self):
        """Initialize FetchTool"""
        self._last_fetch_time: Optional[float] = None
        self._fetch_count = 0
        self._total_bytes = 0
    
    def execute(
        self,
        url: str,
        timeout: int = 10,
        max_content_length: int = 102400,
        extract_type: str = "auto",
        follow_redirects: bool = True,
        **kwargs
    ) -> str:
        """
        Fetch content from URL
        
        Args:
            url: URL to fetch
            timeout: Request timeout (default: 10s)
            max_content_length: Max content size (default: 100KB)
            extract_type: 'auto', 'html', 'text', or 'json'
            follow_redirects: Follow redirects (default: True)
            
        Returns:
            Fetched and extracted content
        """
        # Validate URL
        if not url or not url.strip():
            return "Error: URL cannot be empty"
        
        url = url.strip()
        
        # Validate URL scheme
        if not url.startswith(("http://", "https://")):
            return (
                f"Error: Invalid URL scheme. URL must start with http:// or https://\n"
                f"Provided: {url[:50]}"
            )
        
        # Validate timeout
        timeout = min(max(1, timeout), 30)
        
        # Validate max content length
        max_content_length = min(max(1024, max_content_length), 1024 * 1024)
        
        try:
            start_time = time.time()
            
            # Fetch content
            content, content_type, status_code = self._fetch_url(
                url=url,
                timeout=timeout,
                max_length=max_content_length,
                follow_redirects=follow_redirects
            )
            
            elapsed = time.time() - start_time
            
            # Update stats
            self._last_fetch_time = time.time()
            self._fetch_count += 1
            self._total_bytes += len(content)
            
            # Extract content based on type
            if extract_type == "auto":
                if "json" in content_type:
                    extract_type = "json"
                elif "html" in content_type:
                    extract_type = "html"
                else:
                    extract_type = "text"
            
            # Process content
            if extract_type == "json":
                extracted = self._format_json(content)
            elif extract_type == "html":
                extracted = self._extract_html_content(content, url)
            else:
                extracted = self._extract_text(content)
            
            # Format response
            return self._format_response(
                url=url,
                content=extracted,
                content_type=content_type,
                status_code=status_code,
                elapsed=elapsed
            )
            
        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower():
                return (
                    f"Error: Request timed out after {timeout}s\n"
                    f"URL: {url}\n"
                    f"Try increasing timeout or check if the site is accessible."
                )
            elif "SSL" in error_msg or "certificate" in error_msg.lower():
                return (
                    f"Error: SSL certificate verification failed\n"
                    f"URL: {url}\n"
                    f"This may be due to an invalid or self-signed certificate."
                )
            else:
                return f"Error: Failed to fetch URL: {error_msg}"
    
    def _fetch_url(
        self,
        url: str,
        timeout: int,
        max_length: int,
        follow_redirects: bool
    ) -> tuple:
        """Fetch URL content"""
        try:
            import requests
        except ImportError:
            raise ImportError(
                "requests package not installed. Install with: pip install requests"
            )
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=follow_redirects,
            stream=True
        )
        
        # Check status code
        if response.status_code >= 400:
            raise Exception(f"HTTP {response.status_code}: {response.reason}")
        
        # Get content type
        content_type = response.headers.get("Content-Type", "text/plain")
        
        # Read content with size limit
        content = b""
        for chunk in response.iter_content(chunk_size=8192):
            content += chunk
            if len(content) > max_length:
                content = content[:max_length]
                break
        
        return content.decode("utf-8", errors="ignore"), content_type, response.status_code
    
    def _extract_html_content(self, html: str, url: str) -> str:
        """Extract main content from HTML"""
        # Try to use readability if available
        try:
            from readability import Document
            doc = Document(html)
            title = doc.title()
            content = doc.summary()
            
            # Strip HTML tags
            content = re.sub(r"<[^>]+>", "", content)
            content = re.sub(r"\s+", " ", content).strip()
            
            return f"Title: {title}\n\n{content}"
            
        except ImportError:
            # Fallback: simple HTML stripping
            content = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<[^>]+>", "", content)
            content = re.sub(r"\s+", " ", content).strip()
            
            return content[:5000]  # Limit length
    
    def _extract_text(self, content: str) -> str:
        """Extract plain text"""
        # Clean up whitespace
        content = re.sub(r"\n\s*\n", "\n\n", content)
        content = re.sub(r"[ \t]+", " ", content)
        return content.strip()[:10000]
    
    def _format_json(self, content: str) -> str:
        """Format JSON content"""
        try:
            import json
            data = json.loads(content)
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
            return formatted[:20000]
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}\n\nRaw content:\n{content[:1000]}"
    
    def _format_response(
        self,
        url: str,
        content: str,
        content_type: str,
        status_code: int,
        elapsed: float
    ) -> str:
        """Format fetch response"""
        domain = urlparse(url).netloc
        
        lines = [
            f"馃寪 Fetched: {url}",
            f"馃搳 Status: {status_code}",
            f"馃搫 Type: {content_type}",
            f"鈴憋笍 Time: {elapsed:.2f}s",
            f"馃搹 Size: {len(content):,} bytes",
            "",
            "=" * 60,
            "",
            content,
            "",
            "=" * 60
        ]
        
        return "\n".join(lines)
    
    def get_activity_description(self, **kwargs) -> str:
        """Get human-readable description of current activity"""
        url = kwargs.get("url", "unknown")
        domain = urlparse(url).netloc
        return f"Fetching content from: {domain}"
    
    def get_stats(self) -> Dict[str, Any]:
        """Get tool statistics"""
        return {
            "fetch_count": self._fetch_count,
            "total_bytes": self._total_bytes,
            "last_fetch_time": self._last_fetch_time,
        }
