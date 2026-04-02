# 第五篇：边想边做

人类程序员在 debug 的时候不是"先想好所有步骤，再一步步执行"。更常见的模式是：看到一个报错，脑子里还在想下一步，手已经开始打开相关文件了。思考和行动是重叠的。

Claude Code 的 `StreamingToolExecutor`（530 行）让 AI Agent 也能这样工作。

## 传统模式 vs 流式模式

大多数 Agent 框架的执行模式是这样的：

```
LLM 开始生成 ──────────────────────► LLM 生成完毕
                                         │
                                         ▼
                                    解析 tool_calls
                                         │
                                         ▼
                                    执行 tool_1  →  执行 tool_2  →  执行 tool_3
```

问题显而易见：从 LLM 开始生成到第一个工具开始执行，中间有一大段等待时间。尤其是当 LLM 要返回多个工具调用时，整个响应可能需要几秒甚至十几秒才生成完。

Claude Code 的做法：

```
LLM 开始生成 ─────────────────────────────────► LLM 生成完毕
     │              │              │
     ▼              ▼              ▼
  tool_1 参数     tool_2 参数     tool_3 参数
  完整了          完整了          完整了
     │              │              │
     ▼              ▼              ▼
  开始执行        开始执行        开始执行
  tool_1          tool_2          tool_3
```

每个工具的参数 JSON 一收集完（通过流式事件中的 `content_block_stop`），就立刻开始执行。不等其他工具，也不等 LLM 说完。

## 实现细节

`StreamingToolExecutor` 的核心是一个事件驱动的状态机。它监听 Anthropic 的 Server-Sent Events 流，追踪每个 content block 的状态：

```typescript
// 概念性简化
class StreamingToolExecutor {
  private pendingBlocks: Map<number, PartialToolUse>  // 还在接收参数的工具
  private runningTools: Promise<ToolResult>[]          // 已经在跑的工具

  async onStreamEvent(event: StreamEvent) {
    switch (event.type) {
      case 'content_block_start':
        if (event.content_block.type === 'tool_use') {
          // 新工具调用开始，创建追踪条目
          this.pendingBlocks.set(event.index, {
            id: event.content_block.id,
            name: event.content_block.name,
            inputJson: '',
          })
        }
        break

      case 'content_block_delta':
        if (event.delta.type === 'input_json_delta') {
          // 工具参数 JSON 还在到来，拼接
          this.pendingBlocks.get(event.index)!.inputJson += event.delta.partial_json
        }
        break

      case 'content_block_stop':
        const block = this.pendingBlocks.get(event.index)
        if (block?.type === 'tool_use') {
          // 参数完整了，解析 JSON，立即执行
          const input = JSON.parse(block.inputJson)
          const resultPromise = this.executeTool(block.name, input)
          this.runningTools.push(resultPromise)
          this.pendingBlocks.delete(event.index)
        }
        break
    }
  }

  async awaitAll(): Promise<ToolResult[]> {
    return Promise.all(this.runningTools)
  }
}
```

关键洞察：API 的流式事件中，每个 content block（可能是文本块或工具调用块）有独立的 start/delta/stop 事件。一个工具调用块的 `stop` 事件到来时，这个工具的输入参数就完整了，不需要等其他 content block。

## 性能提升有多大

假设 LLM 返回 3 个工具调用：读文件 A（200ms）、读文件 B（150ms）、运行测试（2000ms）。

**串行模式**：总耗时 = LLM 生成时间 + 200 + 150 + 2000 = LLM + 2350ms

**并行但等生成完**：总耗时 = LLM 生成时间 + max(200, 150, 2000) = LLM + 2000ms

**流式并行**：如果 LLM 生成第一个工具调用的参数用了 1 秒，后续又用了 2 秒生成剩余内容，那么第一个工具在 LLM 还没说完的时候就已经执行完了。总耗时 ≈ LLM 生成时间（因为 2000ms 的测试在 LLM 生成期间就开始跑了）。

在工具执行时间较长的场景（运行测试、编译项目、网络请求），流式执行可以显著降低端到端延迟。

## NanoCoder 的简化实现

NanoCoder 没有实现完整的流式工具解析——OpenAI 兼容 API 的流式事件格式和 Anthropic 不完全一样，而且实现复杂度会大幅增加。

我选择了一个折中方案：等所有 tool_calls 返回之后，用 `concurrent.futures.ThreadPoolExecutor` 并行执行。这覆盖了"多工具并行"的性能收益，只是损失了"LLM 还在生成时就开始执行"这部分的时间节省。

对于大多数场景（尤其是 DeepSeek 和 Qwen 这些响应速度比较快的模型），这个折中是值得的。

---

> 本文是 [Claude Code 源码导读](00-index.md) 系列第 5 篇。配套实现：[NanoCoder](https://github.com/he-yufeng/NanoCoder)
