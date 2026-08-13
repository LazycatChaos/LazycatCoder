"""Token estimation utilities — shared by context compression and session persistence.

Centralizes the model-aware tokenizer selection so that ``context.py``
(compression decisions) and ``session.py`` (persistence estimates) share one
implementation instead of each maintaining their own rough heuristic.

Design:
- ``_TokenizerProxy`` lazily picks the best backend per model:
  qwen -> local CustomTokenizer, OpenAI -> tiktoken, else a mixed-language
  heuristic fallback.
- ``get_token_estimator(model)`` returns a cached ``Callable[[str], int]``
  that counts tokens for a single piece of text.  Instances are cached per
  model so high-frequency callers (context compression) don't re-init the
  tokenizer on every call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


class _TokenizerProxy:
    """Lazy tokenizer wrapper that picks the right backend per model."""

    def __init__(self):
        self._qwen_tokenizer = None
        self._tiktoken_enc = None
        self._last_model: str | None = None
        self._active: str | None = None  # 'qwen' | 'tiktoken' | 'fallback'

    def _ensure_qwen(self):
        if self._qwen_tokenizer is None:
            try:
                from .tokenize import CustomTokenizer
                vocab_path = Path(__file__).parent / "tokenize" / "tiktoken_file"
                self._qwen_tokenizer = CustomTokenizer(str(vocab_path))
            except Exception:
                pass  # Qwen tokenizer unavailable, will fallback

    def _ensure_tiktoken(self, model: str):
        """Get the right tiktoken encoding for the model."""
        if self._tiktoken_enc is None:
            try:
                import tiktoken
                # Map model names to tiktoken encodings
                if "gpt-4" in model or "gpt-3.5" in model:
                    enc_name = "cl100k_base"
                elif "o1" in model or "o3" in model:
                    enc_name = "o200k_base"
                else:
                    enc_name = "cl100k_base"  # default for OpenAI models
                self._tiktoken_enc = tiktoken.get_encoding(enc_name)
            except Exception:
                pass  # tiktoken unavailable

    def count(self, text: str, model: str | None = None) -> int:
        """Count tokens using the best available tokenizer for the model."""
        # Detect if model changed
        if model != self._last_model:
            self._last_model = model
            self._active = None  # force re-detection

        if self._active is None:
            if model and ("qwen" in model.lower() or "qwq" in model.lower()):
                self._ensure_qwen()
                if self._qwen_tokenizer is not None:
                    self._active = "qwen"
            if self._active is None and model:
                self._ensure_tiktoken(model)
                if self._tiktoken_enc is not None:
                    self._active = "tiktoken"
            if self._active is None:
                self._active = "fallback"

        if self._active == "qwen":
            return len(self._qwen_tokenizer.encode(text))
        elif self._active == "tiktoken":
            return len(self._tiktoken_enc.encode(text))
        else:
            # Fallback: mixed-language heuristic
            # - CJK chars ≈ 1 token each
            # - Latin/ASCII ≈ 3.5 chars/token
            cjk = sum(1 for c in text if ord(c) > 0x2E80)
            ascii_len = len(text) - cjk
            return cjk + ascii_len // 3


_tokenizer = _TokenizerProxy()

# Cache of per-model estimator callables.  Keying by model avoids re-initializing
# the underlying tokenizer for high-frequency call sites.
_estimator_cache: dict[str, Callable[[str], int]] = {}


def _approx_tokens(text: str, model: str | None = None) -> int:
    """Estimate token count using the best available tokenizer for the model."""
    return _tokenizer.count(text, model)


def get_token_estimator(model: str = "default") -> Callable[[str], int]:
    """Return a cached ``Callable[[str], int]`` counting tokens for *model*.

    The returned callable estimates tokens for a single text string using the
    most accurate available tokenizer for the given model (qwen -> local
    CustomTokenizer, OpenAI -> tiktoken, otherwise a mixed-language heuristic).

    Instances are cached per model so repeated calls are cheap.
    """
    if model not in _estimator_cache:
        _estimator_cache[model] = lambda text: _approx_tokens(text, model)
    return _estimator_cache[model]