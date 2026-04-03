"""Tool registry with enhanced features."""

from typing import List, Optional, Dict
from .base import Tool
from .bash import BashTool
from .read import ReadFileTool
from .write import WriteFileTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .agent import AgentTool
from .todo import TodoWriteTool
from .web_search import WebSearchTool
from .fetch import FetchTool


class ToolRegistry:
    """Central registry for all tools."""
    
    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._register_default_tools()
    
    def _register_default_tools(self):
        """Register all default tools."""
        default_tools = [
            BashTool(),
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            GlobTool(),
            GrepTool(),
            AgentTool(),
            TodoWriteTool(),
            WebSearchTool(),  # Requires TAVILY_API_KEY
            FetchTool(),
        ]
        for tool in default_tools:
            self.register(tool)
    
    def register(self, tool: Tool):
        """Register a tool."""
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def list_tools(self) -> List[Tool]:
        """List all registered tools."""
        return list(self._tools.values())
    
    def get_all_schemas(self) -> List[dict]:
        """Get OpenAI schemas for all tools."""
        return [tool.schema() for tool in self._tools.values()]
    
    def search_tools(self, query: str) -> List[Tool]:
        """Search tools by name, description, or hint."""
        query_lower = query.lower()
        results = []
        for tool in self._tools.values():
            # Search in name, description, and hint
            searchable = f"{tool.name} {tool.description} {getattr(tool, 'search_hint', '')}".lower()
            if query_lower in searchable:
                results.append(tool)
        return results


# Global registry instance
registry = ToolRegistry()

# Backward compatibility: keep ALL_TOOLS list
ALL_TOOLS = registry.list_tools()


def get_tool(name: str) -> Optional[Tool]:
    """Look up a tool by name."""
    return registry.get(name)


def get_all_tools() -> List[Tool]:
    """Get all registered tools."""
    return registry.list_tools()


def get_tool_schemas() -> List[dict]:
    """Get OpenAI schemas for all tools."""
    return registry.get_all_schemas()
