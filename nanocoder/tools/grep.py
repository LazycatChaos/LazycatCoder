"""Enhanced GrepTool with multiple output modes and advanced features.

Inspired by Claude Code's GrepTool (ripgrep wrapper):
- Multiple output modes: content, files_with_matches, count
- Context lines control (-A, -B, -C)
- Pagination support (head_limit, offset)
- File type filtering
- Automatic VCS directory exclusion
- Results sorted by modification time
"""
import shutil
import subprocess
import os
from pathlib import Path
from typing import List, Optional, Literal
from .base import Tool, ValidationResult


class GrepTool(Tool):
    """Search file contents with regex using native ripgrep."""

    name = "grep"
    description = (
        "Search file contents with regex (ripgrep). "
        "Supports multiple output modes: 'content', 'files_with_matches', 'count'. "
        "Automatically excludes VCS directories. "
    )
    search_hint = "search file contents with regex"

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory or file to search in (default: current working directory)"},
            "glob": {"type": "string", "description": "File glob pattern to filter files (e.g. '*.py')"},
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode: 'content' shows matching lines, 'files_with_matches' lists files, 'count' shows match counts"
            },
            "before_context": {"type": "integer", "description": "Number of lines to show before each match (-B)"},
            "after_context": {"type": "integer", "description": "Number of lines to show after each match (-A)"},
            "context": {"type": "integer", "description": "Number of lines to show before and after each match (-C)"},
            "line_numbers": {"type": "boolean", "description": "Show line numbers in output (default: true)"},
            "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (-i)"},
            "file_type": {"type": "string", "description": "File type to filter by (e.g. 'python', 'rust')"},
            "head_limit": {"type": "integer", "description": "Maximum number of results to return (default: 250, 0 for unlimited)"},
            "offset": {"type": "integer", "description": "Number of results to skip (for pagination)"},
            "multiline": {"type": "boolean", "description": "Enable multiline mode"},
        },
        "required": ["pattern"],
    }

    VCS_DIRS = ['.git', '.svn', '.hg', '.bzr', '.jj', '.sl']
    DEFAULT_HEAD_LIMIT = 250

    def validate_input(self, pattern: str, **kwargs) -> ValidationResult:
        if not pattern or not pattern.strip():
            return ValidationResult(valid=False, message="Error: Pattern cannot be empty", error_code=1)
        path = kwargs.get("path")
        if path:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return ValidationResult(valid=False, message=f"Error: Path does not exist: {path}", error_code=2)
        return ValidationResult(valid=True)

    def execute(
            self,
            pattern: str,
            path: Optional[str] = None,
            glob: Optional[str] = None,
            output_mode: Literal["content", "files_with_matches", "count"] = "files_with_matches",
            before_context: Optional[int] = None,
            after_context: Optional[int] = None,
            context: Optional[int] = None,
            line_numbers: bool = True,
            case_insensitive: bool = False,
            file_type: Optional[str] = None,
            head_limit: Optional[int] = None,
            offset: int = 0,
            multiline: bool = False,
    ) -> str:
        validation = self.validate_input(pattern, path=path)
        if not validation.valid:
            return validation.message

        # 核心改动：寻找原生的 rg.exe，不再做任何 WSL 桥接检测
        rg_executable = shutil.which('rg') or shutil.which('rg.exe')
        if not rg_executable:
            return "Error: ripgrep (rg.exe) not found on Windows. Please install it using 'winget install ripgrep' or 'scoop install ripgrep'."

        args = [rg_executable, "--hidden"]

        for vcs_dir in self.VCS_DIRS:
            args.extend(["--glob", f"!{vcs_dir}"])

        args.extend(["--max-columns", "500"])

        if multiline: args.extend(["-U", "--multiline-dotall"])
        if case_insensitive: args.append("-i")

        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        elif output_mode == "content" and line_numbers:
            args.append("-n")

        if output_mode == "content":
            if context is not None:
                args.extend(["-C", str(context)])
            else:
                if before_context is not None: args.extend(["-B", str(before_context)])
                if after_context is not None: args.extend(["-A", str(after_context)])

        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)

        if file_type: args.extend(["--type", file_type])
        if glob:
            for g in glob.replace(" ", ",").split(","):
                g = g.strip()
                if g: args.extend(["--glob", g])

        search_path = str(Path(path).expanduser().resolve()) if path else os.getcwd()

        try:
            # shell=False 提高安全性，直接调用原生 exe
            result = subprocess.run(
                args,
                cwd=search_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                shell=False
            )

            lines = result.stdout.strip().split("\n") if result.stdout.strip() else []

            applied_limit = head_limit if head_limit is not None else self.DEFAULT_HEAD_LIMIT
            if applied_limit == 0:
                final_lines = lines[offset:]
                limit_info = ""
            else:
                final_lines = lines[offset:offset + applied_limit]
                was_truncated = len(lines) - offset > applied_limit
                limit_info = f" (limit: {applied_limit}" + (
                    f", offset: {offset}" if offset > 0 else "") + ")" if was_truncated else ""

            if output_mode == "content":
                content = "\n".join(final_lines)
                if not content: return "No matches found"
                if limit_info: content += f"\n\n[Showing results with pagination = {limit_info}]"
                return content

            elif output_mode == "count":
                if not final_lines: return "No matches found"
                content = "\n".join(final_lines)
                total_matches = sum(
                    int(line.rsplit(":", 1)[1])
                    for line in final_lines
                    if ":" in line and line.rsplit(":", 1)[1].isdigit()
                )
                file_count = len(final_lines)
                summary = f"\n\nFound {total_matches} total occurrence{'s' if total_matches != 1 else ''} across {file_count} file{'s' if file_count != 1 else ''}{limit_info}"
                return content + summary

            else:
                if not final_lines: return "No files found"
                file_count = len(final_lines)
                result_lines = [f"Found {file_count} file{'s' if file_count != 1 else ''}{limit_info}"]
                result_lines.extend(final_lines)
                return "\n".join(result_lines)

        except subprocess.TimeoutExpired:
            return "Error: search timed out (60s limit)"
        except Exception as e:
            return f"Error: {e}"

    def get_activity_description(self, kwargs: dict) -> str:
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path", "")
        return f"Searching for '{pattern}' in {path}" if path else f"Searching for '{pattern}'"
