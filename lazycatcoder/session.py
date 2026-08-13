"""Session persistence - save and resume conversations.

Claude Code maintains session state via QueryEngine (1295 lines).
LazyCatCoder distills this to: JSON dump of messages + model config + summary.

Enhanced features:
- Auto-save on every message (with lazy flush)
- Session summary generation for quick preview
- Token usage tracking per session
- Session metadata (start time, last activity, file touches)
"""

import json
import os
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

SESSIONS_DIR = Path.home() / ".lazycatcoder" / "sessions"

# Lazy flush timer (seconds) - batch writes within this window
LAZY_FLUSH_INTERVAL = 2.0

# Thread pool for async file writes
_write_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="session_writer")


def _clean_surrogates(obj):
    """Recursively remove surrogate characters (U+D800..U+DFFF) from strings.

    Surrogates can sneak in when reading binary/non-UTF8 files — they are
    valid Python str internals but break JSON UTF-8 encoding and cause
    ``'utf-8' codec can't encode characters`` errors on every subsequent
    session save/load until the corrupted session file is deleted.
    """
    if isinstance(obj, str):
        return "".join(c for c in obj if not (0xD800 <= ord(c) <= 0xDFFF))
    if isinstance(obj, dict):
        return {k: _clean_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_surrogates(item) for item in obj]
    return obj


def _safe_read_json(path: Path) -> Optional[dict]:
    """Read a JSON file, tolerating surrogate-encoded corruption."""
    try:
        # surrogatepass lets us read files that contain raw surrogate bytes
        text = path.read_text(encoding="utf-8", errors="surrogatepass")
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, FileNotFoundError):
        return None


class SessionMetadata:
    """Metadata for a session, used for quick preview and search."""
    
    def __init__(self, session_id: str, data: Optional[dict] = None):
        self.id = session_id
        self.model = data.get("model", "?") if data else "?"
        self.created_at = data.get("created_at", "") if data else ""
        self.saved_at = data.get("saved_at", "") if data else ""
        self.working_dir = data.get("working_dir", "") if data else ""
        self.message_count = data.get("message_count", 0) if data else 0
        self.token_estimate = data.get("token_estimate", 0) if data else 0
        self.summary = data.get("summary", "") if data else ""
        self.files_touched = data.get("files_touched", []) if data else []
        self.user_message_preview = data.get("user_message_preview", "") if data else ""
        self.system_errors = data.get("system_errors", []) if data else []
        self.usage = data.get("usage", {}) if data else {}
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "model": self.model,
            "created_at": self.created_at,
            "saved_at": self.saved_at,
            "working_dir": self.working_dir,
            "message_count": self.message_count,
            "token_estimate": self.token_estimate,
            "summary": self.summary,
            "files_touched": self.files_touched,
            "user_message_preview": self.user_message_preview,
            "system_errors": self.system_errors,
            "usage": self.usage,
        }
    
    def quick_preview(self) -> str:
        """Generate a one-line preview for session listing."""
        if self.summary:
            return self.summary[:100] + "..." if len(self.summary) > 100 else self.summary
        if self.user_message_preview:
            return f"User: {self.user_message_preview[:80]}..."
        file_names = self._file_names()
        return f"{self.message_count} messages, {file_names[:3]}"
    
    def detailed_preview(self) -> str:
        """Generate a detailed preview for session inspection."""
        lines = [
            f"📋 Session: {self.id}",
            f"🤖 Model: {self.model}",
            f"📅 Created: {self.created_at}",
            f"💾 Last saved: {self.saved_at}",
            f"💬 Messages: {self.message_count}",
            f"📊 Tokens: ~{self.token_estimate:,}",
        ]
        if self.files_touched:
            file_strs = self._file_display_list()
            lines.append(f"📁 Files: {', '.join(file_strs[:10])}" +
                        ("..." if len(file_strs) > 10 else ""))
        if self.summary:
            lines.append(f"\n📝 Summary:\n{self.summary}")
        if self.system_errors:
            error_types = [e.get("error_type", "unknown") for e in self.system_errors]
            if "api_error" in error_types:
                lines.append(f"\n🔌 Status: Interrupted by API/Network ({error_types.count('api_error')} times)")
            if "tool_crash" in error_types:
                crashed_tools = set(e.get("tool", "?") for e in self.system_errors if e.get("error_type") == "tool_crash")
                lines.append(f"💥 Status: Tool failures ({', '.join(crashed_tools)})")
            if "llm_parse_error" in error_types:
                lines.append(f"🧠 Status: Agent struggled with tool syntax ({error_types.count('llm_parse_error')} times)")
        else:
            lines.append("\n✅ Status: Healthy")
        return "\n".join(lines)

    def _file_names(self) -> list[str]:
        """Extract file path strings from files_touched (new dict format or legacy str format)."""
        result = []
        for f in self.files_touched:
            if isinstance(f, dict):
                result.append(f.get("path", "?"))
            else:
                result.append(str(f))
        return result

    def _file_display_list(self) -> list[str]:
        """Format files with operation icons for display."""
        result = []
        for f in self.files_touched:
            if isinstance(f, dict):
                path = f.get("path", "?")
                flags = []
                if f.get("has_write"):
                    flags.append("✍️")
                if f.get("has_delete"):
                    flags.append("🗑️")
                if f.get("has_read") and not f.get("has_write"):
                    flags.append("👁️")
                result.append(f"{path}({''.join(flags)})" if flags else path)
            else:
                result.append(str(f))
        return result



