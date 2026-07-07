"""Enhanced file reading with line numbers and read tracking."""

from pathlib import Path
from typing import Optional
from .base import Tool, ValidationResult
from .edit import record_file_read


class ReadFileTool(Tool):
    """Read a file's contents with line numbers."""

    name = "read_file"
    description = (
        "Read a file's contents with line numbers. "
        "Always read a file before editing it. "
        "Use offset and limit parameters to read specific portions of large files."
    )

    search_hint = "read file contents"

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file",
            },
            "offset": {
                "type": "integer",
                "description": "Start line (1-based). Default 1.",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read. Default 2000.",
            },
        },
        "required": ["file_path"],
    }

    DEFAULT_OFFSET = 1
    DEFAULT_LIMIT = 2000
    MAX_LIMIT = 5000  # Prevent reading huge files

    def validate_input(self, file_path: str, **kwargs) -> ValidationResult:
        """Validate input parameters."""
        p = Path(file_path).expanduser().resolve()
        
        if not p.exists():
            return ValidationResult(
                valid=False,
                message=f"Error: File does not exist: {file_path}",
                error_code=1
            )
        
        if not p.is_file():
            return ValidationResult(
                valid=False,
                message=f"Error: Path is not a file: {file_path}",
                error_code=2
            )
        
        # Check file size (warn for very large files)
        try:
            file_size = p.stat().st_size
            if file_size > 10 * 1024 * 1024:  # 10MB
                return ValidationResult(
                    valid=False,
                    message=f"⚠️ File is very large ({file_size / 1024 / 1024:.1f}MB). Consider using offset/limit to read portions.",
                    error_code=3,
                    meta={"suggestion": "use_offset_limit"}
                )
        except Exception as e:
            return ValidationResult(
                valid=False,
                message=f"Error checking file size: {e}",
                error_code=4
            )
        
        return ValidationResult(valid=True)

    def execute(
        self,
        file_path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> str:
        """Read file contents with line numbers."""
        # Validate input
        validation = self.validate_input(file_path)
        if not validation.valid:
            return validation.message

        p = Path(file_path).expanduser().resolve()
        
        # Apply defaults
        offset = offset if offset is not None else self.DEFAULT_OFFSET
        limit = limit if limit is not None else self.DEFAULT_LIMIT
        
        # Enforce maximum limit
        if limit > self.MAX_LIMIT:
            limit = self.MAX_LIMIT
        
        try:
            # Read file
            content = p.read_text(encoding="utf-8")
            lines = content.splitlines()
            
            # Record that we read this file (for edit validation)
            record_file_read(str(p))
            
            # Apply offset and limit
            start_idx = max(0, offset - 1)  # Convert to 0-based
            end_idx = start_idx + limit
            selected_lines = lines[start_idx:end_idx]
            
            # Check if we're showing partial content
            total_lines = len(lines)
            was_truncated = end_idx < total_lines
            
            # Format with line numbers
            result_lines = []
            for i, line in enumerate(selected_lines, start=start_idx + 1):
                result_lines.append(f"{i}\t{line}")
            
            result = "\n".join(result_lines)
            
            # Add summary
            if was_truncated:
                result += f"\n\n[Showing lines {offset}-{end_idx} of {total_lines}. Use offset={end_idx + 1} to read more.]"
            elif total_lines > limit:
                result += f"\n\n[Showing all {total_lines} lines]"
            else:
                result += f"\n\n[End of file - {total_lines} lines total]"
            
            return result or "(empty file)"
        
        except UnicodeDecodeError as e:
            return f"Error: Unable to decode file as UTF-8. The file may be binary or use a different encoding.\nDetails: {e}"
        except Exception as e:
            return f"Error: {e}"

    def get_activity_description(self, kwargs: dict) -> str:
        """Get a short description of what the tool is doing."""
        file_path = kwargs.get("file_path", "")
        offset = kwargs.get("offset", self.DEFAULT_OFFSET)
        limit = kwargs.get("limit", self.DEFAULT_LIMIT)
        return f"Reading {Path(file_path).name} (lines {offset}-{offset + limit})"
