# 分析完 Claude Code 51万行源码后，我用 1300 行 Python 重写了它的核心

上一篇文章《Claude Code 泄露源码深度分析》发出来之后反响挺大，很多朋友问：分析了这么多，能不能把核心的东西提炼出来，用 Python 重新实现一遍？

所以我花了些时间，把 Claude Code 512,000 行 TypeScript 里真正重要的架构设计提炼出来，用不到 1000 行 Python 重新实现了一个完整可用的 AI 编程 Agent。

项目叫 **NanoCoder**，灵感来自 nanoGPT。就像 nanoGPT 用几百行代码让你看懂 GPT 的训练过程一样，NanoCoder 用 950 行代码让你看懂一个 AI 编程 Agent 的全部核心。

GitHub 地址：`https://github.com/he-yufeng/NanoCoder`

## 它能干什么

NanoCoder 是一个运行在终端里的 AI 编程助手。你告诉它你想做什么，它会自己读代码、改代码、运行命令、观察结果，然后继续下一步，直到任务完成。

比如你可以说"把 main.py 里的所有 print 换成 logging"，它会：
1. 先读取 main.py
2. 找到所有 print 语句
3. 逐个替换成 logging 调用
4. 运行代码确认没问题

跟 Claude Code 的使用体验基本一致，但有个关键区别：**它支持任意大模型**。

## 为什么要做这个

Claude Code 好用，但有两个问题：

**1. 只支持 Anthropic API。** 国内开发者要用的话，API 获取本身就是个门槛。而且现在 DeepSeek、Qwen 这些模型的代码能力已经非常强了，为什么不能用它们来驱动一个编程 Agent？

**2. 源码是 51 万行 TypeScript。** 即使源码泄露了，真正能通读理解的人也不多。大部分人看了我上篇分析文章之后，对架构有了概念，但想自己动手改或者在此基础上做点东西，还是无从下手。

NanoCoder 解决这两个问题：
- 支持任何 OpenAI 兼容 API（DeepSeek、Qwen、Kimi、GLM、Ollama 本地模型都行）
- 950 行代码，一个下午就能通读全部源码

## 从 51 万行里提炼出了什么

分析完 Claude Code 源码之后，我发现真正核心的设计其实就几个：

### 1. Agent 工具循环

这是所有 AI 编程 Agent 的骨架：

```
用户输入 → 调用 LLM（带工具定义）→ LLM 返回工具调用 → 执行工具 → 把结果喂回 LLM → 循环
```

当 LLM 返回纯文本（不再调用工具）时，循环结束，把结果返回给用户。

Claude Code 的核心循环在 `query.ts` 里，1729 行。NanoCoder 的在 `agent.py` 里，80 行。逻辑完全一样。

### 2. 搜索替换式编辑

这是 Claude Code 最巧妙的设计。

传统的代码编辑方式要么是行号补丁（容易错位），要么是整文件重写（浪费 token）。Claude Code 的做法是：LLM 指定一个精确的文本片段（old_string）和替换内容（new_string），要求 old_string 在文件中必须唯一出现。

这个约束非常关键。它把"编辑文件"这个模糊的操作变成了一个确定性的操作：找到唯一的那段文本，替换掉。不会改错地方，也不会遗漏。

NanoCoder 完整实现了这个模式，包括唯一性校验和错误提示。

### 3. 上下文压缩

一个编程任务可能需要几十轮工具调用，每轮都会产生大量的命令输出和文件内容。如果全部保留在对话历史里，很快就会超出模型的上下文限制。

Claude Code 的策略是多层压缩：当接近上限时，自动把旧的对话摘要化。NanoCoder 实现了同样的策略（简化版），在接近 70% 上下文容量时触发压缩。

### 4. 工具输出截断

一个 `find` 命令可能返回几万行结果。全丢给 LLM 不仅浪费 token，还会淹没重要信息。NanoCoder（和 Claude Code 一样）会自动截断过长的工具输出，保留头尾最重要的部分。

## 怎么用

安装：

```bash
pip install nanocoder
```

用 DeepSeek 驱动（国内推荐）：

```bash
export OPENAI_API_KEY=你的key
export OPENAI_BASE_URL=https://api.deepseek.com
nanocoder -m deepseek-chat
```

用本地 Ollama 模型：

```bash
export OPENAI_API_KEY=ollama
export OPENAI_BASE_URL=http://localhost:11434/v1
nanocoder -m qwen2.5-coder
```

然后直接在终端里跟它对话就行。

## 谁适合用

说实话，如果你只是需要一个开箱即用的 AI 编程助手，Claude Code 或者 Cursor 可能更适合你。

NanoCoder 面向的是这几类人：

1. **想搞懂 AI 编程 Agent 原理的开发者。** 950 行代码，每个文件单一职责，读完就懂了。

2. **想自己造轮子的团队。** Fork 下来就是一个完整的起点，在上面加功能比从零开始快得多。比如你可以加 MCP 支持、加权限系统、加多 Agent 协作，基底都搭好了。

3. **用国产大模型做编程 Agent 的开发者。** DeepSeek、Qwen 的代码能力已经很强了，但缺一个好用的 Agent 壳。NanoCoder 就是这个壳。

4. **AI Agent 方向的研究者/学生。** 这可能是你能找到的最小的、完整可运行的编程 Agent 实现了。

## 和其他项目的关系

- **vs Claude Code**：NanoCoder 不是替代品，而是核心架构的参考实现。Claude Code 有完善的 MCP、权限系统、Session 管理等等，NanoCoder 只保留了最核心的 Agent 循环和工具系统。

- **vs Claw-Code**：Claw-Code 是 Claude Code 的完整重实现（Python + Rust，已经 10 万+ star），目标是功能对等。NanoCoder 走的是另一条路：最小化，可理解，可魔改。

- **vs Aider**：Aider 是成熟的 AI 编程工具，有 git 集成等高级特性。NanoCoder 更简单，更适合当作基底来扩展。

## 最后

51 万行代码的精华，浓缩在 950 行里。每一行都有存在的理由，每个设计决策都来自生产级系统的验证。

如果你对 AI Agent 的工作原理感兴趣，花一个小时读完 NanoCoder 的源码，可能比看十篇教程更有收获。

GitHub 地址：`https://github.com/he-yufeng/NanoCoder`

欢迎 Star、Fork、PR。有问题可以在评论区讨论。