def _extract_session_info(messages: List[dict]) -> Dict[str, Any]:
    """Extract summary and user message preview from messages.

    files_touched is no longer extracted here — it comes from deterministic
    tool call tracking in Agent._touched_files.
    """
    user_messages = []

    for m in messages:
        role = m.get("role", "")
        text = m.get("content", "") or ""

        # Collect user messages for preview
        if role == "user" and text and len(user_messages) < 5:
            user_messages.append(text.strip()[:200])

    # Generate summary from user messages
    if user_messages:
        summary = "User asked: " + "; ".join(user_messages[:3])
        if len(user_messages) > 3:
            summary += f" (+{len(user_messages) - 3} more requests)"
    else:
        summary = ""

    return {
        "user_message_preview": user_messages[0] if user_messages else "",
        "summary": summary,
    }


def _estimate_tokens(messages: List[dict], model: str = "default") -> int:
    """Estimate total token count for messages using the shared tokenizer.

    Reuses ``token_utils.get_token_estimator`` so session persistence estimates
    use the same model-aware tokenizer as context compression, instead of the
    old error-prone ``len(text) // 3`` heuristic.
    """
    from .token_utils import get_token_estimator
    estimator = get_token_estimator(model)
    total = 0
    for m in messages:
        content = m.get("content", "") or ""
        if isinstance(content, str):
            total += estimator(content)
        elif isinstance(content, list):  # multimodal parts
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimator(part.get("text", ""))
        tool_calls = m.get("tool_calls", [])
        if tool_calls:
            total += estimator(str(tool_calls))
    return total


