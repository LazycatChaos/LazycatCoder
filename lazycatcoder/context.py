"""Multi-layer context compression.

Claude Code uses a 4-layer strategy:
  1. HISTORY_SNIP   - trim old tool outputs to a one-line summary
  2. Microcompact   - LLM-powered summary of old turns (cached)
  3. CONTEXT_COLLAPSE - aggressive compression when nearing hard limit
  4. Autocompact    - periodic background compaction

LazyCatCoder implements the same idea in 3 layers:
  Layer 1 (tool_snip)   - replace verbose tool results with truncated versions
  Layer 2 (summarize)   - LLM-powered summary of old conversation
  Layer 3 (hard_collapse) - last resort: drop everything except summary + recent
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLM


# ---------------------------------------------------------------------------
# Compatibility helpers — work with both AgentMessage and plain dict
# ---------------------------------------------------------------------------

def _msg_role(m) -> str:
    """Get message role as a string (handles both AgentMessage and dict)."""
    if hasattr(m, "role"):
        r = m.role
        return r.value if hasattr(r, "value") else str(r)
    return m.get("role", "")


def _msg_content(m) -> str:
    """Get message content as a string."""
    if hasattr(m, "content"):
        return m.content or ""
    return m.get("content", "") or ""


def _msg_tool_calls(m) -> list:
    """Get tool_calls list."""
    if hasattr(m, "tool_calls"):
        return m.tool_calls or []
    return m.get("tool_calls") or []


def _set_msg_content(m, value: str):
    """Set message content (handles both AgentMessage and dict)."""
    if hasattr(m, "content"):
        m.content = value
    else:
        m["content"] = value


def _link_chain(messages: list):
    """Re-link parent_id chain after compression rebuilds the list.

    After a compression layer clears and rebuilds ``messages``, the new
    messages have no parent_id links.  This helper walks the list and
    sets each message's parent_id to the previous message's id.

    The first message gets parent_id=None (it becomes the new root).
    """
    prev_id = None
    for m in messages:
        if hasattr(m, "parent_id"):
            m.parent_id = prev_id
            prev_id = m.id
        # plain dicts are not linked (old-format sessions)


# ---------------------------------------------------------------------------
# Tokenizer selection — pick the right tokenizer for the current model
# ---------------------------------------------------------------------------

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
                from pathlib import Path
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


def _approx_tokens(text: str, model: str | None = None) -> int:
    """Estimate token count using the best available tokenizer for the model."""
    return _tokenizer.count(text, model)


def estimate_tokens(messages: list, model: str | None = None) -> int:
    total = 0
    for m in messages:
        content = _msg_content(m)
        if content:
            total += _approx_tokens(content, model)
        tc = _msg_tool_calls(m)
        if tc:
            total += _approx_tokens(str(tc), model)
    return total


class ContextManager:
    def __init__(self, max_tokens: int = 128_000, model: str | None = None):
        self.max_tokens = max_tokens
        self.model = model  # Track model for accurate token counting
        # layer thresholds (fraction of max_tokens)
        self._snip_at = int(max_tokens * 0.50)    # 50% -> snip tool outputs
        self._summarize_at = int(max_tokens * 0.70)  # 70% -> LLM summarize
        self._collapse_at = int(max_tokens * 0.90)   # 90% -> hard collapse
        self._autocompact_at = int(max_tokens * 0.40)  # 40% -> proactive background compact
        self._last_autocompact_tokens = 0  # Track to avoid redundant compaction

    def token_usage(self, messages: list) -> int:
        """Return current estimated token count."""
        return estimate_tokens(messages, self.model)

    def should_autocompact(self, current_tokens: int) -> bool:
        """Decide whether to trigger background autocompact.

        Triggers when:
        1. Token usage exceeds 40% of max (enough history to summarize)
        2. At least 10k new tokens since last autocompact (avoid thrashing)
        3. At least 10 messages exist (enough content to compress)
        """
        if current_tokens < self._autocompact_at:
            return False
        if current_tokens - self._last_autocompact_tokens < 10_000:
            return False
        return True

    def maybe_compress(self, messages: list, llm: LLM | None = None) -> bool:
        """Apply compression layers as needed. Returns True if any compression happened."""
        current = estimate_tokens(messages, self.model)
        compressed = False

        # Layer 1: snip verbose tool outputs
        if current > self._snip_at:
            if self._snip_tool_outputs(messages):
                compressed = True
                current = estimate_tokens(messages, self.model)

        # Layer 2: LLM-powered summarization of old turns
        if current > self._summarize_at and len(messages) > 10:
            if self._summarize_old(messages, llm, keep_recent=8):
                compressed = True
                current = estimate_tokens(messages, self.model)

        # Layer 3: hard collapse - last resort
        if current > self._collapse_at and len(messages) > 4:
            self._hard_collapse(messages, llm)
            compressed = True

        return compressed

    def autocompact(self, messages: list, llm: LLM | None = None,
                    min_turns: int = 8, keep_recent: int = 15) -> bool:
        """Layer 4: Background periodic compaction.

        This mirrors Claude Code's Autocompact feature which runs silently
        in the background to keep context fresh without user awareness.

        Enhanced with Claude Code's Lazy GC pattern:
        - After compaction, immediately release pre-compaction messages for GC
        - This prevents memory leaks in long SDK sessions (no UI to preserve)
        
        Args:
            messages: The conversation history to compact
            llm: LLM instance for generating summaries
            min_turns: Minimum number of message turns before triggering
            keep_recent: Number of recent messages to preserve intact

        Returns:
            True if compaction was performed, False otherwise
        """
        # only trigger if we have enough history
        if len(messages) < min_turns:
            return False

        # skip if already compressed recently (check for summary marker)
        if any("[Context compressed" in _msg_content(m) for m in messages[:5]):
            return False

        # summarize the older portion, keep recent conversation intact
        old = messages[:-keep_recent]
        tail = messages[-keep_recent:]

        if len(old) < 10:  # not enough old content to summarize
            return False

        summary = self._get_summary(old, llm)

        # Clear and rebuild with summary + boundary + recent messages
        from .message import AgentMessage, MessageRole

        messages.clear()
        messages.append(AgentMessage(
            role=MessageRole.USER,
            content=f"[Auto-compacted context summary]\n{summary}",
            llm_visible=False,
        ))
        messages.append(AgentMessage(
            role=MessageRole.ASSISTANT,
            content="Context has been summarized. Ready to continue.",
            llm_visible=False,
        ))
        messages.extend(tail)
        _link_chain(messages)
        
        return True

    @staticmethod
    def _snip_tool_outputs(messages: list) -> bool:
        """Layer 1: Truncate tool results over 4000 chars to their first/last lines.

        Threshold raised from 1500 to 4000 to avoid snipping medium-sized
        source files (a ~100-line Python file is already ~3000 chars).
        """
        changed = False
        for m in messages:
            if _msg_role(m) != "tool":
                continue
            content = _msg_content(m)
            if len(content) <= 4000:
                continue
            lines = content.splitlines()
            if len(lines) <= 6:
                continue
            # keep first 3 + last 3 lines
            snipped = (
                "\n".join(lines[:3])
                + f"\n... ({len(lines)} lines, snipped to save context) ...\n"
                + "\n".join(lines[-3:])
            )
            _set_msg_content(m, snipped)
            changed = True
        return changed

    def _summarize_old(self, messages: list, llm: LLM | None,
                       keep_recent: int = 8) -> bool:
        """Layer 2: Summarize old conversation, keep recent messages intact.
        
        Enhanced with Lazy GC: after summarization, old messages are released.
        """
        if len(messages) <= keep_recent:
            return False

        old = messages[:-keep_recent]
        tail = messages[-keep_recent:]

        summary = self._get_summary(old, llm)

        # Claude Code Lazy GC pattern: clear and rebuild
        # This immediately releases pre-compaction messages for GC
        from .message import AgentMessage, MessageRole

        messages.clear()
        messages.append(AgentMessage(
            role=MessageRole.USER,
            content=f"[Context compressed - conversation summary]\n{summary}",
        ))
        messages.append(AgentMessage(
            role=MessageRole.ASSISTANT,
            content="Got it, I have the context from our earlier conversation.",
        ))
        messages.extend(tail)
        _link_chain(messages)
        return True

    def _hard_collapse(self, messages: list, llm: LLM | None):
        """Layer 3: Emergency compression. Keep only last 4 messages + summary.
        
        This is the last resort when context is near the hard limit.
        Uses Lazy GC to immediately release pre-collapse messages.
        """
        tail = messages[-4:] if len(messages) > 4 else messages[-2:]
        summary = self._get_summary(messages[:-len(tail)], llm)

        # Lazy GC: clear and rebuild with minimal content
        from .message import AgentMessage, MessageRole

        messages.clear()
        messages.append(AgentMessage(
            role=MessageRole.USER,
            content=f"[Hard context reset]\n{summary}",
        ))
        messages.append(AgentMessage(
            role=MessageRole.ASSISTANT,
            content="Context restored. Continuing from where we left off.",
        ))
        messages.extend(tail)
        _link_chain(messages)

    def _get_summary(self, messages: list, llm: LLM | None) -> str:
        """Generate summary via LLM or fallback to extraction."""
        flat = self._flatten(messages)
        file_ops = self._extract_file_operations(messages)

        if llm:
            try:
                resp = llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Compress this conversation into a brief summary. "
                                "Preserve: file paths edited, key decisions made, "
                                "errors encountered, current task state. "
                                "CRITICAL: If the conversation contains any todo_write tool calls, "
                                "you MUST include the latest todo list with each item's status "
                                "(pending/in_progress/completed). This is the only way to preserve "
                                "task progress across context compression. "
                                "CRITICAL: Include a 'File operations' section listing which files "
                                "were read, written, or edited (with the operation type). This "
                                "prevents re-reading files that were already examined. "
                                "Drop: verbose command output, code listings, "
                                "redundant back-and-forth."
                            ),
                        },
                        {"role": "user", "content": flat[:15000]},
                    ],
                )
                summary = resp.content
                # Ensure file operations are included even if LLM omitted them
                if file_ops and not any(
                    op in summary for op in ("read", "write", "edit", "File")
                ):
                    summary = f"{summary}\n\nFile operations:\n{file_ops}"
                return summary
            except Exception:
                pass

        # fallback: extract key info including todo state and file ops
        return self._extract_key_info(messages)

    @staticmethod
    def _flatten(messages: list) -> str:
        parts = []
        for m in messages:
            role = _msg_role(m)
            text = _msg_content(m)
            if text:
                # Tool results need more context for quality summaries
                limit = 2000 if role == "tool" else 400
                parts.append(f"[{role}] {text[:limit]}")
        return "\n".join(parts)

    @staticmethod
    def _extract_file_operations(messages: list) -> str:
        """Extract structured file operation history from messages.

        Scans assistant tool_calls for read_file, write_file, edit_file,
        delete_file, and glob calls, producing a concise summary like:
            read:  src/main.py, tests/test_core.py
            edit:  src/main.py
            write: docs/new.md
            delete: tmp/old.py

        This lets the LLM know which files it has already examined,
        avoiding redundant re-reads after context compression.
        """
        import json

        FILE_TOOLS = {"read_file", "write_file", "edit_file", "delete_file"}
        ops: dict[str, list[str]] = {}

        for m in messages:
            if _msg_role(m) != "assistant":
                continue
            tool_calls = _msg_tool_calls(m)
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                if name not in FILE_TOOLS:
                    continue
                args_raw = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except (json.JSONDecodeError, TypeError):
                    continue
                path = args.get("file_path") or ""
                if not path:
                    continue
                op = name.replace("_file", "")  # read_file -> read
                ops.setdefault(op, [])
                if path not in ops[op]:
                    ops[op].append(path)

        if not ops:
            return ""

        lines = []
        for op in ("read", "write", "edit", "delete"):
            paths = ops.get(op)
            if paths:
                lines.append(f"  {op}: {', '.join(paths)}")
        return "\n".join(lines)

    @staticmethod
    def _extract_key_info(messages: list) -> str:
        """Fallback: extract file paths, errors, decisions, and todo state without LLM."""
        import re
        files_seen = set()
        errors = []
        decisions = []
        todo_state = []

        for m in messages:
            text = _msg_content(m)
            # extract file paths
            for match in re.finditer(r'[\w./\-]+\.\w{1,5}', text):
                files_seen.add(match.group())
            # extract error lines
            for line in text.splitlines():
                if 'error' in line.lower() or 'Error' in line:
                    errors.append(line.strip()[:150])
            # extract todo state from todo_write tool results
            if _msg_role(m) == "tool" and "Todos updated" in text:
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith(("⏳", "🔄", "✅")) or ("[pending]" in line or "[in_progress]" in line or "[completed]" in line):
                        todo_state.append(line)

        parts = []
        if todo_state:
            parts.append("Current task list:\n" + "\n".join(todo_state))
        # Use structured file operations instead of broad regex
        file_ops = ContextManager._extract_file_operations(messages)
        if file_ops:
            parts.append(f"File operations:\n{file_ops}")
        elif files_seen:
            parts.append(f"Files touched: {', '.join(sorted(files_seen)[:20])}")
        if errors:
            parts.append(f"Errors seen: {'; '.join(errors[:5])}")
        return "\n".join(parts) or "(no extractable context)"
