"""Enhanced file creation / overwrite with safety checks and chunked writing support."""

from pathlib import Path
from typing import Any, Optional
from .base import Tool, ValidationResult
from .edit import record_file_read


class WriteFileTool(Tool):
    """Create a new file or completely overwrite an existing one. Supports chunked writing for large files."""

    name = "write_file"

    # Max single chunk size: 8KB to avoid JSON truncation in tool arguments
    MAX_CHUNK_BYTES = 8 * 1024

    description = (
        "Create a new file or completely overwrite an existing one. "
        "For small edits to existing files, prefer edit_file instead. "
        "Use this for: creating new files, complete rewrites, or when edit_file fails. "
        "IMPORTANT: You MUST provide BOTH 'file_path' and 'content' parameters. "
        "Ensure the 'content' string is properly escaped, especially for newlines and quotes.\n\n"
        "CHUNKED WRITING for large files (>200 lines or >10KB):\n"
        "  Use 'chunk_index' (1-based) and 'total_chunks' to write large files in parts.\n"
        "  - chunk_index=1: Creates or overwrites the file with the first chunk.\n"
        "  - chunk_index>1: Appends this chunk to the existing file.\n"
        "  - total_chunks: Total number of chunks (helps track progress).\n"
        "  Example: To write a 500-line file, make 3 calls with chunk_index=1,2,3 and total_chunks=3.\n"
        "  Each chunk should be a self-contained portion of the file content."
    )

    search_hint = "create or overwrite files, supports chunked writing for large files"

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
                "description": "Full file content to write. Must be a complete string. If omitted, creates an empty file.",
            },
            "chunk_index": {
                "type": "integer",
                "description": "For chunked writing: the 1-based index of this chunk. Use 1 for the first chunk (creates/overwrites), >1 to append subsequent chunks.",
            },
            "total_chunks": {
                "type": "integer",
                "description": "For chunked writing: total number of chunks. Helps track write progress.",
            },
        },
        "required": ["file_path"],
    }

    # 优化 2：移除 validate_input 中的副作用（创建文件夹），并增加参数校验
    def validate_input(self, file_path: str = "", content: Optional[str] = None, chunk_index: Optional[int] = None, **kwargs: Any) -> ValidationResult:
        """Validate input parameters without making changes to the filesystem."""
        if not file_path:
            return ValidationResult(
                valid=False,
                message="Error: 'file_path' is required.",
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

        # For chunked writing (chunk_index > 1), skip overwrite warnings
        if chunk_index is not None and chunk_index > 1:
            # Still validate chunk size for chunked writes
            if content is not None and len(content.encode('utf-8')) > self.MAX_CHUNK_BYTES:
                return ValidationResult(
                    valid=False,
                    message=f"⚠️ Chunk content is too large ({len(content.encode('utf-8')) / 1024:.1f}KB, max {self.MAX_CHUNK_BYTES / 1024:.0f}KB). "
                            f"Please split into smaller chunks and call write_file with chunk_index={chunk_index + 1}, {chunk_index + 2}, etc.",
                    error_code=4,
                    meta={"suggestion": "split_chunk"}
                )
            return ValidationResult(valid=True)

        # Warn about overwriting large files (only for non-chunked writes)
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

        # Validate single-write content size (non-chunked)
        if content is not None and len(content.encode('utf-8')) > self.MAX_CHUNK_BYTES:
            return ValidationResult(
                valid=False,
                message=f"⚠️ Content is too large ({len(content.encode('utf-8')) / 1024:.1f}KB, max {self.MAX_CHUNK_BYTES / 1024:.0f}KB for a single call). "
                        f"Please use chunked writing: call write_file with chunk_index=1, total_chunks=N for the first part, "
                        f"then chunk_index=2, 3, ... for subsequent parts.",
                error_code=4,
                meta={"suggestion": "use_chunked_writing"}
            )

        return ValidationResult(valid=True)

    # 优化 3：修改函数签名，添加默认值和 **kwargs 捕获。
    # 这样即使模型漏传参数，也不会触发 Python TypeError 崩溃，而是返回清晰的错误信息让模型重试
    def execute(
        self,
        file_path: str = "",
        content: Optional[str] = None,
        chunk_index: Optional[int] = None,
        total_chunks: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """Write content to file. Supports chunked writing for large files."""
        # 兼容不同 Agent 框架传递参数的方式
        file_path = file_path or kwargs.get("file_path", "")
        if content is None:
            content = kwargs.get("content")
        if chunk_index is None:
            chunk_index = kwargs.get("chunk_index")
        if total_chunks is None:
            total_chunks = kwargs.get("total_chunks")

        # 优雅拦截缺失 file_path 的情况
        if not file_path:
            return "Error: Execution failed. You MUST provide 'file_path' in your tool call."

        try:
            p = Path(file_path).expanduser().resolve()

            # 验证输入
            validation = self.validate_input(file_path=file_path, content=content, chunk_index=chunk_index)
            if not validation.valid:
                return validation.message

            # 创建父目录
            try:
                if not p.parent.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return f"Error: Cannot create parent directory: {e}"

            # 判断是覆盖还是追加
            is_chunked = chunk_index is not None and chunk_index > 1
            if is_chunked:
                # 追加模式：chunk_index > 1
                existing = p.read_text(encoding="utf-8") if p.exists() else ""
                content_to_write = existing + (content if content else "")
                p.write_text(content_to_write, encoding="utf-8")
            else:
                # 覆盖模式：chunk_index=1 或未指定
                p.write_text(content if content is not None else "", encoding="utf-8")

            # Record as "read" so it can be edited immediately via EditFileTool
            record_file_read(str(p))

            # Generate summary
            if content is None or content == "":
                result = f"Successfully created empty file: {file_path}"
            else:
                n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                file_size = len(content.encode('utf-8'))

                if is_chunked:
                    progress = f" (chunk {chunk_index}/{total_chunks})" if total_chunks else f" (chunk {chunk_index})"
                    result = f"Appended chunk{progress}: {n_lines} line{'s' if n_lines != 1 else ''} ({file_size} bytes) to {file_path}"
                else:
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