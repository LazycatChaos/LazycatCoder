# LazyCatCoder

[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://github.com/he-yufeng/NanoCoder/actions/workflows/ci.yml/badge.svg)](https://github.com/he-yufeng/NanoCoder/actions)

[English](README.md) | [中文](README_CN.md)

**A production-grade autonomous coding agent — built from scratch in ~1,300 lines of Python.**

LazyCatCoder is a fully functional AI coding agent that reverse-engineers the core architectural patterns of Claude Code and reimplements them in a minimal, readable Python codebase. It features an autonomous agent loop, multi-layer context compression, parallel tool execution, sub-agent orchestration, and a pluggable tool system — all working together in a single, cohesive system.

---

## Core Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Agent Loop                          │
│  User Input → LLM (with tools) → Tool Calls → Execute   │
│       ↑                                    ↓            │
│       └──── Response ←────────────── Result ←┘           │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │  Context    │  │   Session    │  │   Tool         │ │
│  │  Manager    │  │   Manager    │  │   Registry     │ │
│  │  (4 layers) │  │  (async I/O) │  │   (14 tools)   │ │
│  └─────────────┘  └──────────────┘  └────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Key Technical Highlights

### 1. Autonomous Agent Loop with Circuit Breaker
- Multi-turn tool calling with automatic termination detection
- **Circuit breaker pattern**: detects consecutive tool-call failures and injects a system-level warning to break infinite loops
- Language-aware error messages (auto-detects Chinese vs English)
- Configurable max rounds with graceful degradation

### 2. Four-Layer Context Compression Strategy
| Layer | Trigger | Strategy |
|---|---|---|
| **Tool Snip** | >50% token budget | Truncates verbose tool results to first/last lines |
| **LLM Summarize** | >70% token budget | LLM-powered summary of old conversation turns |
| **Hard Collapse** | >90% token budget | Emergency: keep only summary + last 4 messages |
| **Auto-compact** | >40% + 10k new tokens | Background daemon thread, non-blocking via lock-copy-swap |

- **Lazy GC pattern**: immediately releases pre-compaction messages for garbage collection
- Model-aware token counting (Qwen tokenizer → tiktoken → heuristic fallback)

### 3. Parallel Tool Execution with Safety
- **Read-only tools** (read_file, grep, glob, symbols) run concurrently via ThreadPoolExecutor
- **Write tools** (write_file, edit_file, bash) execute sequentially to prevent race conditions
- Results returned in original call order for correct message pairing

### 4. Multi-Agent Orchestration
- Sub-agent spawning with isolated context
- Parent-child agent communication through the tool system
- Configurable workdir and virtual environment per agent

### 5. Pluggable Tool System (14 Built-in Tools)
| Category | Tools |
|---|---|
| **File I/O** | read_file, write_file, edit_file, delete_file |
| **Search** | glob, grep, project_structure, get_file_symbols |
| **Shell** | bash (with working directory & venv support) |
| **Web** | web_search, fetch_url |
| **Agent** | agent (sub-agent), todo_write |

Each tool is a ~20-line class implementing `name`, `schema()`, and `execute()` — trivially extensible.

### 6. Real-Time Session Persistence
- Async session save with critical/non-critical priority levels
- Fire-and-forget pattern: main loop never blocks on disk I/O
- Session resume with full conversation history and model config

### 7. Streaming LLM Client
- OpenAI-compatible API with automatic retry (exponential backoff)
- Token usage tracking (input/output/total per session)
- Debug mode with rich panel output for tool execution visualization

## Project Structure

```
lazycatcoder/
├── agent.py          Agent loop + parallel execution + circuit breaker    514 lines
├── context.py        4-layer compression + lazy GC + token counting       391 lines
├── llm.py            Streaming client + retry + token tracking            150 lines
├── cli.py            REPL + slash commands + session management           160 lines
├── session.py        Async session persistence                            65 lines
├── prompt.py         Dynamic system prompt generation                     35 lines
├── config.py         Environment-based configuration                      30 lines
└── tools/
    ├── base.py       Tool abstract base class + registry                   40 lines
    ├── bash.py       Shell execution + workdir + venv support              95 lines
    ├── edit.py       Search-replace with unique-match safety               70 lines
    ├── read.py       File reading with offset/limit                        40 lines
    ├── write.py      File writing with auto-directory creation             30 lines
    ├── delete.py     File deletion with sandbox protection                 25 lines
    ├── glob_tool.py  File pattern matching                                 35 lines
    ├── grep.py       Regex content search                                  65 lines
    ├── symbols.py    Python AST symbol extraction                          45 lines
    ├── project_structure.py  Directory tree visualization                  30 lines
    ├── agent.py      Sub-agent spawning                                    50 lines
    ├── todo.py       Task tracking                                         30 lines
    ├── web_search.py Web search integration                                35 lines
    └── fetch.py      URL content fetching                                  30 lines
```

## Quick Start

```bash
pip install lazycatcoder

# Interactive mode
lazycatcoder -m kimi-k2.5

# One-shot mode
lazycatcoder -p "find all TODO comments in this project"

# With custom workdir
lazycatcoder --workdir /path/to/project -m gpt-4o
```

## Use as a Library

```python
from lazycatcoder import Agent, LLM

llm = LLM(model="kimi-k2.5", api_key="your-key", base_url="https://api.moonshot.ai/v1")
agent = Agent(llm=llm, workdir="/path/to/project", debug=True)
response = agent.chat("find all TODO comments and list them")
```

## Add Custom Tools (~20 lines)

```python
from lazycatcoder.tools.base import Tool

class HttpTool(Tool):
    name = "http"
    description = "Fetch a URL."
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}

    def execute(self, url: str) -> str:
        import urllib.request
        return urllib.request.urlopen(url).read().decode()[:5000]
```

## REPL Commands

```
/model <name>    Switch model mid-conversation
/compact         Compress context manually
/tokens          Show token usage
/save            Save session to disk
/sessions        List saved sessions
/reset           Clear history
quit             Exit
```

## Test Coverage

48 tests covering core logic, all 14 tools, safety mechanisms, and edge cases:

```bash
python -m pytest tests/ -v
# 48 passed in ~3s
```

## License

MIT. Fork it, learn from it, build something better.
