"""Configuration - env vars and defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 16384          # 单次响应上限（~500行代码/3000行文档）
    max_context_tokens: int = 200_000  # 上下文窗口（留安全余量，配合 compression 使用）
    timeout: int = 120               # LLM 请求超时（秒）
    temperature: float = 0.0
    debug: bool = False
    venv_path: Optional[str] = None  # 虚拟环境路径（可选，设置后 bash 工具会自动激活）

    @classmethod
    def from_env(cls) -> "Config":
        # pick up common env vars automatically
        api_key = (
            os.getenv("NANOCODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        return cls(
            model=os.getenv("NANOCODER_MODEL", "gpt-4o"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("NANOCODER_BASE_URL"),
            max_tokens=int(os.getenv("NANOCODER_MAX_TOKENS", "16384")),
            timeout=int(os.getenv("NANOCODER_TIMEOUT", "120")),
            temperature=float(os.getenv("NANOCODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("NANOCODER_MAX_CONTEXT", "200000")),
            debug=os.getenv("NANOCODER_DEBUG", "").lower() in ("1", "true", "yes", "on"),
            venv_path=os.getenv("NANOCODER_VENV") or None,
        )

    def resolve_venv(self, workdir: Optional[str] = None) -> Optional[str]:
        """解析虚拟环境路径。

        优先级：
        1. 显式配置的 venv_path
        2. 工作目录下的 .venv / venv / env
        3. 当前目录下的 .venv / venv / env
        """
        if self.venv_path and Path(self.venv_path).exists():
            return str(Path(self.venv_path).resolve())

        # 自动检测
        search_dirs = []
        if workdir:
            search_dirs.append(Path(workdir))
        search_dirs.append(Path.cwd())

        for base in search_dirs:
            for name in (".venv", "venv", "env"):
                candidate = base / name
                if candidate.exists() and (candidate / "Scripts" if os.name == "nt" else candidate / "bin").exists():
                    return str(candidate.resolve())

        return None
