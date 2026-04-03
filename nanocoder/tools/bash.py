"""Shell command execution with safety checks.

Claude Code's BashTool is 1,143 lines. This is the distilled version:
- Output capture with truncation (head+tail preserved)
- Timeout support
- Dangerous command detection
- Working directory tracking (cd awareness)
"""

import os
import re
import subprocess
from typing import Optional
from .base import Tool, ValidationResult, PermissionDecision

# track cwd across commands (Claude Code does this too)
# This is used when no explicit workdir is set on the tool
_cwd: Optional[str] = None

# patterns that could wreck the filesystem or leak secrets
_DANGEROUS_PATTERNS = [
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "recursive delete on home/root"),
    (r"\brm\s+(-\w*)?-rf\s", "force recursive delete"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]", "overwrite block device"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "chmod 777 on root"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork bomb"),
    (r"\bcurl\b.*\|\s*(sudo\s+)?bash", "pipe curl to bash"),
    (r"\bwget\b.*\|\s*(sudo\s+)?bash", "pipe wget to bash"),
]


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command. Returns stdout, stderr, and exit code. "
        "Use this for running tests, installing packages, git operations, etc. "
        "Output is automatically truncated (head + tail preserved) for large outputs."
    )

    search_hint = "run shell commands"
    
    # Optional working directory (set by Agent if specified)
    workdir: Optional[str] = None

    @property
    def is_read_only(self) -> bool:
        # Conservative: bash can modify state, so default to False
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return False  # shell commands can have side effects

    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120)",
            },
        },
        "required": ["command"],
    }

    def validate_input(self, command: str, **kwargs) -> ValidationResult:
        """Validate the command before execution."""
        if not command or not command.strip():
            return ValidationResult(
                valid=False,
                message="Error: Command cannot be empty",
                error_code=1
            )
        
        # Check for dangerous patterns
        warning = _check_dangerous(command)
        if warning:
            return ValidationResult(
                valid=False,
                message=f"Blocked: {warning}\nCommand: {command}\nIf intentional, modify the command to be more specific.",
                error_code=2
            )
        
        return ValidationResult(valid=True)

    def execute(self, command: str, timeout: int = 120) -> str:
        global _cwd
        
        # Validate input first
        validation = self.validate_input(command)
        if not validation.valid:
            return validation.message

        # use working directory: explicit workdir > tracked cwd > current directory
        cwd = self.workdir or _cwd or os.getcwd()

        # Windows compatibility: use bash -c "command" for WSL/Git Bash
        if os.name == "nt":  # Windows
            import shutil
            bash_path = shutil.which("bash")
            if bash_path:
                # Use bash -c to execute commands in WSL/Git Bash
                command = f'"{bash_path}" -c "{command}"'

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                cwd=cwd,
            )

            # track cd commands so next command runs in the right place
            if proc.returncode == 0:
                _update_cwd(command, cwd)
            
            out = proc.stdout
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"
            
            # keep head + tail to preserve the most useful info
            # Claude Code uses 15K limit with 6K head + 3K tail
            if len(out) > 15_000:
                head_size = 6000
                tail_size = 3000
                out = (
                    out[:head_size]
                    + f"\n\n... truncated ({len(out)} chars total, showing first {head_size} and last {tail_size} chars) ...\n\n"
                    + out[-tail_size:]
                )
            
            return out.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except Exception as e:
            return f"Error running command: {e}"

    def get_activity_description(self, kwargs: dict) -> str:
        """Get a short description of what the tool is doing."""
        command = kwargs.get("command", "")
        # Extract first few words for brevity
        words = command.split()[:5]
        cmd_preview = " ".join(words)
        if len(command) > len(cmd_preview):
            cmd_preview += "..."
        return f"Running: {cmd_preview}"


def _check_dangerous(cmd: str) -> Optional[str]:
    """Return a warning string if the command looks destructive, else None."""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return reason
    return None


def _update_cwd(command: str, current_cwd: str):
    """Track directory changes from cd commands."""
    global _cwd
    # simple heuristic: look for cd at the end of a && chain or standalone
    parts = command.split("&&")
    for part in parts:
        part = part.strip()
        if part.startswith("cd "):
            target = part[3:].strip().strip("'\"")
            if target:
                new_dir = os.path.normpath(os.path.join(current_cwd, os.path.expanduser(target)))
                if os.path.isdir(new_dir):
                    _cwd = new_dir
