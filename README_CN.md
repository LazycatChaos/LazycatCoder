# NanoCoder

**一个 AI 编程 Agent 的全部精华，浓缩在 ~1300 行 Python 中。**

NanoCoder 将生产级 AI 编程 Agent（如 Claude Code）的核心架构提炼为一个最小化、可魔改、完整可用的实现。可以理解为 **编程 Agent 领域的 nanoGPT**：小到一个下午就能通读源码，强到可以直接拿来干活。

> 我分析了 Claude Code 泄露的 51.2 万行源码，提炼出关键架构设计，用~1300 行 Python 重新实现。这就是成果。

[English](README.md) | [中文](README_CN.md)

## 为什么选 NanoCoder？

|  | Claude Code | Claw-Code | NanoCoder |
|---|---|---|---|
| 语言 | TypeScript（51万行） | Python + Rust | **Python（~1300行）** |
| 模型支持 | 仅 Anthropic | 多模型 | **任何 OpenAI 兼容 API** |
| 能通读全部源码吗？ | 不能（闭源） | 困难（代码量大） | **可以，一个下午够了** |
| 面向人群 | 终端用户 | 终端用户 | **想搞懂原理、自己造轮子的开发者** |
| 可魔改性 | 闭源 | 架构复杂 | **Fork 下来几分钟就能改** |

NanoCoder **不是**要替代 Claude Code。它是一个**参考实现**和**起点**，帮你搞懂 AI 编程 Agent 的工作原理，在此基础上搭建自己的工具。

## 核心特性

Claude Code 每一个关键架构模式，都有对应实现：

- **Agent 工具循环** — LLM 调用工具 → 观察结果 → 决定下一步 → 重复直到完成
- **7 个内置工具** — bash、read_file、write_file、edit_file、glob、grep、**agent**（子代理）
- **搜索替换式编辑** — 精确匹配 + **unified diff 输出**，让每次编辑都可审查
- **子代理生成** — 把复杂子任务委托给独立 Agent，各自拥有独立上下文（Claude Code 的 AgentTool）
- **并行工具执行** — 多个工具调用通过线程池并发执行（对标 StreamingToolExecutor）
- **三层上下文压缩** — 工具输出裁剪 → LLM 摘要 → 硬压缩（对标 HISTORY_SNIP → Microcompact → CONTEXT_COLLAPSE）
- **危险命令拦截** — 拦截 `rm -rf /`、fork bomb、`curl | bash` 等破坏性操作
- **流式输出** — 模型生成的 token 实时显示
- **会话持久化** — 保存/恢复对话，跨会话继续工作
- **任意 LLM** — OpenAI、DeepSeek、Qwen、Kimi、GLM、Ollama 或任何 OpenAI 兼容端点
- **交互式 REPL** — 命令历史、切换模型、token 统计
- **单次模式** — 通过 `nanocoder -p "修复 main.py 的 bug"` 管道执行

## 快速开始

```bash
pip install nanocoder

# OpenAI
export OPENAI_API_KEY=sk-...
nanocoder

# DeepSeek（国内推荐）
export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com
nanocoder -m deepseek-chat

# 通义千问
export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
nanocoder -m qwen-plus

# Kimi（月之暗面）
export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.moonshot.cn/v1
nanocoder -m moonshot-v1-128k

# Ollama（本地部署）
export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1
nanocoder -m qwen2.5-coder
```

## 支持的 LLM

NanoCoder 支持 **任何 OpenAI 兼容 API**，常用的包括：

| 服务商 | Base URL | 示例模型 |
|---|---|---|
| OpenAI | *（默认）* | `gpt-4o`、`gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat`、`deepseek-coder` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus`、`qwen-max` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-128k` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` |
| Ollama | `http://localhost:11434/v1` | `qwen2.5-coder`、`llama3` |
| vLLM | `http://localhost:8000/v1` | *（你部署的模型）* |
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4` |
| Together AI | `https://api.together.xyz/v1` | `meta-llama/Llama-3-70b` |

## 架构

整个代码库一目了然：

```
nanocoder/
├── cli.py          # REPL 界面 & 参数解析               (~160 行)
├── agent.py        # 核心 Agent 循环 + 并行执行          (~110 行)
├── llm.py          # OpenAI 兼容流式客户端               (~115 行)
├── context.py      # 三层上下文压缩                      (~145 行)
├── session.py      # 会话保存/恢复                       (~65 行)
├── prompt.py       # 系统提示词生成                      (~35 行)
├── config.py       # 环境变量配置                        (~30 行)
└── tools/
    ├── base.py     # 工具基类                            (~20 行)
    ├── bash.py     # Shell 执行 + 安全检查               (~80 行)
    ├── read.py     # 带行号的文件读取                    (~40 行)
    ├── write.py    # 文件创建/覆写                       (~30 行)
    ├── edit.py     # 搜索替换 + unified diff             (~70 行)
    ├── glob_tool.py # 文件模式匹配                       (~35 行)
    ├── grep.py     # 正则内容搜索                        (~60 行)
    └── agent.py    # 子代理生成                          (~50 行)
```

### Agent 循环工作原理

