"""LLM provider layer - thin wrapper over OpenAI-compatible APIs.

Since most providers (DeepSeek, Qwen, Kimi, GLM, Ollama, etc.) expose an
OpenAI-compatible endpoint, we just use the openai SDK directly.  Switch
provider by changing OPENAI_BASE_URL + OPENAI_API_KEY. That's it.
"""

import json
import time
import logging
import httpx
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError

logger = logging.getLogger(__name__)


class StreamTimeoutError(Exception):
    """Hard total-time limit for stream consumption."""
    pass


# Known model prefixes that do NOT support the `temperature` parameter.
# New Claude 4+ models reject temperature with a 400 error.
NO_TEMPERATURE_MODELS = ("claude-4", "claude-sonnet-4", "claude-opus-4", 'claude-fable-5')
NO_THINKING_MODELS = ("gpt-",)


# --- Reasoning effort rules (full model-name matching) ---
# Each rule: (exact model names, {default, valid, map, force})
#   - default: effort used when user passes "on" without a specific level
#   - valid:   effort values the API actually accepts (empty set = fallback,
#              only enable_thinking is sent)
#   - map:     user input → resolved effort ("__DISABLE__" = turn off thinking)
#   - force:   if set, all non-disable inputs are forced to this value
#              (e.g. kimi/kimi-k3 only supports "max")
_REASONING_RULES: list[tuple[list[str], dict]] = [
    # qwen3.8-max 系列: default=xhigh, valid={low,medium,xhigh}
    (
        ["qwen3.8-max", "qwen3.8-plus"],
        {
            "default": "xhigh",
            "valid": {"low", "medium", "xhigh"},
            "map": {"max": "xhigh", "high": "xhigh", "minimal": "low", "none": "__DISABLE__"},
            "force": None,
        },
    ),
    # DeepSeek-V4 / GLM-5+ 系列: default=high, valid={high,max}
    (
        ["deepseek-v4-pro", "deepseek-v4-flash",
         "glm-5", "glm-5.1", "glm-5.2"],
        {
            "default": "high",
            "valid": {"high", "max"},
            "map": {"low": "high", "medium": "high", "xhigh": "max", "none": "__DISABLE__"},
            "force": None,
        },
    ),
    # kimi/kimi-k3 (月之暗面直供): 仅支持 max
    (
        ["kimi/kimi-k3"],
        {
            "default": "max",
            "valid": {"max"},
            "map": {"none": "__DISABLE__"},
            "force": "max",
        },
    ),
]

_FALLBACK_RULE = {"default": None, "valid": set(), "map": {"none": "__DISABLE__"}, "force": None}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parse_errors: list[dict] = field(default_factory=list)
    reasoning: str = ""

    @property
    def message(self) -> dict:
        """Convert to OpenAI message format for appending to history."""
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


