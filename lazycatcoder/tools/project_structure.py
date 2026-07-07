"""Project structure tool.

Returns a tree view of the project directory, helping the LLM understand
the codebase layout without needing multiple ls/cd calls.
"""

import os
from pathlib import Path
from typing import Optional
from .base import Tool, ValidationResult


class ProjectStructureTool(Tool):
    """Get a tree view of the project directory structure."""

    name = "project_structure"
    description = (
        "Get a tree-like view of the project directory structure. "
        "This is the FASTEST way to understand the codebase layout — "
        "use it INSTEAD of running multiple 'ls' or 'cd' commands. "
        "Returns directories and files in a hierarchical tree format. "
        "You can specify a subdirectory and control the max depth."
    )

    search_hint = "explore project layout, directory tree"

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Root directory to start from (default: project root / cwd)",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum depth of the tree (default: 3). Increase for deeper exploration.",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden files/directories (starting with .). Default: false.",
            },
        },
        "required": [],
    }

    # Directories and files to skip by default
    _SKIP_DIRS = {
        ".git", "__pycache__", ".venv", "venv", "node_modules",
        ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
        ".tox", ".eggs", "*.egg-info", "dist", "build",
    }

    def validate_input(self, **kwargs) -> ValidationResult:
        path = kwargs.get("path")
        if path:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ValidationResult(
                    valid=False,
                    message=f"Error: Path does not exist: {path}",
                    error_code=1,
                )
            if not p.is_dir():
                return ValidationResult(
                    valid=False,
                    message=f"Error: Path is not a directory: {path}",
                    error_code=2,
                )
        return ValidationResult(valid=True)

    def execute(
        self,
        path: Optional[str] = None,
        max_depth: int = 3,
        include_hidden: bool = False,
    ) -> str:
        """Return a tree view of the directory structure."""
        validation = self.validate_input(path=path)
        if not validation.valid:
            return validation.message

        root = Path(path).expanduser().resolve() if path else Path.cwd()
        max_depth = max(1, min(max_depth, 10))  # clamp 1-10

        lines: list[str] = []
        self._build_tree(root, "", True, max_depth, include_hidden, lines)

        if not lines:
            return f"Directory '{root}' is empty"

        header = f"Project structure: {root} (max depth: {max_depth})\n"
        return header + "\n".join(lines)

    def _build_tree(
        self,
        directory: Path,
        prefix: str,
        is_last: bool,
        max_depth: int,
        include_hidden: bool,
        lines: list[str],
    ):
        """Recursively build the tree structure."""
        # Print current directory name
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{directory.name}/")

        if max_depth <= 0:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}{'    ' if is_last else '│   '}[Permission denied]")
            return

        # Filter entries
        filtered = []
        for entry in entries:
            name = entry.name
            if not include_hidden and name.startswith("."):
                continue
            if entry.is_dir() and name in self._SKIP_DIRS:
                continue
            filtered.append(entry)

        # Adjust prefix for children
        new_prefix = prefix + ("    " if is_last else "│   ")

        for i, entry in enumerate(filtered):
            is_last_entry = (i == len(filtered) - 1)
            if entry.is_dir():
                self._build_tree(entry, new_prefix, is_last_entry, max_depth - 1, include_hidden, lines)
            else:
                connector = "└── " if is_last_entry else "├── "
                lines.append(f"{new_prefix}{connector}{entry.name}")

    def get_activity_description(self, kwargs: dict) -> str:
        path = kwargs.get("path", "project root")
        depth = kwargs.get("max_depth", 3)
        return f"Exploring project structure at {path} (depth: {depth})"
