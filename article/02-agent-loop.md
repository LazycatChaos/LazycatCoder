# 第二篇：1729 行的 while(true)

如果你只看 Claude Code 的一个文件，应该看 `src/query.ts`。

这个文件 1729 行，包含了一个 AI 编程 Agent 的全部核心逻辑：接收用户消息，构造上下文，调用 LLM，解析工具调用，执行工具，把结果喂回 LLM，重复。当 LLM 的回复里不再包含工具调用——只有纯文本——循环结束，结果返回给用户。

每一个 AI Agent 产品——Claude Code、Cursor、Cline、Aider——底层都是这个循环。区别只在于细节处理。而 Claude Code 的细节处理，是我见过的最周全的。

## 循环的骨架

剥掉错误处理、日志、Feature Flag 检查之后，核心循环大致长这样：

```typescript
// src/query.ts (简化版)
while (true) {
  // 1. 构造消息列表：系统提示词 + 历史对话 + 用户输入
  const messages = buildMessages(systemPrompt, history, userInput)

  // 2. 调用 Anthropic API（流式）
  const stream = await apiClient.createMessage({
    model, messages, tools, system, max_tokens, ...
  })

  // 3. 收集响应
  const response = await processStream(stream)

  // 4. 如果有工具调用，执行它们
  if (response.toolUses.length > 0) {
    const results = await executeTools(response.toolUses)
    history.push(response.assistantMessage)
    history.push(...results.map(toToolResultMessage))
    continue  // 回到循环顶部，让 LLM 看到工具结果
  }

  // 5. 没有工具调用 = LLM 说完了
  history.push(response.assistantMessage)
  return response.text
}
```

看起来很简单，对吧？但 Claude Code 在这个骨架上加了大量的防御性逻辑。

## 细节一：流式工具解析

大多数 Agent 框架是等 LLM 完整输出之后再解析工具调用。Claude Code 不是。

`StreamingToolExecutor`（530 行，独立文件）监听 API 的流式响应。每个 token 到达时，它检查是否已经收到了一个完整的工具调用 JSON。如果是，**立即开始执行这个工具**，即使 LLM 还在生成后面的内容。

```typescript
// src/services/tools/StreamingToolExecutor.ts (概念性伪码)
for await (const event of stream) {
  if (event.type === 'content_block_start' && event.content_block.type === 'tool_use') {
    // 新工具调用开始，创建一个 Promise 追踪它
    const toolPromise = waitForCompleteInput(event.index)
    pendingTools.set(event.index, toolPromise)
  }

  if (isToolInputComplete(event.index)) {
    // 输入参数收集完毕，立即开始执行（不等其他工具或文本完成）
    const result = executeToolInBackground(event.index)
    runningTools.push(result)
  }
}

// 流结束后，等待所有还在跑的工具
await Promise.all(runningTools)
```

这意味着如果 LLM 一次返回了三个工具调用（比如同时读三个文件），这三个读操作是**并行执行**的。用户感知到的延迟是最慢那个工具的耗时，而不是三个工具耗时之和。

NanoCoder 用了一个简化版本：不在流中解析，而是等全部工具调用返回后，用 ThreadPool 并行执行。效果接近，实现只需十几行。

## 细节二：上下文构建

每次循环迭代，`buildMessages` 不是简单地把所有历史消息拼起来。它做了大量工作：

1. **系统提示词动态组装**。`src/constants/prompts.ts`（914 行）根据当前环境（操作系统、工作目录、Git 状态、可用工具列表、用户自定义指令）拼接系统提示词。每次循环迭代的系统提示词可能不一样——比如用户中途切换了目录。

2. **token 预算计算**。系统提示词、工具 schema、历史消息各自占多少 token，留给 LLM 回复的空间还有多少——这些都要算。如果历史太长，触发压缩（详见第四篇）。

3. **工具结果缓存检查**。如果某个工具结果引用了磁盘上的文件（大结果会先写盘，只留摘要在上下文里），在构建消息时检查文件是否还在。

## 细节三：错误恢复

生产系统不能因为一次 API 超时就崩溃。`query.ts` 里的错误处理涵盖了：

- **API 限流（429）**：指数退避重试，最多 5 次。
- **上下文过长（400/413）**：自动触发压缩，裁掉旧消息，重试。
- **模型不可用（529）**：切换到 fallbackModel。
- **用户中断（Ctrl+C）**：正在执行的工具被 abort，已完成的工具结果保留在历史中。
- **工具执行异常**：捕获，包装成错误消息，喂回 LLM 让它自己决定如何处理。

最后一点尤其重要。大多数 Agent 框架遇到工具异常会直接抛给用户。Claude Code 的做法是让 LLM 看到错误信息并自行调整策略。这正是"agentic"的核心含义：Agent 遇到问题时自己想办法，而不是转头问人。

## 细节四：轮次预算

`QueryEngine` 有一个 `maxTurns` 配置。每完成一轮工具调用算一个 turn。超过上限就强制停止，避免 LLM 陷入无限循环（比如反复读同一个文件）。

还有 `maxBudgetUsd`——美元预算上限。每次 API 调用的 token 消耗会被换算成费用，累计超过预算就停。这个功能在 Claude Code 的 SDK 模式下特别有用：你可以限制一个自动化任务最多花多少钱。

## NanoCoder 的对应实现

NanoCoder 的 `agent.py` 是这个循环的 Python 版本。核心逻辑 80 行左右。我保留了：

- 工具循环（`for _ in range(max_rounds)` 替代 `while(true)` + `maxTurns`）
- 并行工具执行（ThreadPoolExecutor）
- 上下文压缩触发点
- 工具异常捕获并喂回 LLM

砍掉了：API 重试（留给上层处理）、美元预算（简化版不需要）、流式工具解析（等全部 tool_calls 返回再并行）。

这 80 行代码和 Claude Code 的 1729 行代码干的是同一件事。差别主要在错误处理的粒度和 edge case 的覆盖。

---

> 本文是 [Claude Code 源码导读](00-index.md) 系列第 2 篇。配套实现：[NanoCoder](https://github.com/he-yufeng/NanoCoder)
