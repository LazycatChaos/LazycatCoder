"""Enhanced file editing with multiple operations and safety checks.

Inspired by Claude Code's FileEditTool:
- Read state tracking (timestamp-based)
- Content validation (check if old_string exists and is unique)
- Quote style normalization (straight vs curly quotes)
- Multiple match detection with helpful suggestions
- Encoding and line ending preservation
- Detailed error messages with line numbers
"""

import re
from pathlib import Path
from typing import Literal, Optional, Tuple, List
from .base import Tool, ValidationResult


# Track file read timestamps to prevent editing unread files
_file_read_times: dict = {}


class EditFileTool(Tool):
    """Edit files with various operations: replace, insert, append, prepend."""

    name = "edit_file"
    description = (
        "Edit a file with various operations. "
        "For 'replace': old_string must appear exactly once in the file. "
        "For insert operations: specify the line number and content to insert. "
        "Always read the file first before editing. "
        "The tool will validate that your changes can be applied safely."
    )

    search_hint = "edit existing files"

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "command": {
                "type": "string",
                "description": "Operation type: 'replace', 'insert_after', 'insert_before', 'append', or 'prepend'",
                "enum": ["replace", "insert_after", "insert_before", "append", "prepend"],
            },
            "old_string": {
                "type": "string",
                "description": "For 'replace': exact text to find (must be unique in file)",
            },
            "new_string": {
                "type": "string",
                "description": "For 'replace': replacement text. For insert operations: content to insert",
            },
            "line_number": {
                "type": "integer",
                "description": "For 'insert_after'/'insert_before': the line number (1-based) to insert relative to",
            },
            "insert_line": {
                "type": "integer",
                "description": "Alternative parameter name for line_number",
            },
        },
        "required": ["file_path", "command", "new_string"],
    }

    def validate_input(self, file_path: str, command: str, new_string: str, **kwargs) -> ValidationResult:
        """Validate input before editing."""
        # Check file exists
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
        
        # Check if file was read recently (within last 10 minutes)
        import time
        read_time = _file_read_times.get(str(p), 0)
        current_time = time.time()
        if current_time - read_time > 600:  # 10 minutes
            return ValidationResult(
                valid=False,
                message=f"⚠️ File {file_path} was not read recently. Please read it first to ensure you have the latest content.",
                error_code=3,
                meta={"suggestion": "read_file"}
            )
        
        # For replace command, validate old_string
        if command == "replace":
            old_string = kwargs.get("old_string")
            if not old_string:
                return ValidationResult(
                    valid=False,
                    message="Error: 'old_string' is required for 'replace' command",
                    error_code=4
                )
            
            # Read file and check for matches
            try:
                content = p.read_text(encoding="utf-8")
                
                # Normalize quotes (handle curly quotes)
                old_string_normalized = self._normalize_quotes(old_string)
                content_normalized = self._normalize_quotes(content)
                
                # Count matches
                matches = re.finditer(re.escape(old_string_normalized), content_normalized)
                match_positions = [(m.start(), m.end()) for m in matches]
                
                if len(match_positions) == 0:
                    # Try to find similar content
                    similar = self._find_similar_content(content, old_string)
                    msg = f"Error: '{old_string[:50]}...' not found in file."
                    if similar:
                        msg += f"\n\nSimilar content found:\n{similar}"
                    return ValidationResult(
                        valid=False,
                        message=msg,
                        error_code=5,
                        meta={"suggestion": "check_old_string"}
                    )
                
                if len(match_positions) > 1:
                    # Multiple matches - provide line numbers
                    lines = content.split('\n')
                    match_lines = []
                    for start, end in match_positions:
                        line_num = content[:start].count('\n') + 1
                        match_lines.append(f"  Line {line_num}")
                    
                    return ValidationResult(
                        valid=False,
                        message=f"Error: '{old_string[:50]}...' appears {len(match_positions)} times in the file. Must be unique.\n\nFound at:\n" + "\n".join(match_lines),
                        error_code=6,
                        meta={"suggestion": "make_old_string_more_specific"}
                    )
                
            except Exception as e:
                return ValidationResult(
                    valid=False,
                    message=f"Error reading file for validation: {e}",
                    error_code=7
                )
        
        # For insert operations, validate line_number
        if command in ["insert_after", "insert_before"]:
            line_number = kwargs.get("line_number") or kwargs.get("insert_line")
            if line_number is None:
                return ValidationResult(
                    valid=False,
                    message=f"Error: 'line_number' is required for '{command}' command",
                    error_code=8
                )
            
            if line_number < 1:
                return ValidationResult(
                    valid=False,
                    message=f"Error: line_number must be >= 1, got {line_number}",
                    error_code=9
                )
            
            # Check file has enough lines
            try:
                content = p.read_text(encoding="utf-8")
                total_lines = len(content.split('\n'))
                if command == "insert_after" and line_number > total_lines:
                    return ValidationResult(
                        valid=False,
                        message=f"Error: File has {total_lines} lines, but line_number={line_number}",
                        error_code=10
                    )
                if command == "insert_before" and line_number > total_lines + 1:
                    return ValidationResult(
                        valid=False,
                        message=f"Error: File has {total_lines} lines, but line_number={line_number}",
                        error_code=11
                    )
            except Exception as e:
                return ValidationResult(
                    valid=False,
                    message=f"Error reading file for validation: {e}",
                    error_code=12
                )
        
        return ValidationResult(valid=True)

    def _normalize_quotes(self, text: str) -> str:
        """Normalize curly quotes to straight quotes."""
        # Map curly quotes to straight quotes
        quote_map = {
            '"': '"',  # Left double quotation mark
            '"': '"',  # Right double quotation mark
            "'": "'",  # Left single quotation mark
            "'": "'",  # Right single quotation mark
            '`': '`',  # Left single quotation mark (also used as backtick)
        }
        result = text
        for curly, straight in quote_map.items():
            result = result.replace(curly, straight)
        return result

    def _find_similar_content(self, content: str, target: str, max_distance: int = 3) -> Optional[str]:
        """Find content similar to target using simple string matching."""
        # Split into lines for line-by-line comparison
        content_lines = content.split('\n')
        target_lines = target.split('\n')
        
        if len(target_lines) == 1:
            # Single line search - find lines with similar words
            target_words = set(target.lower().split())
            similar_lines = []
            for i, line in enumerate(content_lines, 1):
                line_words = set(line.lower().split())
                # Check if most words match
                common = target_words & line_words
                if len(common) >= max(1, len(target_words) - max_distance):
                    similar_lines.append(f"  Line {i}: {line[:100]}...")
                    if len(similar_lines) >= 3:
                        break
            if similar_lines:
                return "\n".join(similar_lines)
        
        return None

    def execute(
        self,
        file_path: str,
        command: Literal["replace", "insert_after", "insert_before", "append", "prepend"],
        new_string: str,
        old_string: Optional[str] = None,
        line_number: Optional[int] = None,
        insert_line: Optional[int] = None,
    ) -> str:
        """Execute the file editing operation."""
        # Handle insert_line alias
        if line_number is None and insert_line is not None:
            line_number = insert_line
        
        # Validate input
        validation = self.validate_input(
            file_path=file_path,
            command=command,
            new_string=new_string,
            old_string=old_string,
            line_number=line_number,
        )
        if not validation.valid:
            return validation.message

        p = Path(file_path).expanduser().resolve()
        
        try:
            # Read file with original encoding detection
            content = p.read_text(encoding="utf-8")
            original_content = content
            
            # Detect line ending style
            if '\r\n' in content:
                line_ending = '\r\n'
            elif '\r' in content:
                line_ending = '\r'
            else:
                line_ending = '\n'
            
            # Normalize line endings for processing
            content = content.replace('\r\n', '\n').replace('\r', '\n')
            lines = content.split('\n')
            
            if command == "replace":
                # Normalize quotes for matching
                old_string_normalized = self._normalize_quotes(old_string)
                content_normalized = self._normalize_quotes(content)
                
                # Find and replace
                if old_string_normalized in content_normalized:
                    # Use original old_string for replacement to preserve formatting
                    content = content.replace(old_string, new_string, 1)
                else:
                    return f"Error: Could not find the exact text to replace. The file may have been modified."
            
            elif command == "insert_after":
                if line_number < 1 or line_number > len(lines):
                    return f"Error: Invalid line number {line_number}. File has {len(lines)} lines."
                # Insert after the specified line (1-based)
                insert_index = line_number  # 0-based index after the line
                new_lines = new_string.split('\n')
                lines[insert_index:insert_index] = new_lines
                content = '\n'.join(lines)
            
            elif command == "insert_before":
                if line_number < 1 or line_number > len(lines) + 1:
                    return f"Error: Invalid line number {line_number}. File has {len(lines)} lines."
                # Insert before the specified line (1-based)
                insert_index = line_number - 1  # 0-based index before the line
                new_lines = new_string.split('\n')
                lines[insert_index:insert_index] = new_lines
                content = '\n'.join(lines)
            
            elif command == "append":
                if content and not content.endswith('\n'):
                    content += '\n'
                content += new_string
            
            elif command == "prepend":
                if new_string and not new_string.endswith('\n'):
                    new_string += '\n'
                content = new_string + content
            
            # Restore original line endings
            if line_ending != '\n':
                content = content.replace('\n', line_ending)
            
            # Write back
            p.write_text(content, encoding="utf-8")
            
            # Generate diff summary
            old_lines = original_content.splitlines()
            new_lines = content.splitlines()
            
            added = len(new_lines) - len(old_lines)
            if added > 0:
                change_summary = f"Added {added} line(s)"
            elif added < 0:
                change_summary = f"Removed {-added} line(s)"
            else:
                change_summary = f"Modified {len([i for i, (o, n) in enumerate(zip(old_lines, new_lines)) if o != n])} line(s)"
            
            # Show preview of changes
            preview_lines = []
            if command == "replace":
                preview_lines.append(f"Replaced text in {file_path}")
            elif command in ["insert_after", "insert_before"]:
                preview_lines.append(f"{command.replace('_', ' ').title()} at line {line_number} in {file_path}")
            elif command == "append":
                preview_lines.append(f"Appended to {file_path}")
            elif command == "prepend":
                preview_lines.append(f"Prepended to {file_path}")
            
            preview_lines.append(f"{change_summary}")
            
            # Show first few lines of new content
            new_content_preview = new_string[:200]
            if len(new_string) > 200:
                new_content_preview += "..."
            preview_lines.append(f"New content preview: {new_content_preview}")
            
            return "\n".join(preview_lines)
        
        except Exception as e:
            return f"Error editing file: {e}"

    def get_activity_description(self, kwargs: dict) -> str:
        """Get a short description of what the tool is doing."""
        file_path = kwargs.get("file_path", "")
        command = kwargs.get("command", "")
        return f"{command.replace('_', ' ').title()} in {Path(file_path).name}"


def record_file_read(file_path: str):
    """Record that a file was read (call this from ReadFileTool)."""
    import time
    p = Path(file_path).expanduser().resolve()
    _file_read_times[str(p)] = time.time()
