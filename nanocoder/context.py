"""Multi-layer context compression.

Claude Code uses a 4-layer strategy:
  1. HISTORY_SNIP   - trim old tool outputs to a one-line summary
  2. Microcompact   - LLM-powered summary of old turns (cached)
  3. CONTEXT_COLLAPSE - aggressive compression when nearing hard limit
  4. Autocompact    - periodic background compaction

NanoCoder implements the same idea in 3 layers:
  Layer 1 (tool_snip)   - replace verbose tool results with truncated versions
  Layer 2 (summarize)   - LLM-powered summary of old conversation
  Layer 3 (hard_collapse) - last resort: drop everything except summary + recent
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLM


def _approx_tokens(text: str) -> int:
    """Rough token count. ~3.5 chars/token for mixed en/zh content."""
    return len(text) // 3


def estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        if m.get("content"):
            total += _approx_tokens(m["content"])
        if m.get("tool_calls"):
            total += _approx_tokens(str(m["tool_calls"]))
    return total


class ContextManager:
    def __init__(self, max_tokens: int = 128_000):
        self.max_tokens = max_tokens
        # layer thresholds (fraction of max_tokens)
        self._snip_at = int(max_tokens * 0.50)    # 50% -> snip tool outputs
        self._summarize_at = int(max_tokens * 0.70)  # 70% -> LLM summarize
        self._collapse_at = int(max_tokens * 0.90)   # 90% -> hard collapse

    def maybe_compress(self, messages: list[dict], llm: LLM | None = None) -> bool:
        """Apply compression layers as needed. Returns True if any compression happened."""
        current = estimate_tokens(messages)
        compressed = False

        # Layer 1: snip verbose tool outputs
        if current > self._snip_at:
            if self._snip_tool_outputs(messages):
                compressed = True
                current = estimate_tokens(messages)

        # Layer 2: LLM-powered summarization of old turns
        if current > self._summarize_at and len(messages) > 10:
            if self._summarize_old(messages, llm, keep_recent=8):
                compressed = True
                current = estimate_tokens(messages)

        # Layer 3: hard collapse - last resort
        if current > self._collapse_at and len(messages) > 4:
            self._hard_collapse(messages, llm)
            compressed = True

        return compressed

    def autocompact(self, messages: list[dict], llm: LLM | None = None,
                    min_turns: int = 20, keep_recent: int = 12) -> bool:
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
        if any("[Context compressed" in str(m.get("content", "")) for m in messages[:5]):
            return False

        # summarize the older portion, keep recent conversation intact
        old = messages[:-keep_recent]
        tail = messages[-keep_recent:]

        if len(old) < 10:  # not enough old content to summarize
            return False

        summary = self._get_summary(old, llm)

        # Build compact boundary marker (Claude Code style)
        compact_boundary = {
            "role": "system",
            "subtype": "compact_boundary",
            "content": f"[Auto-compacted context summary]\n{summary}",
            "compact_metadata": {
                "compressed_turns": len(old),
                "preserved_segment": {
                    "head_uuid": tail[0].get("uuid") if tail else None,
                    "tail_uuid": tail[-1].get("uuid") if tail else None,
                } if tail else {},
            }
        }
        
        # Clear and rebuild with summary + boundary + recent messages
        messages.clear()
        messages.append({
            "role": "user",
            "content": f"[Auto-compacted context summary]\n{summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "Context has been summarized. Ready to continue.",
        })
        messages.extend(tail)
        
        # Claude Code Lazy GC: immediately release pre-compaction messages
        # The boundary was just pushed, so it's the last element.
        # Only post-boundary messages are needed going forward.
        # This prevents memory leaks in long sessions.
        mutable_boundary_idx = len(messages) - 1
        if mutable_boundary_idx > 0:
            # Note: We already cleared with messages.clear(), 
            # but this pattern is kept for consistency with Claude Code
            pass  # GC already happened via clear()
        
        return True

    @staticmethod
    def _snip_tool_outputs(messages: list[dict]) -> bool:
        """Layer 1: Truncate tool results over 1500 chars to their first/last lines.

        This mirrors Claude Code's HISTORY_SNIP which replaces old tool outputs
        with a one-line summary to reclaim context space.
        """
        changed = False
        for m in messages:
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
            if len(content) <= 1500:
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
            m["content"] = snipped
            changed = True
        return changed

    def _summarize_old(self, messages: list[dict], llm: LLM | None,
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
        messages.clear()
        messages.append({
            "role": "user",
            "content": f"[Context compressed - conversation summary]\n{summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "Got it, I have the context from our earlier conversation.",
        })
        messages.extend(tail)
        return True

    def _hard_collapse(self, messages: list[dict], llm: LLM | None):
        """Layer 3: Emergency compression. Keep only last 4 messages + summary.
        
        This is the last resort when context is near the hard limit.
        Uses Lazy GC to immediately release pre-collapse messages.
        """
        tail = messages[-4:] if len(messages) > 4 else messages[-2:]
        summary = self._get_summary(messages[:-len(tail)], llm)

        # Lazy GC: clear and rebuild with minimal content
        messages.clear()
        messages.append({
            "role": "user",
            "content": f"[Hard context reset]\n{summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "Context restored. Continuing from where we left off.",
        })
        messages.extend(tail)

    def _get_summary(self, messages: list[dict], llm: LLM | None) -> str:
        """Generate summary via LLM or fallback to extraction."""
        flat = self._flatten(messages)

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
                                "Drop: verbose command output, code listings, "
                                "redundant back-and-forth."
                            ),
                        },
                        {"role": "user", "content": flat[:15000]},
                    ],
                )
                return resp.content
            except Exception:
                pass

        # fallback: extract key lines
        return self._extract_key_info(messages)

    @staticmethod
    def _flatten(messages: list[dict]) -> str:
        parts = []
        for m in messages:
            role = m.get("role", "?")
            text = m.get("content", "") or ""
            if text:
                parts.append(f"[{role}] {text[:400]}")
        return "\n".join(parts)

    @staticmethod
    def _extract_key_info(messages: list[dict]) -> str:
        """Fallback: extract file paths, errors, and decisions without LLM."""
        import re
        files_seen = set()
        errors = []
        decisions = []

        for m in messages:
            text = m.get("content", "") or ""
            # extract file paths
            for match in re.finditer(r'[\w./\-]+\.\w{1,5}', text):
                files_seen.add(match.group())
            # extract error lines
            for line in text.splitlines():
                if 'error' in line.lower() or 'Error' in line:
                    errors.append(line.strip()[:150])

        parts = []
        if files_seen:
            parts.append(f"Files touched: {', '.join(sorted(files_seen)[:20])}")
        if errors:
            parts.append(f"Errors seen: {'; '.join(errors[:5])}")
        return "\n".join(parts) or "(no extractable context)"
