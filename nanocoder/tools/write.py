"""Enhanced file creation / overwrite with safety checks."""

from pathlib import Path
from typing import Any, Optional
from .base import Tool, ValidationResult
from .edit import record_file_read


class WriteFileTool(Tool):
    """Create a new file or completely overwrite an existing one."""

    name = "write_file"

    # 优化 1：增强 Description，明确警告模型必须提供两个参数，并注意转义
    description = (
        "Create a new file or completely overwrite an existing one. "
        "For small edits to existing files, prefer edit_file instead. "
        "Use this for: creating new files, complete rewrites, or when edit_file fails. "
        "IMPORTANT: You MUST provide BOTH 'file_path' and 'content' parameters. "
        "Ensure the 'content' string is properly escaped, especially for newlines and quotes."
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
                "description": "Absolute or relative path for the file",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write. Must be a complete string.",
            },
        },
        "required": ["file_path", "content"],
    }

    # 优化 2：移除 validate_input 中的副作用（创建文件夹），并增加参数校验
    def validate_input(self, file_path: str = "", content: Optional[str] = None, **kwargs: Any) -> ValidationResult:
        """Validate input parameters without making changes to the filesystem."""
        if not file_path or content is None:
            return ValidationResult(
                valid=False,
                message="Error: Both 'file_path' and 'content' are required.",
                error_code=0
            )

        p = Path(file_path).expanduser().resolve()

        # Check if it's trying to overwrite a directory
        if p.exists() and not p.is_file():
            return ValidationResult(
                valid=False,
                message=f"Error: Path exists but is not a file (it might be a directory): {file_path}",
                error_code=1
            )

        # Warn about overwriting large files
        if p.exists():
            try:
                file_size = p.stat().st_size
                if file_size > 100 * 1024:  # 100KB
                    return ValidationResult(
                        valid=False,
                        message=f"⚠️ File exists and is large ({file_size / 1024:.1f}KB). Completely overwriting it might destroy data. Please use 'edit_file' for targeted changes.",
                        error_code=3,
                        meta={"suggestion": "use_edit_file"}
                    )
            except Exception:
                pass

        return ValidationResult(valid=True)

    # 优化 3：修改函数签名，添加默认值和 **kwargs 捕获。
    # 这样即使模型漏传参数，也不会触发 Python TypeError 崩溃，而是返回清晰的错误信息让模型重试
    def execute(self, file_path: str = "", content: Optional[str] = None, **kwargs: Any) -> str:
        """Write content to file."""
        # 兼容不同 Agent 框架传递参数的方式
        file_path = file_path or kwargs.get("file_path", "")
        if content is None:
            content = kwargs.get("content")

        # 优雅拦截缺失参数的情况
        if not file_path or content is None:
            return "Error: Execution failed. You MUST provide BOTH 'file_path' and 'content' in your tool call."

        try:
            # 验证输入（不再需要在这里捕获 TypeError）
            validation = self.validate_input(file_path=file_path, content=content)
            if not validation.valid:
                return validation.message

            p = Path(file_path).expanduser().resolve()

            # 优化 4：将副作用（创建父目录）移到真正执行的地方
            try:
                if not p.parent.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return f"Error: Cannot create parent directory: {e}"

            # Write content
            p.write_text(content, encoding="utf-8")

            # Record as "read" so it can be edited immediately via EditFileTool
            record_file_read(str(p))

            # Generate summary
            n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            file_size = len(content.encode('utf-8'))

            result = f"Successfully wrote {n_lines} line{'s' if n_lines != 1 else ''} to {file_path}"
            if file_size > 1024:
                result += f" ({file_size / 1024:.1f}KB)"
            else:
                result += f" ({file_size} bytes)"

            return result

        except Exception as e:
            return f"Error writing file: {type(e).__name__} - {str(e)}"

    def get_activity_description(self, kwargs: dict) -> str:
        """Get a short description of what the tool is doing."""
        file_path = kwargs.get("file_path", "unknown_file")
        return f"Writing to {Path(file_path).name}"