"""File deletion tool."""

import os
from pathlib import Path
from .base import Tool, ValidationResult


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

    def execute(self, file_path: str) -> str:
        validation = self.validate_input(file_path)
        if not validation.valid:
            return validation.message

        # Resolve to absolute path
        if not os.path.isabs(file_path):
            base = self.workdir if self.workdir else os.getcwd()
            file_path = os.path.join(base, file_path)

        file_path = os.path.normpath(file_path)

        # Safety: ensure the resolved path is within the working directory
        workdir = os.path.realpath(self.workdir) if self.workdir else os.path.realpath(os.getcwd())
        resolved = os.path.realpath(file_path)

        if not (resolved == workdir or resolved.startswith(workdir + os.sep)):
            return (
                f"Error: Path '{file_path}' is outside the working directory. "
                f"Deletion is restricted to '{self.workdir or os.getcwd()}' for safety."
            )

        if not os.path.exists(resolved):
            return f"Error: File not found: {file_path}"

        if os.path.isdir(resolved):
            return (
                f"Error: Cannot delete directory '{file_path}'. "
                f"delete_file only supports regular files. "
                f"Directory deletion is intentionally disabled for safety."
            )

        try:
            os.remove(resolved)
            return f"Deleted: {file_path}"
        except PermissionError:
            return f"Error: Permission denied: {file_path}"
        except Exception as e:
            return f"Error: Failed to delete file: {e}"