def save_session(
    messages: list,
    model: str,
    session_id: Optional[str] = None,
    auto_save: bool = False,
    files_touched: Optional[list[dict]] = None,
    system_errors: Optional[list[dict]] = None,
    working_dir: Optional[str] = None,
    usage: Optional[dict] = None,
) -> str:
    """Save conversation to disk. Returns the session ID.

    Args:
        messages: Conversation messages (AgentMessage objects or plain dicts)
        model: Model name used
        session_id: Optional session ID (generated if not provided)
        auto_save: If True, this is an auto-save (updates timestamp only)
        files_touched: Deterministic file list from tool call tracking
        system_errors: Optional structured error log to persist as session metadata
        working_dir: Optional working directory to persist for resume safety check
        usage: Optional cumulative usage stats dict (e.g. from
            Agent.get_usage_stats()).  Monotonically increasing — distinct from
            ``token_estimate`` which reflects current context size.
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    
    if not session_id:
        session_id = f"session_{int(time.time())}"
    
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # Normalize messages to plain dicts (handles AgentMessage objects)
    from .message import AgentMessage
    if messages and isinstance(messages[0], AgentMessage):
        messages = [m.to_dict() for m in messages]
    
    # Load existing data if auto-saving
    existing_data = {}
    if auto_save:
        path = SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            existing = _safe_read_json(path)
            if existing is not None:
                existing_data = existing
    
    # Extract session info (summary + user message preview only)
    info = _extract_session_info(messages)

    # Use deterministic files_touched from tool call tracking, falling back
    # to existing data for auto-save when no new data is provided
    if files_touched is not None:
        resolved_files = files_touched
    else:
        resolved_files = existing_data.get("files_touched", [])

    # Clean surrogates from messages before serialization to prevent
    # 'utf-8' codec can't encode characters errors
    clean_messages = _clean_surrogates(messages)

    data = {
        "id": session_id,
        "model": model,
        "created_at": existing_data.get("created_at", now),
        "saved_at": now,
        "working_dir": working_dir or existing_data.get("working_dir", ""),
        "messages": clean_messages,
        "message_count": len(clean_messages),
        "token_estimate": _estimate_tokens(clean_messages, model),
        "system_errors": _clean_surrogates(system_errors) if system_errors is not None
                          else existing_data.get("system_errors", []),
        "files_touched": resolved_files,
        "usage": usage if usage is not None else existing_data.get("usage", {}),
        **info,
    }
    
    path = SESSIONS_DIR / f"{session_id}.json"
    # Atomic write: write to temp file then os.replace to avoid truncated
    # session files if the process is killed mid-write.
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json_str, encoding="utf-8")
    os.replace(tmp_path, path)
    return session_id


async def save_session_async(
    messages: list[dict],
    model: str,
    session_id: Optional[str] = None,
    auto_save: bool = False,
    files_touched: Optional[list[dict]] = None,
    system_errors: Optional[list[dict]] = None,
    working_dir: Optional[str] = None,
    usage: Optional[dict] = None,
) -> str:
    """Async wrapper for save_session using thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _write_executor,
        save_session,
        messages,
        model,
        session_id,
        auto_save,
        files_touched,
        system_errors,
        working_dir,
        usage,
    )


class SessionManager:
    """Manage session persistence with lazy flush.
    
    This mirrors Claude Code's session management:
    - Auto-save on every message (with lazy flush)
    - Batch writes within LAZY_FLUSH_INTERVAL
    - Fire-and-forget for assistant messages
    - Await for user messages (critical checkpoints)
    """
    
    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id
        self._pending_save: Optional[asyncio.Task] = None
        self._last_save_time: float = 0
        self._save_count: int = 0
    
    async def record_message(
        self,
        messages: list[dict],
        model: str,
        is_critical: bool = False,
        files_touched: Optional[list[dict]] = None,
        system_errors: Optional[list[dict]] = None,
        working_dir: Optional[str] = None,
        usage: Optional[dict] = None,
    ) -> None:
        """Record a message to session storage.

        Args:
            messages: Full message history
            model: Current model name
            is_critical: If True, wait for write to complete (e.g., user messages)
            files_touched: Deterministic file list from tool call tracking
            system_errors: Optional structured error log to persist
            working_dir: Optional working directory to persist for resume safety
            usage: Optional cumulative usage stats dict (see ``save_session``).
        """
        now = time.time()

        # If within flush interval, batch the write
        if not is_critical and (now - self._last_save_time) < LAZY_FLUSH_INTERVAL:
            # Cancel pending save and schedule new one
            if self._pending_save and not self._pending_save.done():
                self._pending_save.cancel()

            self._pending_save = asyncio.create_task(
                save_session_async(messages, model, self.session_id, auto_save=True,
                                   files_touched=files_touched, system_errors=system_errors,
                                   working_dir=working_dir, usage=usage)
            )
            self._last_save_time = now
            self._save_count += 1
            return

        # Critical save or past flush interval - wait for completion
        if self._pending_save and not self._pending_save.done():
            try:
                await self._pending_save
            except asyncio.CancelledError:
                pass

        # Save now
        if not self.session_id:
            self.session_id = save_session(messages, model, auto_save=False,
                                           files_touched=files_touched,
                                           system_errors=system_errors,
                                           working_dir=working_dir, usage=usage)
        else:
            await save_session_async(messages, model, self.session_id, auto_save=True,
                                    files_touched=files_touched, system_errors=system_errors,
                                    working_dir=working_dir, usage=usage)

        self._last_save_time = time.time()
        self._save_count += 1

    async def flush(self, messages: list[dict], model: str,
                    files_touched: Optional[list[dict]] = None,
                    system_errors: Optional[list[dict]] = None,
                    working_dir: Optional[str] = None,
                    usage: Optional[dict] = None) -> None:
        """Force flush any pending saves."""
        if self._pending_save and not self._pending_save.done():
            try:
                await self._pending_save
            except asyncio.CancelledError:
                pass

        # Final save
        if self.session_id:
            await save_session_async(messages, model, self.session_id,
                                     auto_save=True, files_touched=files_touched,
                                     system_errors=system_errors, working_dir=working_dir,
                                     usage=usage)
    
    def get_stats(self) -> dict:
        """Get session manager statistics."""
        return {
            "session_id": self.session_id,
            "save_count": self._save_count,
            "last_save_time": self._last_save_time,
        }


