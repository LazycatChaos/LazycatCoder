"""File deletion tool."""

import os
from pathlib import Path
from .base import Tool, ToolResult, ValidationResult


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Delete a file from the filesystem. "
        "Use this to remove temporary files, clean up after refactoring, or delete files that are no longer needed. "
        "This operation cannot be undone. The file must exist and be a regular file (not a directory). "
        "Deletion is restricted to the working directory for safety."
    )
    search_hint = "delete remove file"

    # Working directory — set by Agent at startup
    workdir: str | None = None

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return False  # modifies filesystem

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to delete (relative to working directory or absolute)",
            },
        },
        "required": ["file_path"],
    }

    def validate_input(self, file_path: str, **kwargs) -> ValidationResult:
        if not file_path or not file_path.strip():
            return ValidationResult(valid=False, message="Error: file_path cannot be empty", error_code=1)
        return ValidationResult(valid=True)

    def execute(self, file_path: str) -> ToolResult:
        validation = self.validate_input(file_path)
        if not validation.valid:
            return ToolResult(success=False, content=validation.message, error_code=validation.error_code)

        # Resolve to absolute path
        if not os.path.isabs(file_path):
            base = self.workdir if self.workdir else os.getcwd()
            file_path = os.path.join(base, file_path)

        file_path = os.path.normpath(file_path)

        # Safety: ensure the resolved path is within the working directory
        workdir = os.path.realpath(self.workdir) if self.workdir else os.path.realpath(os.getcwd())
        resolved = os.path.realpath(file_path)

        if not (resolved == workdir or resolved.startswith(workdir + os.sep)):
            return ToolResult(
                success=False,
                content=(
                    f"Error: Path '{file_path}' is outside the working directory. "
                    f"Deletion is restricted to '{self.workdir or os.getcwd()}' for safety."
                ),
                error_code=10,
            )

        if not os.path.exists(resolved):
            return ToolResult(success=False, content=f"Error: File not found: {file_path}", error_code=11)

        if os.path.isdir(resolved):
            return ToolResult(
                success=False,
                content=(
                    f"Error: Cannot delete directory '{file_path}'. "
                    f"delete_file only supports regular files. "
                    f"Directory deletion is intentionally disabled for safety."
                ),
                error_code=12,
            )

        try:
            os.remove(resolved)
            return ToolResult(success=True, content=f"Deleted: {file_path}")
        except PermissionError:
            return ToolResult(success=False, content=f"Error: Permission denied: {file_path}", error_code=13)
        except Exception as e:
            return ToolResult(success=False, content=f"Error: Failed to delete file: {e}", error_code=99)
