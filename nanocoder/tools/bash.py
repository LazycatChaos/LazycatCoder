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
from typing import Optional, Literal, Callable
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
    # Optional virtual environment path (auto-activated before commands)
    venv_path: Optional[str] = None
    # Cancellation callback (set by Agent)
    _is_cancelled: Optional[Callable[[], bool]] = None

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

        # 检测交互式命令（会卡住的命令）
        interactive_warning = self._check_interactive(command)
        if interactive_warning:
            return (
                f"Error: 检测到交互式命令，不适合在 bash 工具中运行。\n"
                f"命令: {command}\n"
                f"原因: {interactive_warning}\n"
                f"建议: 使用 -c 参数传递代码/命令，例如: python -c \"print('hello')\" 或 python manage.py shell -c \"...\""
            )

        # 如果有虚拟环境，在执行命令前添加激活前缀
        command = self._prepend_venv_activation(command, cwd)

        # Windows 下使用 PowerShell 替代 bash
        if os.name == "nt":
            # 优先寻找 PowerShell Core (pwsh)，否则用自带的 powershell
            ps_path = shutil.which("pwsh") or shutil.which("powershell")
            if ps_path:
                # 使用 -NoProfile 提升启动速度
                command = f'"{ps_path}" -NoProfile -NonInteractive -Command {command}'

        try:
            # Windows 中文环境下 PowerShell 默认输出 GBK 编码，
            # 直接指定 encoding="utf-8" 会导致中文乱码。
            # 策略：先以 bytes 捕获，再智能解码。
            # 使用 Popen + communicate 替代 run，以便超时后能 kill 整个进程树
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )

            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 超时后 kill 整个进程树（防止孤儿进程）
                _kill_process_tree(proc.pid)
                proc.wait()
                return f"Error: timed out after {timeout}s (process killed)"

            stdout = _decode_output(stdout)
            stderr = _decode_output(stderr)

            # 如果执行成功，尝试更新工作目录
            if proc.returncode == 0:
                _update_cwd(command, cwd)

            out = stdout or ""
            if stderr:
                out += f"\n[stderr]\n{stderr}"
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

    def _prepend_venv_activation(self, command: str, cwd: str) -> str:
        """在命令前添加虚拟环境激活逻辑（仅当命令涉及 Python 时）。"""
        venv = self.venv_path
        if not venv:
            return command

        venv_path = Path(venv)
        if not venv_path.exists():
            return command

        # 判断命令是否需要 Python 环境
        python_keywords = ["python", "pip", "pytest", "django", "manage.py", "flask", "uvicorn", "gunicorn"]
        needs_python = any(kw in command.lower() for kw in python_keywords)
        if not needs_python:
            return command

        # 构建激活命令
        if os.name == "nt":
            # Windows: 使用 Activate.ps1
            activate_script = venv_path / "Scripts" / "Activate.ps1"
            if activate_script.exists():
                # PowerShell: 先激活，再执行命令
                return f"& '{activate_script}'; {command}"
        else:
            # Linux/macOS: 使用 activate
            activate_script = venv_path / "bin" / "activate"
            if activate_script.exists():
                return f"source '{activate_script}' && {command}"

        return command

    @staticmethod
    def _check_interactive(command: str) -> Optional[str]:
        """检测可能导致卡住的交互式命令。

        返回 None 表示安全，返回字符串表示警告原因。
        """
        cmd_lower = command.lower().strip()

        # 交互式 Python shell（没有 -c 参数）
        if re.match(r'^(python|python3|py)\s*$', cmd_lower):
            return "启动了交互式 Python shell，会一直等待输入"

        # Django shell（没有 -c 参数）
        if re.match(r'python\s+manage\.py\s+shell\s*$', cmd_lower):
            return "启动了 Django 交互式 shell，会一直等待输入"
        # 检查 cd && python manage.py shell 这种组合
        last_part = cmd_lower.split('&&')[-1].strip()
        if re.match(r'python\s+manage\.py\s+shell\s*$', last_part):
            return "启动了 Django 交互式 shell，会一直等待输入"

        # 其他交互式命令
        interactive_cmds = [
            (r'\bipython\s*$', '启动了 IPython 交互式 shell'),
            (r'\bbpython\s*$', '启动了 bpython 交互式 shell'),
            (r'\bnode\s*$', '启动了 Node.js 交互式 REPL'),
            (r'\btop\b', '启动了 top 监控（持续运行）'),
            (r'\bhtop\b', '启动了 htop 监控（持续运行）'),
            (r'\bnano\b', '启动了 nano 编辑器'),
            (r'\bvim\b', '启动了 vim 编辑器'),
            (r'\bvi\s', '启动了 vi 编辑器'),
        ]

        for pattern, reason in interactive_cmds:
            if re.search(pattern, cmd_lower):
                return reason

        return None


def _decode_output(data: bytes) -> str:
    """智能解码 subprocess 输出，兼容 UTF-8 和 GBK。

    Windows 中文环境下 PowerShell 默认输出 GBK 编码，
    而 Linux/macOS 通常是 UTF-8。此函数自动尝试多种编码。
    """
    if not data:
        return ""
    # 优先尝试 UTF-8
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # 回退到系统默认编码（中文 Windows 下通常是 cp936/GBK）
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        pass
    # 最后兜底：用 replace 模式强制解码
    return data.decode("utf-8", errors="replace")


def _check_dangerous(cmd: str) -> Optional[str]:
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return reason
    return None


def _kill_process_tree(pid: int):
    """Kill a process and all its children (prevents orphan processes).

    On Windows: uses taskkill /T /F to kill the entire process tree.
    On Linux/macOS: uses os.killpg or recursive kill.
    """
    if os.name == "nt":
        # Windows: taskkill /T kills the process tree, /F forces termination
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )
    else:
        # Linux/macOS: try process group first, then individual kill
        try:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass  # Already dead


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
