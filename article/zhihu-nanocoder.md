# 知乎文章草稿 1：NanoCoder 引流

**标题**：分析完 Claude Code 51 万行源码后，我用 1300 行 Python 重写了它的核心

---

上一篇文章发出来之后，评论区问得最多的两个问题：

1. "所以 Claude Code 的核心到底怎么实现的？能不能用 Python 写一个？"
2. "国内用不了 Anthropic 的 API，有没有支持 DeepSeek/Qwen 的替代品？"

我把这两个问题合在一起回答了：**NanoCoder**，一个 1300 行的 Python 实现，从 Claude Code 51 万行源码中提炼出来的核心架构。支持任何 OpenAI 兼容的大模型。

GitHub：https://github.com/he-yufeng/NanoCoder

## 不是又一个 Claude Code 克隆

市面上已经有了 Claw-Code（10 万+ star 的完整重写）、Aider、Cline 等等。NanoCoder 走的是另一条路。

类比一下：如果你想学 GPT 的训练过程，你不会去读 Megatron-LM 的几十万行代码，你会先看 Andrej Karpathy 的 nanoGPT——300 行代码把核心讲清楚。

NanoCoder 对 AI 编程 Agent 的意义就是 nanoGPT 对 Transformer 的意义。它不是一个产品，而是一份可运行的参考实现。

## 从源码里提炼了什么

读完整个代码库之后，我觉得 Claude Code 里真正重要的设计模式就这几个：

**搜索替换式编辑。** 不用行号补丁（容易错位），不整文件重写（浪费 token）。LLM 给出一段要找的精确文本和替换内容，文本在文件里必须唯一。就这一个约束，干掉了一整类编辑 bug。NanoCoder 实现了这个模式，每次编辑后还输出 unified diff。

**Agent 工具循环 + 并行执行。** 用户输入 → LLM 返回工具调用 → 工具并行执行 → 结果喂回 LLM → 重复。Claude Code 的 StreamingToolExecutor 可以在 LLM 还没说完的时候就开始执行工具。NanoCoder 用 ThreadPool 做了简化版的并行执行。

**三层上下文压缩。** 128K token 听起来很多，但十几轮工具调用后就快满了。Claude Code 用四层策略（裁剪工具输出 → LLM 摘要 → 硬压缩 → 后台自动压缩）。NanoCoder 实现了前三层。

**子代理生成。** 复杂任务拆成子任务，每个子任务交给独立 Agent 处理，各有自己的 128K 上下文。Claude Code 的 AgentTool 有 1397 行，NanoCoder 的是 50 行。

**危险命令拦截。** `rm -rf /`、fork bomb、`curl | bash`，在执行前就拦下来。

## 怎么用

```bash
pip install nanocoder

# 用 DeepSeek
export OPENAI_API_KEY=你的key
export OPENAI_BASE_URL=https://api.deepseek.com
nanocoder -m deepseek-chat
```

然后直接在终端里跟它对话，让它读代码、改代码、跑测试。

也可以当库用：

```python
from nanocoder.agent import Agent
from nanocoder.llm import LLM

llm = LLM(model="deepseek-chat", api_key="sk-...", base_url="https://api.deepseek.com")
agent = Agent(llm=llm)
response = agent.chat("找出所有 TODO 注释并列出来")
```

加自定义工具大概 20 行代码。

## 谁适合用

如果你只是想要一个开箱即用的 AI 编程助手，Claude Code 或者 Cursor 更适合你。

NanoCoder 面向的是：
- 想搞懂 AI 编程 Agent 原理的开发者
- 想自己造轮子的团队（fork 下来就是起点）
- 用国产大模型做编程 Agent 的开发者
- AI Agent 方向的研究者和学生

## 配套导读

除了代码之外，我还写了一套 7 篇的 Claude Code 架构深度导读，从 Agent 循环到工具系统到多 Agent 协作到未发布功能。如果你对"Claude Code 为什么这样设计"感兴趣：

👉 [Claude Code 源码导读系列](https://github.com/he-yufeng/NanoCoder/tree/main/article)

---

GitHub 地址：https://github.com/he-yufeng/NanoCoder

如果觉得有用，给个 Star 呗。
