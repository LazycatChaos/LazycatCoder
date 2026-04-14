"""Shell command execution with safety checks.

Claude Code's BashTool is 1,143 lines. This is the distilled version:
- Output capture with truncation (head+tail preserved)
- Timeout support
- Dangerous command detection
- Working directory tracking (cd awareness)
"""

import base64
import os
import re
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Literal, Callable
from .base import Tool, ValidationResult

# 跨命令跟踪当前工作目录
_cwd: Optional[str] = None

# 危险命令模式
_DANGEROUS_PATTERNS = [
    # Linux / Bash
    (r"\brm\s+(-\w*)?-rf\s", "force recursive delete (rm -rf)"),
    (r"\brm\s+-r\s", "recursive delete (rm -r)"),
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "recursive delete on home/root"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]", "overwrite block device"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "chmod 777 on root"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork bomb"),

    # Windows / PowerShell
    (r"(?i)\bRemove-Item\s+.*-Recurse", "recursive delete (Remove-Item -Recurse)"),
    (r"(?i)\brmdir\s+/s", "recursive delete (rmdir /s)"),
    (r"(?i)\bdel\s+/s", "recursive delete (del /s)"),
    (r"(?i)\bformat\s+[A-Z]:", "format drive"),
    (r"(?i)\bClear-Disk\b", "wipe disk"),
    (r"(?i)\bInitialize-Disk\b", "initialize disk"),
]

# 用于精准捕获实际工作目录的隐形标记
_CWD_MARKER = f"__CWD_MARKER_{os.getpid()}__"


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell/PowerShell command. Returns stdout, stderr, and exit code. "
        "Use this for running tests, installing packages, git operations, etc. "
        "Output is automatically truncated (head + tail preserved) for large outputs."
    )

    search_hint = "run shell commands"

    workdir: Optional[str] = None
    venv_path: Optional[str] = None
    _is_cancelled: Optional[Callable[[], bool]] = None

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        return False

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

        interactive_warning = self._check_interactive(command)
        if interactive_warning:
            return (
                f"Error: 检测到交互式命令，不适合在无头工具中运行。\n"
                f"命令: {command}\n"
                f"原因: {interactive_warning}\n"
                f"建议: 使用 -c 参数传递代码或添加 non-interactive 标志。"
            )

        # 1. 注入虚拟环境激活
        command = self._prepend_venv_activation(command, cwd)

        # 2. 判断环境并注入 CWD 跟踪标记 (修复第 6 点 cmd.exe 兼容性)
        is_windows = os.name == "nt"
        if is_windows:
            ps_path = shutil.which("pwsh") or shutil.which("powershell")
            if ps_path:
                # PowerShell 语法
                command = f"{command}\nWrite-Output '{_CWD_MARKER}:$PWD'"
                encoded = base64.b64encode(command.encode("utf-16-le")).decode()
                # 修复第 4 点：将 ExecutionPolicy 放到解释器参数中，更安全
                cmd_list = [ps_path, "-ExecutionPolicy", "Bypass", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                            encoded]
            else:
                # CMD 语法
                command = f"{command}\necho {_CWD_MARKER}:%CD%"
                cmd_list = ["cmd.exe", "/c", command]
        else:
            # Bash 语法
            command = f"{command}\necho '{_CWD_MARKER}:'$PWD"
            cmd_list = ["/bin/bash", "-c", command]

        try:
            proc = subprocess.Popen(
                cmd_list,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if is_windows else 0,
            )

            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc.pid)
                proc.wait()
                return f"Error: timed out after {timeout}s (process killed)"

            stdout = _decode_output(stdout_bytes)
            stderr = _decode_output(stderr_bytes)

            # 3. 提取并更新真实工作目录 (提取发生在截断前，不用担心长输出导致标记丢失)
            stdout, extracted_cwd = self._extract_and_update_cwd(stdout)
            if extracted_cwd:
                _cwd = extracted_cwd

            out = stdout.strip()
            if stderr.strip():
                if out:
                    out += f"\n\n[stderr]\n{stderr.strip()}"
                else:
                    out = f"[stderr]\n{stderr.strip()}"

            if proc.returncode != 0:
                out += f"\n\n[Process exited with code {proc.returncode}]"

            if len(out) > 15_000:
                head_size = 6000
                tail_size = 3000
                out = (
                        out[:head_size]
                        + f"\n\n... truncated ({len(out)} chars total, showing first {head_size} and last {tail_size} chars) ...\n\n"
                        + out[-tail_size:]
                )

            return out.strip() or "(Command completed successfully, no output)"

        except Exception as e:
            return f"Error running command: {str(e)}"

    def _extract_and_update_cwd(self, stdout: str) -> tuple[str, Optional[str]]:
        if not stdout:
            return stdout, None

        # 修复第 5 点：将 \r?\n? 加入正则，完美吞掉整行，避免残留空行或误删正常空行
        pattern = re.compile(rf"^{_CWD_MARKER}:(?P<pwd>.*)\r?\n?", re.MULTILINE)
        match = pattern.search(stdout)

        extracted_cwd = None
        if match:
            extracted_pwd = match.group("pwd").strip()
            if os.path.isdir(extracted_pwd):
                extracted_cwd = extracted_pwd
            # 直接替换，不再使用后置的 strip()
            stdout = pattern.sub("", stdout)

        return stdout, extracted_cwd

    def _prepend_venv_activation(self, command: str, cwd: str) -> str:
        venv = self.venv_path
        if not venv:
            return command

        venv_path = Path(venv)
        if not venv_path.exists():
            return command

        python_keywords = ["python", "pip", "pytest", "django", "manage.py", "flask", "uvicorn", "gunicorn"]
        if not any(kw in command.lower() for kw in python_keywords):
            return command

        if os.name == "nt":
            activate_script = venv_path / "Scripts" / "Activate.ps1"
            if activate_script.exists():
                # 第 4 点修改：移除了 Set-ExecutionPolicy，因为我们已经在 Popen 参数里加了
                return f"& '{activate_script}'; {command}"
        else:
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