def load_session(session_id: str) -> tuple[list, str, Optional[SessionMetadata]] | None:
    """Load a saved session. Returns (messages, model, metadata) or None.
    
    Messages are returned as AgentMessage objects (converted from stored dicts
    via AgentMessage.from_dict, which handles old-format sessions gracefully).
    """
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    
    data = _safe_read_json(path)
    if data is None:
        return None
    metadata = SessionMetadata(session_id, data)
    
    # Convert stored dicts to AgentMessage objects
    from .message import dicts_to_messages
    raw_messages = data.get("messages", [])
    messages = dicts_to_messages(raw_messages)
    
    return messages, data["model"], metadata


def list_sessions(limit: int = 20) -> list[SessionMetadata]:
    """List available sessions, newest first, with metadata."""
    if not SESSIONS_DIR.exists():
        return []
    
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        data = _safe_read_json(f)
        if data is None:
            continue
        metadata = SessionMetadata(data.get("id", f.stem), data)
        sessions.append(metadata)
        
        if len(sessions) >= limit:
            break
    
    return sessions


def get_session(session_id: str) -> Optional[SessionMetadata]:
    """Get metadata for a specific session without loading full messages."""
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    
    try:
        data = _safe_read_json(path)
        if data is None:
            return None
        return SessionMetadata(session_id, data)
    except (KeyError, FileNotFoundError):
        return None


def search_sessions(query: str, limit: int = 10) -> list[SessionMetadata]:
    """Search sessions by summary, files touched, or user messages."""
    query_lower = query.lower()
    results = []
    
    for f in SESSIONS_DIR.glob("*.json"):
        data = _safe_read_json(f)
        if data is None:
            continue
        
        # Search in summary, files, and user messages
        raw_files = data.get("files_touched", [])
        file_paths = " ".join(
            f.get("path", "") if isinstance(f, dict) else str(f)
            for f in raw_files
        )
        searchable = [
            data.get("summary", ""),
            data.get("user_message_preview", ""),
            file_paths,
            data.get("model", ""),
        ]
        
        if any(query_lower in text.lower() for text in searchable if text):
            metadata = SessionMetadata(data.get("id", f.stem), data)
            results.append(metadata)
        
        if len(results) >= limit:
            break
    
    return results


def delete_session(session_id: str) -> bool:
    """Delete a session."""
    path = SESSIONS_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def export_session(session_id: str, output_path: str) -> bool:
    """Export a session to a markdown file."""
    result = load_session(session_id)
    if not result:
        return False
    
    messages, model, metadata = result
    
    lines = [
        f"# LazyCatCoder Session Export",
        f"",
        f"## Metadata",
        f"- **Session ID**: {session_id}",
        f"- **Model**: {model}",
        f"- **Created**: {metadata.created_at}",
        f"- **Last Saved**: {metadata.saved_at}",
        f"- **Messages**: {metadata.message_count}",
        f"- **Tokens**: ~{metadata.token_estimate:,}",
        f"",
        f"## Summary",
        f"{metadata.summary or 'No summary available'}",
        f"",
        f"## Files Touched",
    ]
    
    for f in metadata._file_display_list():
        lines.append(f"- `{f}`")
    
    lines.extend([
        f"",
        f"## Conversation",
        f"",
    ])
    
    for msg in messages:
        # Handle both AgentMessage objects and plain dicts
        if hasattr(msg, 'role'):
            role = msg.role.value if hasattr(msg.role, 'value') else str(msg.role)
        else:
            role = msg.get("role", "unknown")
        content = msg.content if hasattr(msg, 'content') else msg.get("content", "")
        lines.append(f"### {role.title()}")
        lines.append(f"")
        lines.append(f"```")
        lines.append(f"{content}")
        lines.append(f"```")
        lines.append(f"")
    
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    return True
