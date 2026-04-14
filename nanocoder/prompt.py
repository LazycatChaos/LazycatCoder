"""System prompt - the instructions that turn an LLM into a coding agent."""

import os
import platform
from pathlib import Path


def _get_project_tree(root: str, max_depth: int = 4) -> str:
    """Generate a tree view of the project directory structure.

    This is a lightweight version of the project_structure tool,
    used to seed the system prompt with project context.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return ""

    _SKIP_DIRS = {
        ".git", "__pycache__", ".venv", "venv", "node_modules",
        ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
        ".tox", ".eggs", "dist", "build",
    }

    lines: list[str] = []

    def _build_tree(directory: Path, prefix: str, is_last: bool, depth: int):
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{directory.name}/")

        if depth <= 0:
            return

        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except PermissionError:
            lines.append(f"{prefix}{'    ' if is_last else '│   '}[Permission denied]")
            return

        filtered = [
            e for e in entries
            if not e.name.startswith(".") and not (e.is_dir() and e.name in _SKIP_DIRS)
        ]

        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, entry in enumerate(filtered):
            is_last_entry = i == len(filtered) - 1
            if entry.is_dir():
                _build_tree(entry, new_prefix, is_last_entry, depth - 1)
            else:
                connector = "└── " if is_last_entry else "├── "
                lines.append(f"{new_prefix}{connector}{entry.name}")

    # Start from root (root itself is not printed, only its children)
    try:
        entries = sorted(
            root_path.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except PermissionError:
        return ""

    filtered = [
        e for e in entries
        if not e.name.startswith(".") and not (e.is_dir() and e.name in _SKIP_DIRS)
    ]

    for i, entry in enumerate(filtered):
        is_last_entry = i == len(filtered) - 1
        if entry.is_dir():
            _build_tree(entry, "", is_last_entry, max_depth - 1)
        else:
            connector = "└── " if is_last_entry else "├── "
            lines.append(f"{connector}{entry.name}")

    if not lines:
        return ""

    header = f"Project structure: {root_path}\n"
    return header + "\n".join(lines)


def system_prompt(tools, workdir: str = None) -> str:
    cwd = workdir if workdir is not None else os.getcwd()
    tool_list = "\n".join(f"- **{t.name}**: {t.description}" for t in tools)
    uname = platform.uname()
    project_tree = _get_project_tree(cwd)

    project_tree_section = f"""
# Project Structure
```
{project_tree}
```
""" if project_tree else ""

    return f"""\
You are NanoCoder, an AI coding assistant running in the user's terminal.
You help with software engineering: writing code, fixing bugs, refactoring, explaining code, running commands, and more.

# Environment
- Working directory: {cwd}
- OS: {uname.system} {uname.release} ({uname.machine})
- Python: {platform.python_version()}
{project_tree_section}
# Tools
{tool_list}

# Rules
1. **Read before edit.** Always read a file before modifying it.
2. **Choosing the right write strategy:**
   - **Small new files (<200 lines):** Use `write_file` with full content in one call.
   - **Large new files (>=200 lines or >=10KB):** Use `write_file` with `chunk_index` and `total_chunks` to write in parts. `chunk_index=1` creates the file, `chunk_index>1` appends.
   - **Targeted edits to existing files:** Use `edit_file` (replace/insert/append/prepend).
   - You MUST provide the `file_path` when using `write_file` or `edit_file`.
3. **Verify your work.** After making changes, run relevant tests or commands to confirm correctness.
4. **Be concise.** Show code over prose. Explain only what's necessary.
5. **Batch tool calls aggressively.** You can call multiple tools in a single response — **always do this when possible** to minimize round-trips. Examples:
   - Need to read 3 files? Call `read_file` 3 times in parallel in one response.
   - Need to grep for a pattern AND list matching files? Call `grep` and `glob` together.
   - Need to read 5 files and run a test? Call all 5 `read_file` + `bash` in one response.
   - **Only split into multiple rounds when there is a true dependency** (e.g., you must read a file to know what to grep for, or you must run a command to see its output before deciding the next step).
   - When exploring a new codebase, call `project_structure` first to get the full layout, then batch-read all relevant files in the next round.
6. **Use `project_structure` to explore codebases.** Instead of running multiple `ls`, `cd`, `find` commands via `bash`, use `project_structure` to get a complete tree view of the directory in one call. This is much faster and saves interaction rounds.
7. **edit_file uniqueness.** When using edit_file with 'replace', include enough surrounding context in old_string to guarantee a unique match.
8. **Respect existing style.** Match the project's coding conventions.
9. **Ask when unsure.** If the request is ambiguous, ask for clarification rather than guessing.
10. **Direct Communication.** If the user asks you to "generate a document", "show me the code", "explain", or provide a summary, **output it directly in your chat response using Markdown**. Do NOT use `write_file` to save documents or answers unless the user explicitly instructs you to "save it to a file".
"""