class LLM:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: int = 300,
        reasoning_effort: Optional[str] = None,
        **kwargs,
    ):
        self.model = model
        self.timeout = timeout  # total stream timeout (seconds)
        # Fine-grained httpx timeout: connect=10s (fast fail), read=60s (chunk interval),
        # write=10s, pool=10s.  Since provider streams thinking chunks, read=60 is safe.
        self._http_timeout = httpx.Timeout(
            connect=10.0,
            read=60.0,
            write=10.0,
            pool=10.0,
        )
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self._http_timeout,
            max_retries=0,  # SDK retries disabled; we control retries below
        )
        self.extra = kwargs  # temperature, max_tokens, etc.
        self.reasoning_effort = reasoning_effort
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        on_reasoning=None,
        on_reasoning_end=None,
        debug: bool = False,
        purpose: str = "",
    ) -> LLMResponse:
        """Send messages, stream back response, handle tool calls.

        Args:
            purpose: Optional label for the log prefix (e.g. "compress").
                     When set, the "Sending request" log is prefixed with
                     [purpose] so users can distinguish compression calls
                     from main agent-loop calls.
        """
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self._strip_unsupported_params(self.extra),
        }
        if self.reasoning_effort is not None:
            extra_body = self._build_reasoning_params()
            if extra_body:
                params['extra_body'] = extra_body
        else:
            # Explicitly disable thinking — many models (DeepSeek-R1, GLM, etc.)
            # enable thinking by default, so omitting the flag won't turn it off.
            params['extra_body'] = {"enable_thinking": False}
        if tools:
            params["tools"] = tools

        # Log before sending request to avoid confusion during long waits
        prefix = f"[{purpose}] " if purpose else ""
        print(f"{prefix}Sending request to {self.model}...")

        # Phase 1: Create stream (retriable)
        raw_stream = self._create_stream_with_retry(params)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        prompt_tok = 0
        completion_tok = 0

        # Phase 2: Consume stream (NOT retriable - on_token may have partial output)
        try:
            for chunk in self._timeout_stream(raw_stream, self.timeout):
                # usage info comes in the final chunk
                if chunk.usage:
                    prompt_tok = chunk.usage.prompt_tokens
                    completion_tok = chunk.usage.completion_tokens

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 思考内容 — 读取但不进入 content
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                    if on_reasoning:
                        on_reasoning(reasoning)

                # accumulate text
                if delta.content:
                    content_parts.append(delta.content)
                    if on_token:
                        on_token(delta.content)

                # accumulate tool calls across chunks
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tc_map:
                            tc_map[idx] = {"id": "", "name": "", "args": ""}
                        if tc_delta.id:
                            tc_map[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tc_map[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tc_map[idx]["args"] += tc_delta.function.arguments
        finally:
            # Ensure underlying HTTP connection is closed on error/timeout
            if hasattr(raw_stream, "close"):
                raw_stream.close()
            # 无条件调用 on_reasoning_end — 闭包内部用 state["started"] 做 guard
            # 即使流式中途异常，也能正确关闭 reasoning 显示
            if on_reasoning_end:
                on_reasoning_end()

        # parse accumulated tool calls
        parsed: list[ToolCall] = []
        parse_errors: list[dict] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError) as e:
                args = {}
                parse_errors.append({
                    "tool": raw.get("name", "?"),
                    "raw_args": raw.get("args", "")[:200],
                    "error": str(e),
                })
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            parse_errors=parse_errors,
            reasoning="".join(reasoning_parts),
        )

    def _get_reasoning_rule(self) -> dict:
        """Find the reasoning rule for the current model (exact name match)."""
        for names, rule in _REASONING_RULES:
            if self.model in names:
                return rule
        return _FALLBACK_RULE

    def _build_reasoning_params(self) -> dict | None:
        """Build extra_body for reasoning_effort based on model rules.

        - None effort or "none" → {"enable_thinking": False}
        - Fallback model (no reasoning_effort support) → {"enable_thinking": True}
        - Known model → {"enable_thinking": True, "reasoning_effort": resolved}
        """
        rule = self._get_reasoning_rule()
        effort = self.reasoning_effort

        # 关闭：None 或 "none"
        if effort is None or effort.lower() == "none":
            return {"enable_thinking": False}

        effort_lower = effort.lower()

        # 查映射表
        resolved = rule["map"].get(effort_lower, effort_lower)

        if resolved == "__DISABLE__":
            return {"enable_thinking": False}

        # force 模型（如 kimi/kimi-k3 仅支持 max）：所有非关闭输入强制映射
        if rule.get("force"):
            resolved = rule["force"]
        elif rule["valid"] and resolved not in rule["valid"]:
            # 不在合法值范围内，回退到默认
            logger.warning(
                f"effort '{effort}' not supported by {self.model}, "
                f"using default '{rule['default']}'"
            )
            resolved = rule["default"]

        # 兜底模型（不支持 reasoning_effort）→ 只发 enable_thinking
        if not rule["valid"]:
            return {"enable_thinking": True}

        return {"enable_thinking": True, "reasoning_effort": resolved}

    def _strip_unsupported_params(self, params: dict) -> dict:
        """Filter out parameters not supported by the current model."""
        params = dict(params)  # don't mutate original
        if any(self.model.startswith(p) for p in NO_TEMPERATURE_MODELS):
            params.pop("temperature", None)
        if any(self.model.startswith(p) for p in NO_THINKING_MODELS):
            params.pop("extra_body", None)
        return params

    def _timeout_stream(self, stream, timeout: int):
        """Wrap a streaming response with a total timeout.

        OpenAI SDK's timeout only controls per-chunk reads. If the server
        keeps the connection open but sends no data, the stream can hang
        indefinitely. This generator enforces a hard total time limit.
        """
        start = time.monotonic()
        for chunk in stream:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise StreamTimeoutError(
                    f"Total stream timeout exceeded: {timeout}s "
                    f"(elapsed {elapsed:.1f}s)"
                )
            yield chunk

    def _create_stream_with_retry(self, params: dict, max_attempts: int = 3):
        """Create a streaming completion with retry on transient errors.

        This only covers Phase 1 (stream creation).  Once the stream is
        created and we start consuming chunks, retries are NOT safe because
        on_token may have already received partial output.
        """
        # Try with stream_options first; fall back only on 400 (unsupported)
        params_with_usage = {**params, "stream_options": {"include_usage": True}}
        try:
            return self._do_create_stream(params_with_usage, max_attempts)
        except APIError as e:
            if e.status_code == 400:
                print("  [LLM] stream_options unsupported, retrying without it...")
                return self._do_create_stream(params, max_attempts)
            raise

    def _do_create_stream(self, params: dict, max_attempts: int):
        """Internal: actually call the API with retry logic."""
        for attempt in range(max_attempts):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt == max_attempts - 1:
                    raise
                wait = 2 ** attempt
                print(f"  [LLM] {type(e).__name__}, retry in {wait}s...")
                time.sleep(wait)
            except APIError as e:
                # 400 about temperature: strip it and retry once (catches new models
                # not yet in NO_TEMPERATURE_MODELS)
                if (
                    e.status_code == 400
                    and attempt == 0
                    and "temperature" in str(e.message).lower()
                ):
                    print("  [LLM] temperature deprecated for this model, retrying without it...")
                    params.pop("temperature", None)
                    continue
                # 400 about enable_thinking — model doesn't support it, strip and retry
                if (
                    e.status_code == 400
                    and attempt == 0
                    and "enable_thinking" in str(e.message).lower()
                ):
                    print("  [LLM] enable_thinking unsupported by this model, retrying without it...")
                    params.pop("extra_body", None)
                    continue
                # 400 about reasoning_effort — strip it, keep enable_thinking only
                if (
                    e.status_code == 400
                    and attempt == 0
                    and "reasoning_effort" in str(e.message).lower()
                ):
                    print("  [LLM] reasoning_effort unsupported by this model, retrying with enable_thinking only...")
                    if "extra_body" in params:
                        params["extra_body"] = {"enable_thinking": True}
                    continue
                # 5xx = server error, retry; 4xx = client error, don't
                if e.status_code and e.status_code >= 500 and attempt < max_attempts - 1:
                    wait = 2 ** attempt
                    print(f"  [LLM] Server {e.status_code}, retry in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
