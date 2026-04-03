"""
WebSearchTool - Search the web using Tavily API

Features:
- Web search with Tavily API
- Result filtering and ranking
- Source attribution
- Configurable result limits
- Clean, formatted output
"""

import os
import time
from typing import Optional, List, Dict, Any
from .base import Tool


class WebSearchTool(Tool):
    """Search the web using Tavily API"""
    
    name = "web_search"
    description = """Search the web for current information using Tavily API.
Use this tool when you need to:
- Find current information not in your knowledge base (post-2024 events)
- Research topics, products, or services
- Get latest news and updates
- Verify facts or claims
- Look up documentation or technical information

Returns formatted search results with titles, URLs, content snippets, and relevance scores."""
    
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query. Be specific and include relevant keywords."
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5, max: 10)",
                "default": 5
            },
            "search_depth": {
                "type": "string",
                "description": "Search depth: 'basic' for faster results, 'advanced' for more comprehensive",
                "enum": ["basic", "advanced"],
                "default": "basic"
            },
            "include_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of domains to include in results (optional)"
            },
            "exclude_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of domains to exclude from results (optional)"
            }
        },
        "required": ["query"]
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize WebSearchTool
        
        Args:
            api_key: Tavily API key. If not provided, reads from TAVILY_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        self._client = None
        self._last_search_time: Optional[float] = None
        self._search_count = 0
    
    def _get_client(self):
        """Lazy load Tavily client"""
        if self._client is None:
            if not self.api_key:
                raise ValueError(
                    "Tavily API key not provided. Set TAVILY_API_KEY environment variable "
                    "or pass api_key to WebSearchTool constructor."
                )
            try:
                from tavily import TavilyClient
                self._client = TavilyClient(api_key=self.api_key)
            except ImportError:
                raise ImportError(
                    "tavily package not installed. Install with: pip install tavily"
                )
        return self._client
    
    def execute(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        **kwargs
    ) -> str:
        """
        Execute web search
        
        Args:
            query: Search query
            max_results: Maximum results (default: 5, max: 10)
            search_depth: 'basic' or 'advanced'
            include_domains: Domains to include
            exclude_domains: Domains to exclude
            
        Returns:
            Formatted search results
        """
        # Validate inputs
        if not query or not query.strip():
            return "Error: Search query cannot be empty"
        
        # Enforce limits
        max_results = min(max(1, max_results), 10)
        
        try:
            client = self._get_client()
            
            # Perform search
            start_time = time.time()
            search_params = {
                "search_depth": search_depth,
                "max_results": max_results,
            }
            
            if include_domains:
                search_params["include_domains"] = include_domains
            if exclude_domains:
                search_params["exclude_domains"] = exclude_domains
            
            response = client.search(query, **search_params)
            elapsed = time.time() - start_time
            
            # Update stats
            self._last_search_time = time.time()
            self._search_count += 1
            
            # Handle different response formats
            # Tavily API returns a dict with 'results' key containing list of search results
            if isinstance(response, dict):
                results = response.get("results", [])
                # Also check for 'organic' key which some APIs use
                if not results and "organic" in response:
                    results = response.get("organic", [])
            elif isinstance(response, list):
                results = response
            elif hasattr(response, "__dict__"):
                # If response is an object, try to get results attribute
                results = getattr(response, "results", []) or getattr(response, "organic", [])
            else:
                # If response is a string or other type, return error with details
                return (
                    f"Error: Unexpected response format from Tavily API.\n"
                    f"Type: {type(response).__name__}\n"
                    f"Value: {str(response)[:200]}"
                )
            
            # Format results
            return self._format_results(results, query, elapsed)
            
        except Exception as e:
            error_msg = str(e)
            if "API key" in error_msg or "unauthorized" in error_msg.lower():
                return (
                    f"Error: Tavily API authentication failed.\n"
                    f"Please check your TAVILY_API_KEY environment variable.\n"
                    f"Get a key at: https://tavily.com/"
                )
            elif "rate limit" in error_msg.lower():
                return (
                    f"Error: Rate limit exceeded.\n"
                    f"Please wait a moment before searching again."
                )
            else:
                return f"Error: Web search failed: {error_msg}"
    
    def _format_results(
        self, 
        results: List[Dict[str, Any]], 
        query: str,
        elapsed: float
    ) -> str:
        """Format search results for display"""
        if not results:
            return f"No results found for: {query}"
        
        lines = [
            f"🔍 Search Results for: \"{query}\"",
            f"⏱️  Search time: {elapsed:.2f}s | Results: {len(results)}",
            "",
            "=" * 70,
            ""
        ]
        
        for i, result in enumerate(results, 1):
            # Handle case where result might be a string or other type
            if isinstance(result, dict):
                title = result.get("title", "No title")
                url = result.get("url", "No URL")
                content = result.get("content", "No content")
                score = result.get("score")
            elif isinstance(result, str):
                # If result is a string, use it as content
                title = f"Result {i}"
                url = "N/A"
                content = result
                score = None
            else:
                # Try to convert to string
                title = f"Result {i}"
                url = "N/A"
                content = str(result)
                score = None
            
            # Format score as percentage if available
            score_str = f"{score:.1%}" if isinstance(score, (int, float)) else "N/A"
            
            # Truncate long content
            if len(content) > 300:
                content = content[:300] + "..."
            
            lines.append(f"{i}. {title}")
            lines.append(f"   🔗 {url}")
            lines.append(f"   📊 Relevance: {score_str}")
            lines.append(f"   📝 {content}")
            lines.append("")
        
        lines.append("=" * 70)
        lines.append("💡 Tip: Use fetch_url to get full content from any URL above")
        
        return "\n".join(lines)
    
    def get_activity_description(self, **kwargs) -> str:
        """Get human-readable description of current activity"""
        query = kwargs.get("query", "unknown")
        return f"Searching web for: {query[:50]}{'...' if len(query) > 50 else ''}"
    
    def get_stats(self) -> Dict[str, Any]:
        """Get tool statistics"""
        return {
            "search_count": self._search_count,
            "last_search_time": self._last_search_time,
            "api_key_configured": bool(self.api_key),
        }
