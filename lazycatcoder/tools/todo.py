"""Todo list management for tracking task progress.

Inspired by Claude Code's TodoWriteTool - helps track progress on complex tasks.
"""

from typing import List, Literal
from dataclasses import dataclass
from .base import Tool, ToolResult


@dataclass
class TodoItem:
    """A single todo item."""
    content: str  # Imperative form (what to do)
    active_form: str  # Present continuous form (what's being done)
    status: Literal["pending", "in_progress", "completed"] = "pending"


class TodoWriteTool(Tool):
    """Create and manage a structured task list for coding sessions."""

    name = "todo_write"
    tier = "extended"
    description = (
        "Use this tool to create and manage a structured task list for your current coding session. "
        "This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user. "
        "Use proactively for: (1) Complex multi-step tasks (3+ steps), (2) Non-trivial tasks requiring planning, "
        "(3) When user explicitly requests todo list, (4) After receiving new instructions. "
        "NOT for single trivial tasks that can be completed in one step.\n\n"
        "IMPORTANT: When context gets compressed, the todo list in recent messages is the ONLY reliable "
        "record of task progress. Always call this tool after completing a step to keep the latest state "
        "visible in context. Each call replaces the entire list — include ALL tasks (completed, in_progress, and pending)."
    )

    prompt_snippet = "Create and manage a structured task list."
    prompt_guidelines = [
        "Use proactively for complex multi-step tasks (3+ steps). NOT for single trivial tasks.",
        "When context gets compressed, the todo list in recent messages is the ONLY reliable record of task progress. Always call this tool after completing a step.",
        "Each call replaces the entire list — include ALL tasks (completed, in_progress, and pending).",
    ]

    search_hint = "manage the session task checklist"

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return False

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

    def __init__(self):
        super().__init__()
        self._todos: List[TodoItem] = []

    def execute(self, todos: List[dict]) -> ToolResult:
        """Update the todo list and return status."""
        try:
            validated_todos = []
            for todo in todos:
                if not isinstance(todo, dict):
                    return ToolResult(success=False, content=f"Error: Invalid todo format: {todo}", error_code=1)

                content = todo.get("content", "").strip()
                active_form = todo.get("active_form", "").strip()
                status = todo.get("status", "pending")

                if not content or not active_form:
                    return ToolResult(success=False, content="Error: Both 'content' and 'active_form' must be non-empty strings", error_code=2)
                if status not in ["pending", "in_progress", "completed"]:
                    return ToolResult(success=False, content=f"Error: Invalid status '{status}'.", error_code=4)

                validated_todos.append(TodoItem(content=content, active_form=active_form, status=status))

            total_tasks = len(validated_todos)
            in_progress_count = sum(1 for t in validated_todos if t.status == "in_progress")
            completed_count = sum(1 for t in validated_todos if t.status == "completed")
            pending_count = sum(1 for t in validated_todos if t.status == "pending")

            # State machine validation: strict but flexible
            if in_progress_count > 1:
                return ToolResult(success=False, content=f"Error: Exactly ONE task must be in_progress, but found {in_progress_count}.", error_code=6)

            if total_tasks > 0 and in_progress_count == 0:
                # Allowed ONLY if purely planning (all pending) or fully done (all completed)
                if not (completed_count == total_tasks or pending_count == total_tasks):
                    return ToolResult(
                        success=False,
                        content="Error: You have pending tasks but nothing is in_progress. Please mark the task you are currently working on as 'in_progress'.",
                        error_code=5
                    )

            # Heuristic: warn if completed without verification step
            warnings = []
            all_completed = (total_tasks > 0 and completed_count == total_tasks)
            verify_keywords = ["verif", "test", "run", "check", "valid"]
            has_verification = any(
                any(kw in t.content.lower() for kw in verify_keywords)
                for t in validated_todos
            )

            if all_completed and total_tasks >= 3 and not has_verification:
                warnings.append(
                    "NOTE: You just completed 3+ tasks without an explicit verification/testing step. "
                    "Consider adding a task to check your work before concluding."
                )

            # Commit state
            self._todos = validated_todos

            # Build response with ASCII UI (safe for all terminals)
            result_lines = ["Todos updated successfully.\n"]
            if warnings:
                result_lines.extend(warnings)
                result_lines.append("")

            if validated_todos:
                result_lines.append("Current task list:")
                for i, todo in enumerate(validated_todos, 1):
                    status_mark = {"pending": "[ ]", "in_progress": "[-]", "completed": "[x]"}[todo.status]
                    pointer = "->" if todo.status == "in_progress" else "  "
                    result_lines.append(f" {pointer} {i}. {status_mark} {todo.content}")
            else:
                result_lines.append("All tasks completed! Task list cleared.")

            return ToolResult(success=True, content="\n".join(result_lines))

        except Exception as e:
            return ToolResult(success=False, content=f"Error updating todos: {e}", error_code=99)

    def get_todos(self) -> List[TodoItem]:
        """Get current todo list."""
        return self._todos.copy()

    def clear_todos(self):
        """Clear all todos."""
        self._todos.clear()
