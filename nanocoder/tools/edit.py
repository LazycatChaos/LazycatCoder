"""File editing with multiple operations: replace, insert, append, prepend.

This tool supports various editing operations:
- **replace**: Replace an exact substring (original Claude Code style)
- **insert_after**: Insert new content after a specific line number
- **insert_before**: Insert new content before a specific line number  
- **append**: Add content to the end of the file
- **prepend**: Add content to the beginning of the file
"""

import difflib
from pathlib import Path

from .base import Tool


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file with various operations. "
        "Use 'command' to specify the operation type: 'replace', 'insert_after', 'insert_before', 'append', or 'prepend'. "
        "For 'replace', old_string must appear exactly once. "
        "For insert operations, specify the line number and content to insert."
    )
    
    @property
    def is_read_only(self) -> bool:
        return False  # modifies file content
    
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "command": {
                "type": "string",
                "description": "Operation type: 'replace', 'insert_after', 'insert_before', 'append', 'prepend'",
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
        },
        "required": ["file_path", "command", "new_string"],
    }

    def execute(
        self,
        file_path: str,
        command: str,
        new_string: str,
        old_string: str = None,
        line_number: int = None,
    ) -> str:
        try:
            p = Path(file_path).expanduser().resolve()
            if not p.exists():
                return f"Error: {file_path} not found"

            content = p.read_text()
            old_content = content
            
            if command == "replace":
                if not old_string:
                    return "Error: 'old_string' is required for 'replace' command"
                return self._do_replace(p, content, old_string, new_string)
            
            elif command == "append":
                # Append to end of file
                if content and not content.endswith("\n"):
                    new_string = "\n" + new_string
                if not new_string.endswith("\n"):
                    new_string = new_string + "\n"
                new_content = content + new_string
                return self._save_and_diff(p, old_content, new_content, file_path)
            
            elif command == "prepend":
                # Prepend to beginning of file
                if not new_string.endswith("\n"):
                    new_string = new_string + "\n"
                new_content = new_string + content
                return self._save_and_diff(p, old_content, new_content, file_path)
            
            elif command == "insert_after":
                if line_number is None:
                    return "Error: 'line_number' is required for 'insert_after' command"
                return self._do_insert(p, content, line_number, new_string, after=True)
            
            elif command == "insert_before":
                if line_number is None:
                    return "Error: 'line_number' is required for 'insert_before' command"
                return self._do_insert(p, content, line_number, new_string, after=False)
            
            else:
                return f"Error: unknown command '{command}'"
                
        except Exception as e:
            return f"Error: {e}"
    
    def _do_replace(self, p: Path, content: str, old_string: str, new_string: str) -> str:
        """Perform a replace operation."""
        occurrences = content.count(old_string)

        if occurrences == 0:
            preview = content[:500] + ("..." if len(content) > 500 else "")
            return (
                f"Error: old_string not found in {p}.\n"
                f"File starts with:\n{preview}"
            )
        if occurrences > 1:
            # find line numbers where old_string appears
            lines = content.splitlines()
            match_lines = []
            for i, line in enumerate(lines, start=1):
                if old_string in line:
                    match_lines.append(i)
                    if len(match_lines) >= 10:  # cap at 10 to avoid huge error messages
                        break
            
            line_info = f" at lines: {match_lines[:10]}"
            if len(match_lines) >= 10 and occurrences > 10:
                line_info += f" (and {occurrences - 10} more)"
            
            return (
                f"Error: old_string appears {occurrences} times in {p}{line_info}. "
                f"Include more surrounding lines to make it unique."
            )

        new_content = content.replace(old_string, new_string, 1)
        return self._save_and_diff(p, content, new_content, str(p))
    
    def _do_insert(self, p: Path, content: str, line_number: int, new_string: str, after: bool) -> str:
        """Insert content at a specific line position."""
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        
        # Handle edge cases for line numbers
        if line_number < 1:
            return f"Error: line_number must be >= 1, got {line_number}"
        if line_number > total_lines + 1:
            return f"Error: file has {total_lines} lines, cannot insert at line {line_number}"
        
        # Ensure new_string ends with newline
        if new_string and not new_string.endswith("\n"):
            new_string = new_string + "\n"
        
        # Calculate insertion index
        # line_number=1, after=False -> insert at index 0 (before first line)
        # line_number=1, after=True -> insert at index 1 (after first line)
        if after:
            insert_index = line_number
        else:
            insert_index = line_number - 1
        
        # Insert the new content
        lines.insert(insert_index, new_string)
        new_content = "".join(lines)
        
        return self._save_and_diff(p, content, new_content, str(p))
    
    def _save_and_diff(self, p: Path, old_content: str, new_content: str, filename: str) -> str:
        """Save the new content and generate a diff."""
        p.write_text(new_content, encoding="utf-8")
        diff = _unified_diff(old_content, new_content, filename)
        return f"Edited {p}\n{diff}"


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    """Generate a compact unified diff between old and new file content."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}", tofile=f"b/{filename}",
        n=context,
    )
    result = "".join(diff)
    # truncate enormous diffs
    if len(result) > 3000:
        result = result[:2500] + "\n... (diff truncated)\n"
    return result
