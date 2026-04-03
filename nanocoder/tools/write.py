"""Enhanced file creation / overwrite with safety checks."""

from pathlib import Path
from typing import Optional
from .base import Tool, ValidationResult
from .edit import record_file_read


class WriteFileTool(Tool):
    """Create a new file or completely overwrite an existing one."""

    name = "write_file"
    description = (
        "Create a new file or completely overwrite an existing one. "
        "For small edits to existing files, prefer edit_file instead. "
        "Use this for: creating new files, complete rewrites, or when edit_file fails."
    )

    search_hint = "create or overwrite files"

    @property
    def is_read_only(self) -> bool:
        return False  # writes to disk

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path for the file",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write",
            },
        },
        "required": ["file_path", "content"],
    }

    def validate_input(self, file_path: str, content: str, **kwargs) -> ValidationResult:
        """Validate input parameters."""
        p = Path(file_path).expanduser().resolve()
        
        # Check parent directory exists or can be created
        if p.exists():
            if not p.is_file():
                return ValidationResult(
                    valid=False,
                    message=f"Error: Path exists but is not a file: {file_path}",
                    error_code=1
                )
        else:
            # Check if parent directory exists
            if not p.parent.exists():
                # Try to create parent directories
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    return ValidationResult(
                        valid=False,
                        message=f"Error: Cannot create parent directory: {e}",
                        error_code=2
                    )
        
        # Warn about overwriting large files
        if p.exists():
            try:
                file_size = p.stat().st_size
                if file_size > 100 * 1024:  # 100KB
                    return ValidationResult(
                        valid=False,
                        message=f"⚠️ File exists and is large ({file_size / 1024:.1f}KB). Consider using edit_file for small changes.",
                        error_code=3,
                        meta={"suggestion": "use_edit_file"}
                    )
            except Exception:
                pass
        
        return ValidationResult(valid=True)

    def execute(self, file_path: str, content: str) -> str:
        """Write content to file."""
        try:
            p = Path(file_path).expanduser().resolve()
            
            # Validate input
            validation = self.validate_input(file_path=file_path, content=content)
            if not validation.valid:
                return validation.message
            
            # Create parent directories if needed
            p.parent.mkdir(parents=True, exist_ok=True)
            
            # Write content
            p.write_text(content, encoding="utf-8")
            
            # Record as "read" so it can be edited immediately
            record_file_read(str(p))
            
            # Generate summary
            n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            file_size = len(content.encode('utf-8'))
            
            result = f"Wrote {n_lines} line{'s' if n_lines != 1 else ''} to {file_path}"
            if file_size > 1024:
                result += f" ({file_size / 1024:.1f}KB)"
            else:
                result += f" ({file_size} bytes)"
            
            return result
        
        except Exception as e:
            return f"Error: {e}"

    def get_activity_description(self, kwargs: dict) -> str:
        """Get a short description of what the tool is doing."""
        file_path = kwargs.get("file_path", "")
        return f"Writing {Path(file_path).name}"
