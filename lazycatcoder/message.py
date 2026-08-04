"""AgentMessage — typed message representation for the agent loop.

Replaces bare dicts with a lightweight dataclass.  The key insight is that
not all messages should be sent to the LLM (e.g. compact boundaries,
internal notifications).  The ``llm_visible`` flag controls this, and
``to_llm_dict()`` only emits messages where it is True.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from uuid import uuid4
#
try:
    from enum import StrEnum
except ImportError:
    # Backport StrEnum for Python versions before 3.11
    from enum import Enum
    class StrEnum(str, Enum):
        pass
from typing import Any


class MessageRole(StrEnum):
    """Message roles matching OpenAI's chat completion API."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    # Non-OpenAI roles (internal only, never sent to LLM as-is)
    SYSTEM_NOTIFY = "system_notify"


@dataclass(slots=True)
class AgentMessage:
    """A single message in the conversation history.

    Fields:
        role: Message role (system, user, assistant, tool, system_notify).
        content: Text content of the message.
        tool_calls: List of OpenAI-format tool call dicts (assistant only).
        tool_call_id: ID linking a tool result to its call (tool role only).
        tool_name: Name of the tool that produced this result (tool role only).
        llm_visible: If False, this message is filtered out by
            ``to_llm_dict()`` and ``_full_messages()``.  Used for compact
            boundaries and internal notifications that shouldn't be sent
            to the LLM as raw messages.
        created_at: Unix timestamp for ordering (not sent to LLM).
        metadata: Free-form dict for future extensions (source, skill_id, etc.).
    """
    role: MessageRole
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    llm_visible: bool = True
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    # Session Tree fields — enable future /undo and /branch commands.
    # id uniquely identifies this message; parent_id links to the previous
    # message in the conversation chain.  None means root (first message).
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    parent_id: str | None = None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Convert to a plain dict for JSON persistence.

        Includes all fields so that ``from_dict`` can fully reconstruct
        the message.  Omits None values to keep the JSON compact.
        """
        d: dict[str, Any] = {
            "role": self.role.value,
            "content": self.content,
        }
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_name is not None:
            d["tool_name"] = self.tool_name
        if not self.llm_visible:
            d["llm_visible"] = False
        d["created_at"] = self.created_at
        if self.metadata:
            d["metadata"] = self.metadata
        d["id"] = self.id
        if self.parent_id is not None:
            d["parent_id"] = self.parent_id
        return d

    def to_llm_dict(self) -> dict | None:
        """Convert to OpenAI chat-completion message format.

        Returns None if ``llm_visible`` is False (message should be
        filtered out of the LLM context).

        For ``system_notify`` messages, also returns None — these are
        internal notifications that should not be sent to the LLM as-is.
        """
        if not self.llm_visible:
            return None
        if self.role == MessageRole.SYSTEM_NOTIFY:
            return None

        d: dict[str, Any] = {"role": self.role.value}

        # assistant messages may have tool_calls with null content
        if self.role == MessageRole.ASSISTANT:
            d["content"] = self.content or None
            if self.tool_calls:
                d["tool_calls"] = self.tool_calls
        elif self.role == MessageRole.TOOL:
            d["content"] = self.content
            if self.tool_call_id is not None:
                d["tool_call_id"] = self.tool_call_id
        else:
            d["content"] = self.content

        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AgentMessage":
        """Reconstruct an AgentMessage from a plain dict.

        Handles both new-format (AgentMessage.to_dict) and old-format
        (bare dict from pre-Phase-2 sessions) gracefully.
        """
        role_str = d.get("role", "system")
        try:
            role = MessageRole(role_str)
        except ValueError:
            role = MessageRole.SYSTEM_NOTIFY  # unknown role → internal notify

        # tool_calls may be stored as a list of dicts
        tool_calls = d.get("tool_calls", []) or []

        return cls(
            role=role,
            content=d.get("content", "") or "",
            tool_calls=tool_calls,
            tool_call_id=d.get("tool_call_id"),
            tool_name=d.get("tool_name"),
            llm_visible=d.get("llm_visible", True),
            created_at=d.get("created_at", time.time()),
            metadata=d.get("metadata", {}),
            id=d.get("id") or uuid4().hex[:12],
            parent_id=d.get("parent_id"),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def messages_to_llm_dicts(messages: list[AgentMessage]) -> list[dict]:
    """Convert a list of AgentMessages to OpenAI-format dicts.

    Filters out messages where ``to_llm_dict()`` returns None.
    """
    result = []
    for m in messages:
        d = m.to_llm_dict()
        if d is not None:
            result.append(d)
    return result


def messages_to_dicts(messages: list[AgentMessage]) -> list[dict]:
    """Convert a list of AgentMessages to plain dicts for persistence."""
    return [m.to_dict() for m in messages]


def dicts_to_messages(dicts: list[dict]) -> list[AgentMessage]:
    """Convert a list of plain dicts to AgentMessages.

    Handles old-format sessions gracefully via ``AgentMessage.from_dict``.
    """
    return [AgentMessage.from_dict(d) for d in dicts]
