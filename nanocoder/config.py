"""Configuration - env vars and defaults."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    debug: bool = False

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
            max_tokens=int(os.getenv("NANOCODER_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("NANOCODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("NANOCODER_MAX_CONTEXT", "128000")),
            debug=os.getenv("NANOCODER_DEBUG", "").lower() in ("1", "true", "yes", "on"),
        )
