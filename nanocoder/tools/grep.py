"""Enhanced GrepTool with multiple output modes and advanced features.

Inspired by Claude Code's GrepTool (ripgrep wrapper):
- Multiple output modes: content, files_with_matches, count
- Context lines control (-A, -B, -C)
- Pagination support (head_limit, offset)
- File type filtering
- Automatic VCS directory exclusion
- Results sorted by modification time
"""

import subprocess
import os
from pathlib import Path
from typing import List, Optional, Literal
from .base import Tool, ValidationResult


class GrepTool(Tool):
    """Search file contents with regex using ripgrep."""

    name = "grep"
    description = (
        "Search file contents with regex (ripgrep). "
        "Supports multiple output modes: 'content' (matching lines), 'files_with_matches' (file paths), 'count' (match counts). "
        "Use -n for line numbers, -i for case insensitive, -C/-A/-B for context lines. "
        "Automatically excludes .git and other VCS directories. "
        "Results are limited to 250 by default (use head_limit=0 for unlimited)."
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
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to current directory.",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. '*.js', '*.{ts,tsx}')",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode: 'content' shows matching lines, 'files_with_matches' shows file paths, 'count' shows match counts. Default: 'files_with_matches'.",
            },
            "-B": {
                "type": "integer",
                "description": "Number of lines before each match (requires output_mode='content')",
            },
            "-A": {
                "type": "integer",
                "description": "Number of lines after each match (requires output_mode='content')",
            },
            "-C": {
                "type": "integer",
                "description": "Number of lines before and after each match (requires output_mode='content')",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers. Default: true.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "type": {
                "type": "string",
                "description": "File type to search (e.g. js, py, rust, go, java)",
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N lines/entries. Default: 250. Pass 0 for unlimited.",
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N lines/entries before applying head_limit. Default: 0.",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode where . matches newlines. Default: false.",
            },
        },
        "required": ["pattern"],
    }

    # VCS directories to exclude
    VCS_DIRS = ['.git', '.svn', '.hg', '.bzr', '.jj', '.sl']
    
    # Default result limit to prevent context bloat
    DEFAULT_HEAD_LIMIT = 250

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
        
        return ValidationResult(valid=True)

    def execute(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        output_mode: Literal["content", "files_with_matches", "count"] = "files_with_matches",
        before_context: Optional[int] = None,  # -B
        after_context: Optional[int] = None,  # -A
        context: Optional[int] = None,  # -C
        line_numbers: bool = True,  # -n
        case_insensitive: bool = False,  # -i
        file_type: Optional[str] = None,  # type
        head_limit: Optional[int] = None,
        offset: int = 0,
        multiline: bool = False,
    ) -> str:
        """Execute grep search."""
        # Validate input
        validation = self.validate_input(pattern)
        if not validation.valid:
            return validation.message

        # Build ripgrep command
        args = ["rg", "--hidden"]
        
        # Exclude VCS directories
        for vcs_dir in self.VCS_DIRS:
            args.extend(["--glob", f"!{vcs_dir}"])
        
        # Limit line length to prevent clutter
        args.extend(["--max-columns", "500"])
        
        # Multiline mode
        if multiline:
            args.extend(["-U", "--multiline-dotall"])
        
        # Case insensitive
        if case_insensitive:
            args.append("-i")
        
        # Output mode flags
        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        elif output_mode == "content" and line_numbers:
            args.append("-n")
        
        # Context lines (for content mode)
        if output_mode == "content":
            if context is not None:
                args.extend(["-C", str(context)])
            else:
                if before_context is not None:
                    args.extend(["-B", str(before_context)])
                if after_context is not None:
                    args.extend(["-A", str(after_context)])
        
        # Handle patterns starting with dash
        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)
        
        # File type filter
        if file_type:
            args.extend(["--type", file_type])
        
        # Glob filter
        if glob:
            # Split on commas and spaces
            for g in glob.replace(" ", ",").split(","):
                g = g.strip()
                if g:
                    args.extend(["--glob", g])
        
        # Set working directory
        search_path = path
        if search_path:
            p = Path(search_path).expanduser().resolve()
            search_path = str(p)
        else:
            search_path = os.getcwd()
        
        try:
            # Determine ripgrep executable path
            # Try 'rg' first, then fallback to Windows path if on WSL
            import shutil
            rg_executable = shutil.which('rg') or 'rg'
            
            # If rg not found and running in WSL, try to use Windows rg.exe
            if rg_executable == 'rg' and os.path.exists('/proc/version'):
                with open('/proc/version') as f:
                    version = f.read().lower()
                if 'microsoft' in version or 'wsl' in version:
                    # Try to get rg path from Windows using where.exe and wslpath
                    try:
                        # First get Windows path using where.exe
                        result = subprocess.run(
                            ['cmd.exe', '/c', 'where.exe', 'rg'],
                            capture_output=True, text=True, timeout=5
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            win_path = result.stdout.strip().split('\n')[0]
                            # Convert to WSL path
                            result = subprocess.run(
                                ['wslpath', '-u', win_path],
                                capture_output=True, text=True, timeout=5
                            )
                            if result.returncode == 0 and result.stdout.strip():
                                rg_executable = result.stdout.strip()
                    except Exception:
                        pass
            
            # Execute ripgrep
            result = subprocess.run(
                [rg_executable] + args[1:],  # Replace 'rg' with determined executable
                cwd=search_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            
            lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
            
            # Apply pagination
            applied_limit = head_limit if head_limit is not None else self.DEFAULT_HEAD_LIMIT
            if applied_limit == 0:
                # Unlimited
                final_lines = lines[offset:]
                limit_info = ""
            else:
                final_lines = lines[offset:offset + applied_limit]
                was_truncated = len(lines) - offset > applied_limit
                limit_info = f" (limit: {applied_limit}" + (f", offset: {offset}" if offset > 0 else "") + ")" if was_truncated else ""
            
            # Format output based on mode
            if output_mode == "content":
                content = "\n".join(final_lines)
                if not content:
                    return "No matches found"
                if limit_info:
                    content += f"\n\n[Showing results with pagination = {limit_info}]"
                return content
            
            elif output_mode == "count":
                if not final_lines:
                    return "No matches found"
                content = "\n".join(final_lines)
                # Parse total matches
                total_matches = sum(
                    int(line.rsplit(":", 1)[1])
                    for line in final_lines
                    if ":" in line and line.rsplit(":", 1)[1].isdigit()
                )
                file_count = len(final_lines)
                summary = f"\n\nFound {total_matches} total occurrence{'s' if total_matches != 1 else ''} across {file_count} file{'s' if file_count != 1 else ''}{limit_info}"
                return content + summary
            
            else:  # files_with_matches
                if not final_lines:
                    return "No files found"
                file_count = len(final_lines)
                result_lines = [f"Found {file_count} file{'s' if file_count != 1 else ''}{limit_info}"]
                result_lines.extend(final_lines)
                return "\n".join(result_lines)
        
        except subprocess.TimeoutExpired:
            return "Error: search timed out (60s limit)"
        except FileNotFoundError:
            return "Error: ripgrep (rg) not found. Please install ripgrep."
        except Exception as e:
            return f"Error: {e}"

    def get_activity_description(self, kwargs: dict) -> str:
        """Get a short description of what the tool is doing."""
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path", "")
        if path:
            return f"Searching for '{pattern}' in {path}"
        return f"Searching for '{pattern}'"
