"""Todo list management for tracking task progress.

Inspired by Claude Code's TodoWriteTool - helps track progress on complex tasks.
"""

from typing import List, Literal, Optional
from dataclasses import dataclass, field
from .base import Tool, ValidationResult


@dataclass
class TodoItem:
    """A single todo item."""
    content: str  # Imperative form (what to do)
    active_form: str  # Present continuous form (what's being done)
    status: Literal["pending", "in_progress", "completed"] = "pending"


class TodoWriteTool(Tool):
    """Create and manage a structured task list for coding sessions."""

    name = "todo_write"
    description = (
        "Use this tool to create and manage a structured task list for your current coding session. "
        "This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user. "
        "Use proactively for: (1) Complex multi-step tasks (3+ steps), (2) Non-trivial tasks requiring planning, "
        "(3) When user explicitly requests todo list, (4) After receiving new instructions. "
        "NOT for single trivial tasks that can be completed in one step.\n\n"
        "IMPORTANT: When context gets compressed, the todo list in recent messages is the ONLY reliable "
        "record of task progress. Always call this tool after completing a step to keep the latest state "
        "visible in context. Each call replaces the entire list — include ALL todos with updated statuses."
    )

    search_hint = "manage the session task checklist"

    @property
    def is_read_only(self) -> bool:
        return False  # modifies todo state

    @property
    def is_concurrency_safe(self) -> bool:
        return False  # stateful operations

    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The task to be done (imperative form, e.g., 'Run tests')",
                        },
                        "active_form": {
                            "type": "string",
                            "description": "Present continuous form shown during execution (e.g., 'Running tests')",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Task status",
                        },
                    },
                    "required": ["content", "active_form", "status"],
                },
            },
        },
        "required": ["todos"],
    }

    # In-memory storage (would be persisted in real implementation)
    _todos: List[TodoItem] = field(default_factory=list)

    def execute(self, todos: List[dict]) -> str:
        """Update the todo list and return status."""
        try:
            # Validate and convert todos
            validated_todos = []
            for todo in todos:
                if not isinstance(todo, dict):
                    return f"Error: Invalid todo format: {todo}"
                
                content = todo.get("content", "").strip()
                active_form = todo.get("active_form", "").strip()
                status = todo.get("status", "pending")

                if not content:
                    return "Error: Each todo must have non-empty 'content'"
                if not active_form:
                    return "Error: Each todo must have non-empty 'active_form'"
                if status not in ["pending", "in_progress", "completed"]:
                    return f"Error: Invalid status '{status}'. Must be: pending, in_progress, or completed"

                validated_todos.append(TodoItem(
                    content=content,
                    active_form=active_form,
                    status=status
                ))

            # Validate business rules
            in_progress_count = sum(1 for t in validated_todos if t.status == "in_progress")
            if in_progress_count == 0 and len(validated_todos) > 0:
                return "Error: At least one task must be in_progress when there are tasks"
            if in_progress_count > 1:
                return f"Error: Exactly ONE task must be in_progress, but found {in_progress_count}"

            # Check for completion without verification (heuristic)
            all_completed = all(t.status == "completed" for t in validated_todos)
            has_verification = any("verif" in t.content.lower() for t in validated_todos)
            
            old_todos = self._todos.copy()
            self._todos = validated_todos

            # Build response
            result_lines = ["Todos updated successfully.\n"]
            
            if validated_todos:
                result_lines.append("Current task list:")
                for i, todo in enumerate(validated_todos, 1):
                    status_icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}[todo.status]
                    result_lines.append(f"  {i}. {status_icon} [{todo.status}] {todo.content}")
                
                if all_completed and len(validated_todos) >= 3 and not has_verification:
                    result_lines.append(
                        "\n⚠️ NOTE: You just completed 3+ tasks and none was a verification step. "
                        "Consider adding a verification task before finalizing."
                    )
            else:
                result_lines.append("All tasks completed! Task list cleared.")

            return "\n".join(result_lines)

        except Exception as e:
            return f"Error updating todos: {e}"

    def get_todos(self) -> List[TodoItem]:
        """Get current todo list."""
        return self._todos.copy()

    def clear_todos(self):
        """Clear all todos."""
        self._todos.clear()