```
用户输入
    │
    ▼
┌─────────────────────────────┐
│  构建消息列表                │
│  (系统提示词 + 对话历史)     │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  调用 LLM（流式）            │◄──────────────┐
│  附带工具定义                │               │
└──────────┬──────────────────┘               │
           │                                   │
     ┌─────┴─────┐                            │
     │           │                             │
  纯文本?   工具调用?                          │
     │           │                             │
     ▼           ▼                             │
  返回给    执行每个工具                       │
  用户      将结果加入历史 ────────────────────┘
```

这和 Claude Code、ChatGPT 以及所有 AI 编程助手的底层循环完全一样。区别在于，这里你可以看到并修改每一个环节。

### 核心设计决策（源自 Claude Code）

1. **搜索替换式编辑**（`edit_file`）：LLM 不用行号补丁，也不用整文件重写，而是指定一个精确的子串来查找和替换。子串必须唯一，杜绝歧义。编辑后自动输出 unified diff，方便审查。Claude Code 最重要的代码编辑创新。

2. **子代理生成**（`agent`）：复杂子任务交给独立 Agent 处理，拥有自己的上下文窗口，防止主对话被污染。Claude Code 的 AgentTool 有 1,397 行，NanoCoder 用 50 行实现核心。

3. **并行工具执行**：当 LLM 一次返回多个工具调用时，NanoCoder 用线程池并发执行。这对标 Claude Code 的 StreamingToolExecutor（530行），它在模型还在生成时就开始执行工具。

4. **三层上下文压缩**：对标 Claude Code 的多层策略（HISTORY_SNIP → Microcompact → CONTEXT_COLLAPSE）：
   - 第1层：裁剪冗长的工具输出，保留头尾
   - 第2层：LLM 驱动的旧对话摘要
   - 第3层：接近硬上限时强制压缩

5. **危险命令拦截**：Bash 工具在执行前检查已知的破坏性模式（`rm -rf /`、fork bomb、`curl | bash`）。Claude Code 的 BashTool 有 1,143 行的安全检查，NanoCoder 实现了其中最核心的部分。

6. **先读后改原则**：系统提示词要求 LLM 修改文件前必须先读取，防止盲改。

7. **工具输出截断**：超长命令输出保留头部和尾部（最有用的部分），防止浪费上下文。

## 扩展 NanoCoder

添加新工具只需 ~20 行：

```python
# nanocoder/tools/my_tool.py
from .base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "做一些有用的事。"
    parameters = {
        "type": "object",
        "properties": {
            "arg1": {"type": "string", "description": "..."},
        },
        "required": ["arg1"],
    }

    def execute(self, arg1: str) -> str:
        # 你的逻辑
        return "result"
```

然后在 `tools/__init__.py` 里注册就行。

也可以把 NanoCoder 当库用：

```python
from nanocoder.agent import Agent
from nanocoder.llm import LLM

llm = LLM(model="deepseek-chat", api_key="sk-...", base_url="https://api.deepseek.com")
agent = Agent(llm=llm)
response = agent.chat("读一下 main.py，给 parse 函数加上错误处理")
print(response)
```

## 配置

全部通过环境变量配置（不需要配置文件）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | *（必填）* | LLM 服务商的 API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 端点 |
| `NANOCODER_MODEL` | `gpt-4o` | 模型名称 |
| `NANOCODER_MAX_TOKENS` | `4096` | 每次回复最大 token 数 |
| `NANOCODER_TEMPERATURE` | `0` | 采样温度 |
| `NANOCODER_MAX_CONTEXT` | `128000` | 上下文窗口大小 |

## REPL 命令

| 命令 | 说明 |
|---|---|
| `/help` | 显示帮助 |
| `/reset` | 清空对话历史 |
| `/model <名称>` | 切换模型 |
| `/tokens` | 查看 token 用量 |
| `/save` | 保存当前会话 |
| `/sessions` | 列出已保存的会话 |
| `quit` | 退出 |

恢复已保存的会话：`nanocoder -r <session_id>`

## 设计哲学

NanoCoder 遵循 **nanoGPT 哲学**：最小化复杂度，最大化理解度。

- 每个文件只做一件事
- 不为抽象而抽象
- 注释解释"为什么"，而不是"是什么"
- 整个项目就是用来 fork 和魔改的

如果说 Claude Code 是一辆车，NanoCoder 就是拆出来放在测试台上的发动机。你可以看清每个零件如何运转，然后拿去组装你自己的车。

## 相关项目

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic 官方编程 Agent（闭源）
- [Claw-Code](https://github.com/instructkr/claw-code) — 完整的 Python/Rust 洁净室重实现
- [nanoGPT](https://github.com/karpathy/nanoGPT) — 本项目哲学的灵感来源
- [Aider](https://github.com/paul-gauthier/aider) — 成熟的 Python AI 结对编程工具

## License

MIT License。Fork 它，改它，用它造点好东西。

## 作者

**何宇峰** ([@he-yufeng](https://github.com/he-yufeng))

- Agentic AI Researcher @ Moonshot AI (Kimi)
- MS CS @ HKU
- [知乎：Claude Code 源码深度分析（17万+ 阅读）](https://zhuanlan.zhihu.com/p/1898797658343862272)
