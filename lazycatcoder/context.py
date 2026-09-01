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

import time
from typing import TYPE_CHECKING

from .message import AgentMessage

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


def _set_msg_metadata(m, key: str, value):
    """Set a metadata field (handles both AgentMessage and dict)."""
    if hasattr(m, "metadata"):
        m.metadata[key] = value
    else:
        meta = m.setdefault("metadata", {})
        meta[key] = value


def _msg_metadata(m) -> dict:
    """Get the metadata dict (handles both AgentMessage and dict)."""
    if hasattr(m, "metadata"):
        return m.metadata
    return m.setdefault("metadata", {})


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
# Token estimation — delegated to the shared token_utils module so that
# context compression and session persistence share one implementation.
# ---------------------------------------------------------------------------

from .token_utils import _approx_tokens


# Soft-delete archive limits.  Old messages evicted from the active list are
# archived here (in-memory) instead of being destroyed.  Eviction is FIFO by
# compression batch — a batch is a semantic unit (all messages compressed into
# one summary), so it must be dropped whole.
ARCHIVE_MAX_ITEMS = 500        # hard cap on message count (防极端碎消息)
ARCHIVE_MAX_TOKENS = 256_000   # soft cap on archived tokens (~2x 128k window)


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

        # Hybrid token estimation: cache the last exact prompt_tokens and
        # completion_tokens returned by the LLM API along with the message
        # count at that point.  This lets token_usage() compute
        #   exact_base + exact_assistant + estimated_delta
        # instead of re-estimating the entire conversation every time.
        #
        # prompt_tokens     = exact token count of all messages sent to API
        # completion_tokens = exact token count of model output (content +
        #   tool_calls + reasoning).  Since reasoning is NOT sent back to the
        #   API in subsequent rounds, we subtract estimated reasoning tokens
        #   to get the assistant message's real context footprint.
        self._last_exact_prompt_tokens: int = 0
        self._last_exact_msg_count: int = 0
        self._last_exact_assistant_tokens: int = 0

        # Soft-delete side-store.  Messages compressed out of the active list
        # are archived here (same AgentMessage type as the active list) rather
        # than destroyed, so history stays recoverable within the process.
        self._archived: list[AgentMessage] = []
        self._archive_seq: int = 0
        self._archived_tokens: int = 0
        self._archive_batch_tokens: dict[int, int] = {}  # batch_id -> token count

    def update_exact_tokens(
        self,
        prompt_tokens: int,
        msg_count: int,
        completion_tokens: int = 0,
        reasoning: str = "",
    ) -> None:
        """Cache exact token counts from the LLM API response.

        Called after every LLM round.  ``msg_count`` is the number of messages
        that were in the conversation *when the API call was made* (i.e. before
        the assistant response was appended).

        prompt_tokens is the exact input token count — it becomes the base
        for hybrid estimation.

        completion_tokens includes content + tool_calls + reasoning, but
        reasoning is NOT sent back to the API.  We subtract estimated reasoning
        tokens to get the assistant message's real context footprint, which
        is more accurate than re-estimating the assistant message from scratch.
        """
        if prompt_tokens > 0 and msg_count >= 0:
            self._last_exact_prompt_tokens = prompt_tokens
            self._last_exact_msg_count = msg_count
            if completion_tokens > 0:
                reasoning_tokens = _approx_tokens(reasoning, self.model) if reasoning else 0
                # Clamp to 0 — defensive against APIs that report
                # completion_tokens < reasoning estimate (rare, but possible
                # if reasoning was truncated or the estimate is off).
                assistant_tokens = max(0, completion_tokens - reasoning_tokens)
                self._last_exact_assistant_tokens = assistant_tokens
            else:
                self._last_exact_assistant_tokens = 0

    def _invalidate_exact_tokens(self) -> None:
        """Invalidate the cached exact token count.

        Called after compression layers rebuild the message list (snip,
        summarize, collapse) because the exact base no longer corresponds
        to the current messages.
        """
        self._last_exact_prompt_tokens = 0
        self._last_exact_msg_count = 0
        self._last_exact_assistant_tokens = 0

    def token_usage(self, messages: list) -> int:
        """Return current token count using hybrid estimation.

        If we have cached exact values from the last API call:
          exact_prompt_tokens  (base for all messages at API call time)
        + exact_assistant_tokens  (completion_tokens - reasoning, for the
          assistant message that was generated but not yet in messages at
          API call time)
        + estimated_delta  (for any messages added after the assistant message,
          e.g. tool results, next user message)

        Falls back to full estimation if no exact base is available.
        """
        n = len(messages)
        if self._last_exact_prompt_tokens > 0 and n >= self._last_exact_msg_count:
            delta_msgs = messages[self._last_exact_msg_count:]
            if not delta_msgs:
                # No new messages — just the exact prompt_tokens.
                # If assistant_tokens is set, the assistant message has been
                # appended (it wasn't counted in prompt_tokens).
                return self._last_exact_prompt_tokens + self._last_exact_assistant_tokens

            # Check if the first delta message is the assistant message we
            # have exact completion_tokens for.  If so, use the exact value
            # instead of estimating it.
            first = delta_msgs[0]
            first_role = _msg_role(first)
            exact_part = 0
            remaining = delta_msgs

            if (
                self._last_exact_assistant_tokens > 0
                and first_role == "assistant"
            ):
                # The assistant message is the first new message.  Use exact
                # token count for it, estimate the rest.
                exact_part = self._last_exact_assistant_tokens
                remaining = delta_msgs[1:]

            estimated = estimate_tokens(remaining, self.model)
            return self._last_exact_prompt_tokens + exact_part + estimated
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
        current = self.token_usage(messages)
        compressed = False

        # Layer 1: snip verbose tool outputs
        if current > self._snip_at:
            if self._snip_tool_outputs(messages):
                compressed = True
                # Snipping modifies messages in-place; exact base is stale.
                self._invalidate_exact_tokens()
                current = self.token_usage(messages)

        # Layer 2: LLM-powered summarization of old turns
        if current > self._summarize_at and len(messages) > 10:
            if self._summarize_old(messages, llm, keep_recent=8):
                compressed = True
                self._invalidate_exact_tokens()
                current = self.token_usage(messages)

        # Layer 3: hard collapse - last resort
        if current > self._collapse_at and len(messages) > 4:
            self._hard_collapse(messages, llm)
            self._invalidate_exact_tokens()
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

        # Soft-delete: archive the old portion, then rebuild active list with
        # summary + boundary + recent messages.  Old messages are preserved in
        # the side-store (in-memory) instead of being destroyed.
        from .message import AgentMessage, MessageRole

        batch_id = self._archive_messages(old, reason="layer4_autocompact")
        summary_msg = AgentMessage(
            role=MessageRole.USER,
            content=f"[Auto-compacted context summary]\n{summary}",
            llm_visible=False,
        )
        summary_msg.metadata["compressed_batches"] = [batch_id]
        first_id = old[0].id if hasattr(old[0], "id") else old[0].get("id")
        last_id = old[-1].id if hasattr(old[-1], "id") else old[-1].get("id")
        if first_id is not None and last_id is not None:
            summary_msg.metadata["compressed_range"] = (first_id, last_id)
        ack_msg = AgentMessage(
            role=MessageRole.ASSISTANT,
            content="Context has been summarized. Ready to continue.",
            llm_visible=False,
        )
        messages[:] = [summary_msg, ack_msg] + tail
        _link_chain(messages)
        self._invalidate_exact_tokens()
        
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
            _set_msg_metadata(m, "snipped", True)
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

        # Soft-delete: archive the old portion, then rebuild active list with
        # summary + ack + recent messages.  Old messages are preserved in the
        # in-memory side-store instead of being destroyed.
        from .message import AgentMessage, MessageRole

        batch_id = self._archive_messages(old, reason="layer2_compress")
        summary_msg = AgentMessage(
            role=MessageRole.USER,
            content=f"[Context compressed - conversation summary]\n{summary}",
        )
        summary_msg.metadata["compressed_batches"] = [batch_id]
        # Range may be unavailable for plain dicts (no id field); keep it best-effort.
        first_id = old[0].id if hasattr(old[0], "id") else old[0].get("id")
        last_id = old[-1].id if hasattr(old[-1], "id") else old[-1].get("id")
        if first_id is not None and last_id is not None:
            summary_msg.metadata["compressed_range"] = (first_id, last_id)
        ack_msg = AgentMessage(
            role=MessageRole.ASSISTANT,
            content="Got it, I have the context from our earlier conversation.",
        )
        messages[:] = [summary_msg, ack_msg] + tail
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
# ------------------------------------------------------------------
    # Soft-delete archive (side-store for compressed-out messages)
    # ------------------------------------------------------------------

    def _archive_messages(self, old: list, reason: str) -> int:
        """Move ``old`` messages into the in-memory archive, marked non-visible.

        All messages in a single compression get the same ``archive_batch_id``
        so they can be evicted (and later retrieved) as one semantic unit.
        ``llm_visible`` is forced to False; metadata is appended, never
        overwriting any pre-existing fields.

        Returns the new batch id.
        """
        if not old:
            return -1

        self._archive_seq += 1
        batch_id = self._archive_seq
        now = time.time()
        batch_tokens = 0

        for m in old:
            # Force non-visible (dicts and AgentMessage alike).
            if hasattr(m, "llm_visible"):
                m.llm_visible = False
            else:
                m["llm_visible"] = False
            _set_msg_metadata(m, "archived", True)
            if "archived_reason" not in _msg_metadata(m):
                _set_msg_metadata(m, "archived_reason", reason)
            _msg_metadata(m).setdefault("archived_at", now)
            _set_msg_metadata(m, "archive_batch_id", batch_id)
            content = _msg_content(m)
            if content:
                batch_tokens += _approx_tokens(content, self.model)

        self._archived.extend(old)
        self._archived_tokens += batch_tokens
        self._archive_batch_tokens[batch_id] = batch_tokens
        self._gc_archived()
        return batch_id

    def _gc_archived(self) -> None:
        """Evict archived batches FIFO (oldest first) when over the caps.

        Eviction is whole-batch: a batch is a semantic unit (all messages
        compressed into one summary), so dropping it partially would leave
        orphaned messages that no summary references.
        """
        while self._archived and (
            len(self._archived) > ARCHIVE_MAX_ITEMS
            or self._archived_tokens > ARCHIVE_MAX_TOKENS
        ):
            oldest_batch = _msg_metadata(self._archived[0]).get("archive_batch_id")
            if oldest_batch is None:
                # Defensive: a message without a batch id can't be grouped;
                # drop it alone.
                m = self._archived.pop(0)
                self._archived_tokens -= _approx_tokens(_msg_content(m), self.model)
                continue
            freed = self._archive_batch_tokens.pop(oldest_batch, 0)
            self._archived_tokens -= freed
            self._archived = [
                m for m in self._archived
                if _msg_metadata(m).get("archive_batch_id") != oldest_batch
            ]

    def get_archived(self, *, batch_id: int | None = None) -> list:
        """Return archived messages as a read-only copy, optionally by batch.

        Returns a shallow copy of the list so callers cannot mutate the
        archive through the returned reference.
        """
        if batch_id is not None:
            return [
                m for m in self._archived
                if _msg_metadata(m).get("archive_batch_id") == batch_id
            ]
        return list(self._archived)

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
                    purpose="compress",
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
