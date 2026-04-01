# NanoCoder

**The entire essence of an AI coding agent, in ~1300 lines of Python.**

NanoCoder distills the core architecture of production AI coding agents (like Claude Code) into a minimal, hackable, and fully functional implementation. Think of it as **nanoGPT for coding agents** — small enough to read in one sitting, powerful enough to actually use.

> I analyzed 512,000 lines of leaked Claude Code source, extracted the key architectural patterns, and reimplemented them in ~1,300 lines of clean Python. This is the result.

[English](README.md) | [中文](README_CN.md)

## Why NanoCoder?

|  | Claude Code | Claw-Code | NanoCoder |
|---|---|---|---|
| Language | TypeScript (512K LoC) | Python + Rust | **Python (~1,300 LoC)** |
| LLM Support | Anthropic only | Multi-provider | **Any OpenAI-compatible API** |
| Can you read the full source? | No (proprietary) | Difficult (huge codebase) | **Yes, in one afternoon** |
| Designed for | End users | End users | **Developers who want to build their own** |
| Hackability | Closed source | Complex architecture | **Fork and build in minutes** |

NanoCoder is **not** trying to replace Claude Code. It's a **reference implementation** and **starting point** for developers who want to understand how AI coding agents work and build their own.

## Features

Every key architectural pattern from Claude Code, distilled:

