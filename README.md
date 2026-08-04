# LazyCatCoder

[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://github.com/he-yufeng/NanoCoder/actions/workflows/ci.yml/badge.svg)](https://github.com/he-yufeng/NanoCoder/actions)

[English](README.md) | [中文](README_CN.md)

**A production-grade autonomous coding agent — built from scratch in Python.**

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
│  │  (4 layers) │  │  (async I/O) │  │   (13 tools)   │ │
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

### 5. Pluggable Tool System (13 Built-in Tools)
| Category | Tools |
|---|---|
| **File I/O** | read_file, write_file, edit_file, delete_file |
| **Search** | glob, grep, project_structure, get_file_symbols |
| **Shell** | bash (with working directory & venv support) |
| **Web** | web_search, fetch_url |
| **Agent** | agent (sub-agent), todo_write |

Each tool extends the `Tool` base class, implementing `name`, `parameters`, and `execute()`. The base class provides input validation, permission checks, and safe exception handling out of the box.

### 6. Real-Time Session Persistence
- Async session save with critical/non-critical priority levels
- Fire-and-forget pattern: main loop never blocks on disk I/O
- Session resume with full conversation history, model config, and error history
- Health status display on resume (API errors, tool crashes, LLM parse errors)

### 7. Streaming LLM Client
- OpenAI-compatible API with automatic retry (exponential backoff)
- Token usage tracking (input/output/total per session)
- Debug mode with rich panel output for tool execution visualization

### 8. Virtual Environment Support
- Auto-detects `.venv` / `venv` / `env` in the working directory
- `--venv` flag for manual specification, `/venv` command for runtime switching
- bash tool automatically activates the virtual environment

### 9. Error Analysis System
- Categorized by type: API errors, tool crashes, LLM parse errors
- Categorized by tool: identifies the most error-prone tools
- `/errors` command outputs a detailed report with per-error turn and context

## Project Structure

```
lazycatcoder/
├── agent.py              Agent loop + parallel execution + circuit breaker    610 lines
├── context.py            4-layer compression + lazy GC + token counting       338 lines
├── llm.py                Streaming client + retry + token tracking            222 lines
├── cli.py                REPL + slash commands + session management           363 lines
├── session.py            Async session persistence + error history            429 lines
├── prompt.py             Dynamic system prompt generation                      32 lines
├── config.py             Environment-based config + venv detection             56 lines
├── tokenize/             Custom tokenizer (Qwen model support)
│   ├── custom_tokenizer.py
│   ├── tokenizer_base.py
│   └── stopwords.py
└── tools/
    ├── __init__.py       Tool registry + global instance                       94 lines
    ├── base.py           Tool abstract base + ToolResult + safe_execute       118 lines
    ├── bash.py           Shell execution + workdir + venv support              272 lines
    ├── edit.py           Search-replace with unique-match safety               366 lines
    ├── read.py           File reading with offset/limit                        146 lines
    ├── write.py          File writing with auto-directory creation             172 lines
    ├── delete.py         File deletion with sandbox protection                  75 lines
    ├── glob_tool.py      File pattern matching                                 134 lines
    ├── grep.py           Regex content search                                  171 lines
    ├── symbols.py        Python AST symbol extraction                          127 lines
    ├── project_structure.py  Directory tree visualization                      126 lines
    ├── agent.py          Sub-agent spawning                                     53 lines
    ├── todo.py           Task tracking                                         131 lines
    ├── web_search.py     Web search integration                                246 lines
    └── fetch.py          URL content fetching                                  294 lines

article/                   Architecture deep-dive docs (7 articles)
eval/                      Evaluation system (20 test cases + runner + evaluator)
examples/                  Example scripts
tests/                     Unit tests (48 cases)
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

# With virtual environment
lazycatcoder --venv /path/to/.venv -m gpt-4o

# Resume a saved session
lazycatcoder -r <session-id>
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LAZYCATCODER_MODEL` | Model name | `gpt-4o` |
| `OPENAI_API_KEY` / `LAZYCATCODER_API_KEY` / `DEEPSEEK_API_KEY` | API key | — |
| `OPENAI_BASE_URL` / `LAZYCATCODER_BASE_URL` | API base URL | — |
| `LAZYCATCODER_MAX_TOKENS` | Max tokens per response | `16384` |
| `LAZYCATCODER_MAX_CONTEXT` | Context window size | `200000` |
| `LAZYCATCODER_TIMEOUT` | Request timeout (seconds) | `120` |
| `LAZYCATCODER_TEMPERATURE` | Sampling temperature | `0` |
| `LAZYCATCODER_VENV` | Virtual environment path | auto-detect |
| `LAZYCATCODER_DEBUG` | Debug mode | `false` |
| `LAZYCATCODER_THINKING` | Enable reasoning/thinking mode | `false` |

## Use as a Library

```python
from lazycatcoder import Agent, LLM

llm = LLM(model="kimi-k2.5", api_key="your-key", base_url="https://api.moonshot.ai/v1")
agent = Agent(llm=llm, workdir="/path/to/project", debug=True)
response = agent.chat("find all TODO comments and list them")
```

## Add Custom Tools

```python
from lazycatcoder.tools.base import Tool, ToolResult

class HttpTool(Tool):
    name = "http"
    description = "Fetch a URL."
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}

    def execute(self, url: str) -> ToolResult:
        import urllib.request
        content = urllib.request.urlopen(url).read().decode()[:5000]
        return ToolResult.ok(content)
```

## REPL Commands

```
/help              Show this help
/model <name>      Switch model mid-conversation
/timeout <sec>     Set request timeout
/workdir [dir]     Show or change working directory
/venv [path]       Show or set virtual environment
/tokens            Show token usage (total)
/usage             Detailed usage report (per-round avg, context %, errors)
/errors            Error analysis report (by type, by tool, details)
/compact           Compress context manually
/save              Save session to disk
/sessions          List saved sessions
/reset             Save & clear conversation history
/cancel            Cancel current tool execution
/debug             Toggle debug mode
quit               Exit
```

## Test Coverage

48 tests covering core logic, all 13 tools, safety mechanisms, and edge cases:

```bash
python -m pytest tests/ -v
# 48 passed in ~3s
```

## Evaluation System

Built-in evaluation framework with 20 test cases covering bug fixes, refactoring, security, concurrency, and more:

```bash
python -m eval.run
```

## License

MIT. Fork it, learn from it, build something better.
