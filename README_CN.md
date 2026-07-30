# LazyCatCoder

[English](README.md) | [中文](README_CN.md)

[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://github.com/he-yufeng/NanoCoder/actions/workflows/ci.yml/badge.svg)](https://github.com/he-yufeng/NanoCoder/actions)

**一个生产级自主编程 Agent — 用 Python 从零构建。**

LazyCatCoder 是一个功能完整的 AI 编程 Agent，它逆向分析了 Claude Code 的核心架构模式，并用极简、可读的 Python 代码重新实现。系统包含自主 Agent 循环、多层上下文压缩、并行工具执行、子 Agent 编排和可插拔工具系统——所有模块协同工作，构成一个完整的自主编程系统。

---

## 核心架构

```
┌─────────────────────────────────────────────────────────┐
│                     Agent 循环                          │
│  用户输入 → LLM（带工具） → 工具调用 → 执行              │
│       ↑                                    ↓            │
│       └──── 回复 ←────────────── 结果 ←───┘             │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │  上下文     │  │   会话       │  │   工具         │ │
│  │  管理器     │  │   管理器     │  │   注册表       │ │
│  │  (4 层)     │  │  (异步 I/O)  │  │   (13 个工具)  │ │
│  └─────────────┘  └──────────────┘  └────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## 核心技术亮点

### 1. 自主 Agent 循环 + 熔断机制
- 多轮工具调用，自动检测终止条件
- **熔断器模式**：检测连续工具调用失败，注入系统级警告打破死循环
- 语言感知错误提示（自动识别中文/英文用户）
- 可配置最大轮数，优雅降级

### 2. 四层上下文压缩策略
| 层级 | 触发条件 | 策略 |
|---|---|---|
| **Tool Snip** | >50% token 预算 | 截断冗长工具结果为首尾行 |
| **LLM Summarize** | >70% token 预算 | LLM 驱动的旧对话摘要 |
| **Hard Collapse** | >90% token 预算 | 紧急模式：仅保留摘要 + 最近 4 条消息 |
| **Auto-compact** | >40% + 10k 新 token | 后台守护线程，lock-copy-swap 非阻塞压缩 |

- **Lazy GC 模式**：压缩后立即释放旧消息，避免长会话内存泄漏
- 模型感知 Token 计数（Qwen tokenizer → tiktoken → 启发式回退）

### 3. 并行工具执行 + 安全隔离
- **只读工具**（read_file、grep、glob、symbols）通过 ThreadPoolExecutor 并发执行
- **写操作工具**（write_file、edit_file、bash）顺序执行，防止竞态条件
- 结果按原始调用顺序返回，确保消息配对正确

### 4. 多 Agent 编排
- 子 Agent 生成，上下文隔离
- 父子 Agent 通过工具系统通信
- 每个 Agent 可配置独立工作目录和虚拟环境

### 5. 可插拔工具系统（13 个内置工具）
| 类别 | 工具 |
|---|---|
| **文件 I/O** | read_file, write_file, edit_file, delete_file |
| **搜索** | glob, grep, project_structure, get_file_symbols |
| **Shell** | bash（支持工作目录和虚拟环境） |
| **网络** | web_search, fetch_url |
| **Agent** | agent（子 Agent）, todo_write |

每个工具继承 `Tool` 基类，实现 `name`、`parameters`、`execute()` 即可扩展。基类提供输入验证、权限检查、安全异常捕获等内置能力。

### 6. 实时会话持久化
- 异步会话保存，区分关键/非关键优先级
- Fire-and-forget 模式：主循环永不阻塞在磁盘 I/O 上
- 会话恢复，保留完整对话历史、模型配置和错误历史
- 恢复时显示会话健康状态（API 错误、工具崩溃、LLM 解析错误）

### 7. 流式 LLM 客户端
- OpenAI 兼容 API，自动重试（指数退避）
- Token 用量追踪（每会话 input/output/total）
- Debug 模式：Rich 面板可视化展示工具执行过程

### 8. 虚拟环境支持
- 自动检测工作目录下的 `.venv` / `venv` / `env`
- `--venv` 参数手动指定，`/venv` 命令运行时切换
- bash 工具自动激活虚拟环境

### 9. 错误分析系统
- 按类型分类：API 错误、工具崩溃、LLM 解析错误
- 按工具分类：定位最容易出错的工具
- `/errors` 命令输出详细报告，包含每条错误的轮次和上下文

## 项目结构

```
lazycatcoder/
├── agent.py              Agent 循环 + 并行执行 + 熔断器            610 行
├── context.py            4 层压缩 + Lazy GC + Token 计数           338 行
├── llm.py                流式客户端 + 重试 + Token 追踪            222 行
├── cli.py                REPL + 斜杠命令 + 会话管理                363 行
├── session.py            异步会话持久化 + 错误历史                  429 行
├── prompt.py             动态系统提示词生成                          32 行
├── config.py             环境变量配置 + 虚拟环境检测                 56 行
├── tokenize/             自定义 Tokenizer（Qwen 模型支持）
│   ├── custom_tokenizer.py
│   ├── tokenizer_base.py
│   └── stopwords.py
└── tools/
    ├── __init__.py       工具注册表 + 全局实例                      94 行
    ├── base.py           工具抽象基类 + ToolResult + safe_execute  118 行
    ├── bash.py           Shell 执行 + 工作目录 + 虚拟环境支持       272 行
    ├── edit.py           搜索替换 + 唯一匹配安全机制                 366 行
    ├── read.py           文件读取 + 偏移量/限制                      146 行
    ├── write.py          文件写入 + 自动创建目录                     172 行
    ├── delete.py         文件删除 + 沙箱保护                          75 行
    ├── glob_tool.py      文件模式匹配                                134 行
    ├── grep.py           正则内容搜索                                171 行
    ├── symbols.py        Python AST 符号提取                         127 行
    ├── project_structure.py  目录树可视化                            126 行
    ├── agent.py          子 Agent 生成                                53 行
    ├── todo.py           任务追踪                                    131 行
    ├── web_search.py     网络搜索集成                                246 行
    └── fetch.py          URL 内容获取                                294 行

article/                   架构解析文档（7 篇）
eval/                      评测系统（20 个测试用例 + runner + evaluator）
examples/                  示例脚本
tests/                     单元测试（48 个用例）
```

## 快速开始

```bash
pip install lazycatcoder

# 交互模式
lazycatcoder -m kimi-k2.5

# 单次模式
lazycatcoder -p "找出项目中所有 TODO 注释"

# 指定工作目录
lazycatcoder --workdir /path/to/project -m gpt-4o

# 指定虚拟环境
lazycatcoder --venv /path/to/.venv -m gpt-4o

# 恢复已保存的会话
lazycatcoder -r <session-id>
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `NANOCODER_MODEL` | 模型名称 | `gpt-4o` |
| `OPENAI_API_KEY` / `NANOCODER_API_KEY` / `DEEPSEEK_API_KEY` | API 密钥 | — |
| `OPENAI_BASE_URL` / `NANOCODER_BASE_URL` | API 基础 URL | — |
| `NANOCODER_MAX_TOKENS` | 单次响应上限 | `16384` |
| `NANOCODER_MAX_CONTEXT` | 上下文窗口大小 | `200000` |
| `NANOCODER_TIMEOUT` | 请求超时（秒） | `120` |
| `NANOCODER_TEMPERATURE` | 采样温度 | `0` |
| `NANOCODER_VENV` | 虚拟环境路径 | 自动检测 |
| `NANOCODER_DEBUG` | 调试模式 | `false` |

## 作为库使用

```python
from lazycatcoder import Agent, LLM

llm = LLM(model="kimi-k2.5", api_key="your-key", base_url="https://api.moonshot.ai/v1")
agent = Agent(llm=llm, workdir="/path/to/project", debug=True)
response = agent.chat("找出项目里所有 TODO 注释并列出来")
```

## 自定义工具

```python
from lazycatcoder.tools.base import Tool, ToolResult

class HttpTool(Tool):
    name = "http"
    description = "请求一个 URL。"
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}

    def execute(self, url: str) -> ToolResult:
        import urllib.request
        content = urllib.request.urlopen(url).read().decode()[:5000]
        return ToolResult.ok(content)
```

## REPL 命令

```
/help              显示帮助
/model <名称>      切换模型
/timeout <秒>      设置请求超时
/workdir [目录]    查看或切换工作目录
/venv [路径]       查看或设置虚拟环境
/tokens            查看 token 用量（总计）
/usage             详细用量报告（每轮平均、上下文百分比、错误统计）
/errors            错误分析报告（按类型、按工具、详情）
/compact           手动压缩上下文
/save              保存会话
/sessions          列出已保存的会话
/reset             保存并清空对话历史
/cancel            取消当前工具执行
/debug             切换调试模式
quit               退出
```

## 测试覆盖

48 个测试用例，覆盖核心逻辑、全部 13 个工具、安全机制和边界情况：

```bash
python -m pytest tests/ -v
# 48 passed in ~3s
```

## 评测系统

内置 20 个评测用例的自动化评测框架，覆盖 bug 修复、重构、安全防护、并发处理等场景：

```bash
python -m eval.run
```

## License

MIT。Fork，然后拿去造更好的东西，如果能标注此出处就更好了。