- **Agentic tool loop** — LLM calls tools, observes results, decides next step, repeats until done
- **7 built-in tools** — bash, read_file, write_file, edit_file, glob, grep, **agent** (sub-agents)
- **Search-and-replace editing** — the key innovation that makes LLM code edits reliable (exact match + **unified diff output**)
- **Sub-agent spawning** — delegate complex sub-tasks to independent agents with isolated context (Claude Code's AgentTool)
- **Parallel tool execution** — multiple tool calls run concurrently via ThreadPool (inspired by StreamingToolExecutor)
- **3-layer context compression** — tool output snipping → LLM summarization → hard collapse (mirrors Claude Code's HISTORY_SNIP → Microcompact → CONTEXT_COLLAPSE)
- **Dangerous command detection** — blocks `rm -rf /`, fork bombs, `curl | bash`, etc.
- **Streaming output** — tokens appear in real-time as the model generates
- **Session persistence** — save/resume conversations across sessions
- **Any LLM provider** — OpenAI, DeepSeek, Qwen, Kimi, GLM, Ollama, or any OpenAI-compatible endpoint
- **Interactive REPL** — command history, model switching, token tracking
- **One-shot mode** — pipe tasks via `nanocoder -p "fix the bug in main.py"`

## Quick Start

```bash
pip install nanocoder

# OpenAI
export OPENAI_API_KEY=sk-...
nanocoder

# DeepSeek
export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com
nanocoder -m deepseek-chat

# Qwen (via DashScope)
export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
nanocoder -m qwen-plus

# Ollama (local)
export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1
nanocoder -m qwen2.5-coder
```

## Supported LLM Providers

NanoCoder works with **any OpenAI-compatible API**. Here are some popular ones:

| Provider | Base URL | Example Model |
|---|---|---|
| OpenAI | *(default)* | `gpt-4o`, `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat`, `deepseek-coder` |
| Qwen (Alibaba) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus`, `qwen-max` |
| Kimi (Moonshot) | `https://api.moonshot.cn/v1` | `moonshot-v1-128k` |
| GLM (Zhipu) | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` |
| Ollama | `http://localhost:11434/v1` | `qwen2.5-coder`, `llama3` |
| vLLM | `http://localhost:8000/v1` | *(your served model)* |
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4` |
| Together AI | `https://api.together.xyz/v1` | `meta-llama/Llama-3-70b` |

## Architecture

The entire codebase fits in your head:

```
nanocoder/
├── cli.py          # REPL interface & arg parsing         (~160 lines)
├── agent.py        # Core agent loop + parallel exec      (~110 lines)
├── llm.py          # OpenAI-compatible streaming client    (~115 lines)
├── context.py      # 3-layer context compression           (~145 lines)
├── session.py      # Save/resume conversations             (~65 lines)
├── prompt.py       # System prompt generation              (~35 lines)
├── config.py       # Environment-based configuration       (~30 lines)
└── tools/
    ├── base.py     # Tool base class                       (~20 lines)
    ├── bash.py     # Shell execution + safety checks       (~80 lines)
    ├── read.py     # File reading with line numbers        (~40 lines)
    ├── write.py    # File creation/overwrite               (~30 lines)
    ├── edit.py     # Search-and-replace + unified diff     (~70 lines)
    ├── glob_tool.py # File pattern matching                (~35 lines)
    ├── grep.py     # Regex content search                  (~60 lines)
    └── agent.py    # Sub-agent spawning                    (~50 lines)
```

### How the Agent Loop Works

```
User input
    │
    ▼
┌─────────────────────────────┐
│  Build messages              │
│  (system prompt + history)   │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Call LLM (streaming)        │◄──────────────┐
│  with tool definitions       │               │
└──────────┬──────────────────┘               │
           │                                   │
     ┌─────┴─────┐                            │
     │           │                             │
  text?     tool calls?                        │
     │           │                             │
     ▼           ▼                             │
  Return    Execute each tool                  │
  to user   Append results to history ─────────┘
```

This is the same fundamental loop used by Claude Code, ChatGPT, and every other agentic coding assistant. The difference is that here you can read and modify every piece of it.

### Key Design Decisions (from Claude Code)

1. **Search-and-replace editing** (`edit_file`): Instead of line-number patches or whole-file rewrites, the LLM specifies an exact substring to find and replace. The substring must be unique in the file, eliminating edit ambiguity. Returns a unified diff so you can see exactly what changed. This is Claude Code's most important innovation for reliable code editing.

2. **Sub-agent spawning** (`agent`): For complex sub-tasks, spawn an independent agent with its own conversation history. This prevents context pollution and enables divide-and-conquer workflows. Claude Code's AgentTool is 1,397 lines; NanoCoder's is 50.

3. **Parallel tool execution**: When the LLM returns multiple tool calls in one response, NanoCoder executes them concurrently using a thread pool. This mirrors Claude Code's StreamingToolExecutor (530 lines) which starts executing tools while the model is still generating.

4. **3-layer context compression**: Mirrors Claude Code's multi-layer strategy (HISTORY_SNIP → Microcompact → CONTEXT_COLLAPSE):
   - Layer 1: Snip verbose tool outputs to head+tail
   - Layer 2: LLM-powered summarization of old conversation turns
   - Layer 3: Hard collapse when nearing the limit

5. **Dangerous command detection**: The bash tool checks commands against known destructive patterns (`rm -rf /`, fork bombs, `curl | bash`) before execution. Claude Code's BashTool (1,143 lines) has extensive safety checks; NanoCoder implements the essential ones.

6. **Read-before-edit discipline**: The system prompt instructs the LLM to always read a file before modifying it, preventing blind edits.

7. **Tool output truncation**: Very long command outputs are truncated preserving head + tail (the most useful parts), preventing context window waste.

## Extending NanoCoder

Adding a new tool takes ~20 lines:

```python
# nanocoder/tools/my_tool.py
from .base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "Does something useful."
    parameters = {
        "type": "object",
        "properties": {
            "arg1": {"type": "string", "description": "..."},
        },
        "required": ["arg1"],
    }

    def execute(self, arg1: str) -> str:
        # your logic here
        return "result"
```

Then register it in `tools/__init__.py`. That's it.

You can also use NanoCoder as a library:

```python
from nanocoder.agent import Agent
from nanocoder.llm import LLM

llm = LLM(model="deepseek-chat", api_key="sk-...", base_url="https://api.deepseek.com")
agent = Agent(llm=llm)
response = agent.chat("Read main.py and add error handling to the parse function")
print(response)
```

## Configuration

All config is via environment variables (no config files to manage):

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | API key for your LLM provider |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API endpoint |
| `NANOCODER_MODEL` | `gpt-4o` | Model name |
| `NANOCODER_MAX_TOKENS` | `4096` | Max tokens per response |
| `NANOCODER_TEMPERATURE` | `0` | Sampling temperature |
| `NANOCODER_MAX_CONTEXT` | `128000` | Context window size |

## REPL Commands

| Command | Description |
|---|---|
| `/help` | Show available commands |
| `/reset` | Clear conversation history |
| `/model <name>` | Switch model mid-conversation |
| `/tokens` | Show token usage for this session |
| `/save` | Save conversation to disk |
| `/sessions` | List saved sessions |
| `quit` | Exit NanoCoder |

Resume a saved session: `nanocoder -r <session_id>`

## Philosophy

NanoCoder follows the **nanoGPT philosophy**: minimize complexity, maximize understanding.

- Every file has a single responsibility
- No abstractions for the sake of abstractions
- Comments explain *why*, not *what*
- The whole thing is meant to be forked and modified

If Claude Code is a car, NanoCoder is the engine on a test bench. You can see every moving part, understand how it works, and swap components to build your own vehicle.

## Related

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic's official coding agent (proprietary)
- [Claw-Code](https://github.com/instructkr/claw-code) — Full-featured clean-room reimplementation in Python/Rust
- [nanoGPT](https://github.com/karpathy/nanoGPT) — The inspiration for this project's philosophy
- [Aider](https://github.com/paul-gauthier/aider) — Established Python AI pair programming tool

## License

MIT License. Fork it, ship it, build something great.

## Author

**Yufeng He** ([@he-yufeng](https://github.com/he-yufeng))

- Agentic AI Researcher @ Moonshot AI (Kimi)
- MS CS @ HKU | Former @ Baidu, Kuaishou
- [Zhihu article: Claude Code Source Analysis (170K+ reads)](https://zhuanlan.zhihu.com/p/1898797658343862272)
