"""System prompt - the instructions that turn an LLM into a coding agent.

Modular design: tools self-declare their guidelines via `prompt_snippet` and
`prompt_guidelines` class attributes.  The builder assembles them into a
coherent system prompt alongside environment info and cross-cutting rules.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools.base import Tool


# ---------------------------------------------------------------------------
# Options dataclass — extensible without changing the builder signature
# ---------------------------------------------------------------------------

@dataclass
class SystemPromptOptions:
    """Options for building the system prompt.

    All fields have sensible defaults so callers can pass only what they need.
    """
    tools: list[Tool]
    cwd: str = ""
    custom_prompt: str | None = None       # Replace the identity section entirely
    append_prompt: str | None = None       # Append extra content at the end
    guidelines: list[str] = field(default_factory=list)  # Extra cross-cutting rules
    inactive_tools: list[Tool] = field(default_factory=list)  # Registered but not active


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_system_prompt(
    tools: list[Tool],
    inactive_tools: list[Tool] | None = None,
    workdir: str = "",
) -> str:
    """Assemble the system prompt from modular sections.

    Args:
        tools: Active tools (name + description + guidelines shown).
        inactive_tools: Registered but not-yet-active tools (shown in a
            separate "Inactive Tools" section so the LLM knows they exist
            and can call ``activate_tool`` to enable them).
        workdir: Working directory for the environment section.
    """
    opts = SystemPromptOptions(
        tools=tools,
        cwd=workdir or "",
        inactive_tools=inactive_tools or [],
    )
    return _build_from_options(opts)


def _build_from_options(opts: SystemPromptOptions) -> str:
    """Internal builder — accepts a fully-populated options object."""
    cwd = opts.cwd or os.getcwd()
    uname = platform.uname()

    # --- Section 1: Identity ---
    if opts.custom_prompt:
        identity = opts.custom_prompt
    else:
        identity = (
            "You are LazyCatCoder, an AI coding assistant running in the user's terminal.\n"
            "You help with software engineering: writing code, fixing bugs, refactoring, "
            "explaining code, running commands, and more."
        )

    # --- Section 2: Environment ---
    env = (
        f"# Environment\n"
        f"- Working directory: {cwd}\n"
        f"- OS: {uname.system} {uname.release} ({uname.machine})\n"
        f"- Python: {platform.python_version()}"
    )

    # --- Section 3: Tools (name + full description) ---
    tool_list = "\n".join(f"- **{t.name}**: {t.description}" for t in opts.tools)

    # --- Section 3b: Inactive tools (registered, not yet active) ---
    inactive_block = ""
    if opts.inactive_tools:
        inactive_names = ", ".join(t.name for t in opts.inactive_tools)
        inactive_block = (
            f"\n\n## Inactive Tools (require activation)\n"
            f"The following tools are registered but NOT active. "
            f"To use them, call `activate_tool` first; they will be available next turn.\n"
            f"Tools: {inactive_names}"
        )

    # --- Section 4: Tool Guidelines (self-declared by each tool) ---
    tool_guidelines: list[str] = []
    for t in opts.tools:
        for g in t.prompt_guidelines:
            tool_guidelines.append(f"- **{t.name}**: {g}")
    tool_guidelines_block = "\n".join(tool_guidelines) if tool_guidelines else ""

    # --- Section 5: Cross-cutting Rules (not tool-specific) ---
    rules = _build_rules_block(opts.guidelines)

    # --- Assemble ---
    parts = [identity, "", env, "", "# Tools", tool_list + inactive_block]

    if tool_guidelines_block:
        parts.extend(["", "# Tool Guidelines", tool_guidelines_block])

    parts.extend(["", rules])

    if opts.append_prompt:
        parts.extend(["", opts.append_prompt])

    return "\n".join(parts)


def _build_rules_block(extra_guidelines: list[str] | None = None) -> str:
    """Build the cross-cutting rules section.

    Tool-specific rules (e.g. 'read before edit', 'use project_structure first')
    have been migrated to each tool's `prompt_guidelines`.  This section only
    contains rules that apply across all tools.
    """
    lines = [
        "# Rules",
        "",
        "## 🛠️ Tool Mastery & Workflow",
        '1. **Batch Aggressively:** When file paths are already known (either given by the user or discovered in the previous step), batch multiple tool calls in one round (e.g. `read_file` × 3 + `bash`). Do NOT batch `read_file` calls if you don\'t yet know the correct paths — explore first, then batch.',
        "",
        "## 🧠 Engineering Standards",
        '2. **Surgical Changes:** Touch ONLY what you must. Match existing style exactly. Do NOT "improve" or reformat adjacent code, comments, or imports. **Violation example:** fixing a function and also renaming an unrelated variable. Clean up only your own dead code.',
        '3. **Simplicity First:** Write the minimum code necessary. No speculative features or "future-proofing". Propose simpler approaches if the request seems over-engineered.',
        "4. **Goal-Driven Verification:** Run relevant tests/commands *before* changes to understand current behavior, and *after* changes to verify correctness. Do not assume code works.",
        "",
        "## 💬 Communication & Behavior",
        "5. **Think Before Coding:** If the request is ambiguous, state assumptions and tradeoffs. **If 1-2 file reads can resolve the ambiguity, do that first in a batched call, then ask a focused question.**",
        "6. **Direct Output & Conciseness:** For explanations, code reviews, or summaries, output Markdown directly in chat (do not write to file unless asked). Prioritize showing code over long explanations; explain only what isn't obvious.",
    ]

    if extra_guidelines:
        lines.append("")
        for i, g in enumerate(extra_guidelines, start=7):
            lines.append(f"{i}. {g}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backward-compatible wrapper — existing callers use system_prompt(tools, workdir)
# ---------------------------------------------------------------------------

def system_prompt(tools, workdir: str = None) -> str:
    """Backward-compatible wrapper. Delegates to build_system_prompt()."""
    return build_system_prompt(tools, workdir=workdir or "")
