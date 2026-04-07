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
import shutil
from pathlib import Path
from typing import Optional, Literal
from .base import Tool, ValidationResult

# 跨命令跟踪当前工作目录
_cwd: Optional[str] = None

# 危险命令模式（已增加 Windows/PowerShell 的高危操作拦截）
_DANGEROUS_PATTERNS = [
    # Linux / Bash — 递归删除（任何路径，不限于根目录）
    (r"\brm\s+(-\w*)?-rf\s", "force recursive delete (rm -rf)"),
    (r"\brm\s+-r\s", "recursive delete (rm -r)"),
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "recursive delete on home/root"),
    # Windows / PowerShell — 递归删除（任何路径）
    (r"(?i)\bRemove-Item\s+.*-Recurse", "recursive delete (Remove-Item -Recurse)"),
    (r"(?i)\brmdir\s+/s", "recursive delete (rmdir /s)"),
    (r"(?i)\bdel\s+/s", "recursive delete (del /s)"),
    # 文件系统破坏
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]", "overwrite block device"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "chmod 777 on root"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork bomb"),
    # Windows — 格式化
    (r"(?i)\bformat\s+[A-Z]:", "format drive"),
]


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell/PowerShell command. Returns stdout, stderr, and exit code. "
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
                "description": "The shell/PowerShell command to run",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120)",
            },
        },
        "required": ["command"],
    }

    def validate_input(self, command: str, **kwargs) -> ValidationResult:
        if not command or not command.strip():
            return ValidationResult(valid=False, message="Error: Command cannot be empty", error_code=1)

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

        validation = self.validate_input(command)
        if not validation.valid:
            return validation.message

        cwd = self.workdir or _cwd or os.getcwd()

        # Windows 下使用 PowerShell 替代 bash
        if os.name == "nt":
            # 优先寻找 PowerShell Core (pwsh)，否则用自带的 powershell
            ps_path = shutil.which("pwsh") or shutil.which("powershell")
            if ps_path:
                # 使用 -NoProfile 提升启动速度
                command = f'"{ps_path}" -NoProfile -NonInteractive -Command {command}'

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

            # 如果执行成功，尝试更新工作目录
            if proc.returncode == 0:
                _update_cwd(command, cwd)

            out = proc.stdout or ""
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"

            # 截断处理 (保护 Context Length)
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
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return reason
    return None


def _update_cwd(command: str, current_cwd: str):
    """Track directory changes from cd commands (supports Bash and PowerShell)."""
    global _cwd
    # PowerShell 常用 ';' 或 '&&' 连接命令
    parts = re.split(r'&&|;', command)
    for part in parts:
        part = part.strip()
        # 匹配 cd, Set-Location, sl, chdir 等
        match = re.match(r'^(cd|Set-Location|sl|chdir)\s+(.+)', part, re.IGNORECASE)
        if match:
            target = match.group(2).strip().strip("'\"")
            if target:
                new_dir = os.path.normpath(os.path.join(current_cwd, os.path.expanduser(target)))
                if os.path.isdir(new_dir):
                    _cwd = new_dir
