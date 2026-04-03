"""Enhanced GlobTool with better file discovery.

Features:
- Result limit to prevent context bloat
- Directory validation
- Sorted by modification time (recent first)
- Relative path output
"""

import os
from pathlib import Path
from typing import List, Optional
from .base import Tool, ValidationResult


class GlobTool(Tool):
    """Find files matching a glob pattern."""

    name = "glob"
    description = (
        "Find files matching a glob pattern. Supports ** for recursive matching (e.g. '**/*.py'). "
        "Results are sorted by modification time (most recent first) and limited to 100 files by default. "
        "Use head_limit=0 for unlimited results."
    )

    search_hint = "find files by pattern"

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: cwd)",
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N files. Default: 100. Pass 0 for unlimited.",
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N files before applying head_limit. Default: 0.",
            },
        },
        "required": ["pattern"],
    }

    DEFAULT_HEAD_LIMIT = 100

    def validate_input(self, pattern: str, **kwargs) -> ValidationResult:
        """Validate input parameters."""
        if not pattern or not pattern.strip():
            return ValidationResult(
                valid=False,
                message="Error: Pattern cannot be empty",
                error_code=1
            )
        
        path = kwargs.get("path")
        if path:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ValidationResult(
                    valid=False,
                    message=f"Error: Path does not exist: {path}",
                    error_code=2
                )
            if not p.is_dir():
                return ValidationResult(
                    valid=False,
                    message=f"Error: Path is not a directory: {path}",
                    error_code=3
                )
        
        return ValidationResult(valid=True)

    def execute(
        self,
        pattern: str,
        path: Optional[str] = None,
        head_limit: Optional[int] = None,
        offset: int = 0,
    ) -> str:
        """Find files matching the glob pattern."""
        # Validate input
        validation = self.validate_input(pattern)
        if not validation.valid:
            return validation.message

        # Set search directory
        search_dir = Path(path).expanduser().resolve() if path else Path.cwd()

        try:
            # Use pathlib's glob for matching
            matches = list(search_dir.glob(pattern))
            
            # Filter to only files (not directories)
            files = [m for m in matches if m.is_file()]
            
            # Sort by modification time (most recent first)
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            
            # Apply pagination
            applied_limit = head_limit if head_limit is not None else self.DEFAULT_HEAD_LIMIT
            if applied_limit == 0:
                # Unlimited
                final_files = files[offset:]
                limit_info = ""
            else:
                final_files = files[offset:offset + applied_limit]
                was_truncated = len(files) - offset > applied_limit
                limit_info = f" (limit: {applied_limit}" + (f", offset: {offset}" if offset > 0 else "") + ")" if was_truncated else ""
            
            if not final_files:
                return f"No files found matching '{pattern}'{limit_info}"
            
            # Convert to relative paths for cleaner output
            result_lines = [f"Found {len(final_files)} file{'s' if len(final_files) != 1 else ''}{limit_info}"]
            for file_path in final_files:
                try:
                    rel_path = file_path.relative_to(Path.cwd())
                    result_lines.append(str(rel_path))
                except ValueError:
                    # File is not relative to cwd, use absolute path
                    result_lines.append(str(file_path))
            
            return "\n".join(result_lines)
        
        except Exception as e:
            return f"Error: {e}"

    def get_activity_description(self, kwargs: dict) -> str:
        """Get a short description of what the tool is doing."""
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path", "")
        if path:
            return f"Finding files matching '{pattern}' in {path}"
        return f"Finding files matching '{pattern}'"
