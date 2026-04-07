"""LLM provider layer - thin wrapper over OpenAI-compatible APIs.

Since most providers (DeepSeek, Qwen, Kimi, GLM, Ollama, etc.) expose an
OpenAI-compatible endpoint, we just use the openai SDK directly.  Switch
provider by changing OPENAI_BASE_URL + OPENAI_API_KEY. That's it.
"""

import json
import time
from dataclasses import dataclass, field

from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError


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
        timeout: int = 120,
        **kwargs,
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.extra = kwargs  # temperature, max_tokens, etc.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        debug: bool = False,
    ) -> LLMResponse:
        """Send messages, stream back response, handle tool calls."""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools

        # Log before sending request to avoid confusion during long waits
        print(f"📤 Sending request to {self.model}...")

        # Debug: log the actual messages being sent
        if debug:
            print(f"\n[DEBUG] === LLM Request ({len(messages)} messages) ===")
            for i, m in enumerate(messages):
                role = m.get("role", "?")
                content = m.get("content", "")
                if content:
                    preview = content[:300] + ("..." if len(content) > 300 else "")
                    print(f"  [{i}] {role}: {preview}")
                if m.get("tool_calls"):
                    print(f"  [{i}] {role}: tool_calls={m['tool_calls']}")
                if m.get("tool_call_id"):
                    print(f"  [{i}] tool_call_id={m['tool_call_id']}")
                if m.get("subtype"):
                    print(f"  [{i}] subtype={m['subtype']}")
            if tools:
                tool_names = [t["function"]["name"] for t in tools]
                print(f"  Tools available: {', '.join(tool_names)}")
            print(f"[DEBUG] === End Request ===\n")

        # stream_options is an OpenAI extension; not all providers support it
        try:
            params["stream_options"] = {"include_usage": True}
            stream = self._call_with_retry(params)
        except Exception:
            params.pop("stream_options", None)
            stream = self._call_with_retry(params)

        content_parts: list[str] = []
        tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        prompt_tok = 0
        completion_tok = 0

        for chunk in stream:
            # usage info comes in the final chunk
            if chunk.usage:
                prompt_tok = chunk.usage.prompt_tokens
                completion_tok = chunk.usage.completion_tokens

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

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

        # parse accumulated tool calls
        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                time.sleep(wait)
            except APIError as e:
                # 5xx = server error, retry; 4xx = client error, don't
                if e.status_code and e.status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
