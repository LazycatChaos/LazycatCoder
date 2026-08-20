"""Activate extended tools so they become available in the next turn.

This tool is always in the core tier — the LLM can call it any time to
pull in extended tools (web_search, fetch_url, agent, todo_write) that
are listed in the system prompt under "Inactive Tools".
"""

from .base import Tool, ToolResult


class ActivateTool(Tool):
    """Activate inactive tools by name."""

    name = "activate_tool"
    tier = "core"
    description = (
        "Activate inactive tools so they become available in the next turn. "
        "Available inactive tools are listed in the system prompt under 'Inactive Tools'."
    )

    prompt_snippet = "Activate extended tools (web_search, fetch_url, etc.)"
    prompt_guidelines = []

    parameters = {
        "type": "object",
        "properties": {
            "tool_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool names to activate, e.g. [\"web_search\", \"fetch_url\"]",
            }
        },
        "required": ["tool_names"],
    }

    # Set by Agent.__init__ after construction — the Agent instance
    # that owns this tool instance.  Named _owner_agent (not _parent_agent)
    # to distinguish from AgentTool's parent-child relationship.
    _owner_agent = None

    def execute(self, *, tool_names: list[str], **_) -> ToolResult:
        if self._owner_agent is None:
            return ToolResult.fail("activate_tool not initialized (no owner agent)")

        activated, skipped = self._owner_agent.activate_tools(tool_names)

        lines = []
        for name in activated:
            tool = self._owner_agent.registry.get(name)
            snippet = tool.prompt_snippet or tool.description[:80]
            lines.append(f"✓ {name}: {snippet}")
        for name in skipped:
            lines.append(f"- {name} (already active or unknown)")

        if activated:
            lines.append("\nThese tools will be available starting from the next turn.")

        return ToolResult.ok("\n".join(lines))
