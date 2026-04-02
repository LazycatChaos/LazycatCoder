# Doc 7: 查询引擎与 LLM 交互

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）、Doc 2（构建系统与运行时）、Doc 3（入口点与初始化流程）、Doc 4（终端 UI 系统）、Doc 5（命令系统）、Doc 6（工具系统）

在前六篇文档中，我们理解了 Claude Code 的语言基础、架构全景、构建系统、启动流程、UI 渲染、命令系统和工具系统。命令系统和工具系统定义了 Claude Code **能做什么**，但真正驱动这些能力的核心引擎是**查询引擎（QueryEngine）**。查询引擎是 Claude Code 的"大脑"——它接收用户输入，构造上下文，调用 Anthropic API 获取 Claude 的回复，处理工具调用循环，管理对话历史和 token 预算。如果说工具系统是"手臂"，那么查询引擎就是指挥手臂运动的"中枢神经"。

本文档将深入查询引擎的核心类 `QueryEngine`、查询管道 `query.ts`、以及 API 客户端层的流式调用与重试策略。

---

## 第一章：QueryEngine 核心 src/QueryEngine.ts (1,295 行)

### 1.1 QueryEngine 的定位

`QueryEngine` 是 Claude Code 中查询生命周期和会话状态的**唯一所有者**。每个对话（conversation）对应一个 `QueryEngine` 实例。它的设计文档注释清晰表达了这一定位：

```typescript
// src/QueryEngine.ts:175-183
// QueryEngine 拥有查询生命周期和会话状态的所有权。
// 它将 ask() 中的核心逻辑提取为独立类，可被
// 无头/SDK 路径和（未来阶段的）REPL 共同使用。
//
// 每个对话一个 QueryEngine。每次 submitMessage() 调用
// 在同一对话中开始一个新的"轮次"（turn）。
// 状态（消息、文件缓存、用量等）跨轮次持久化。
export class QueryEngine {
```

这个设计将 **对话状态持久化** 与 **单次查询执行** 明确分离——`QueryEngine` 维护跨轮次的状态，而每次 `submitMessage()` 调用执行一个完整的查询周期。

### 1.2 配置类型 QueryEngineConfig

`QueryEngine` 的所有外部依赖通过 `QueryEngineConfig` 类型注入，这是一个包含 20+ 字段的配置对象：

```typescript
// src/QueryEngine.ts:130-173
export type QueryEngineConfig = {
  cwd: string                            // 工作目录
  tools: Tools                           // 可用工具列表
  commands: Command[]                    // 可用命令列表
  mcpClients: MCPServerConnection[]      // MCP 服务器连接
  agents: AgentDefinition[]              // 智能体定义
  canUseTool: CanUseToolFn               // 工具权限检查函数
  getAppState: () => AppState            // 获取应用状态
  setAppState: (f: (prev: AppState) => AppState) => void  // 更新应用状态
  initialMessages?: Message[]            // 初始对话历史
  readFileCache: FileStateCache          // 文件读取缓存
  customSystemPrompt?: string            // SDK 调用者自定义系统提示词
  appendSystemPrompt?: string            // 追加到系统提示词的内容
  userSpecifiedModel?: string            // 用户指定的模型
  fallbackModel?: string                 // 回退模型（529 错误时使用）
  thinkingConfig?: ThinkingConfig        // 思考模式配置
  maxTurns?: number                      // 最大轮次限制
  maxBudgetUsd?: number                  // USD 预算上限
  taskBudget?: { total: number }         // API 侧 token 预算
  jsonSchema?: Record<string, unknown>   // 结构化输出 JSON schema
  orphanedPermission?: OrphanedPermission // 孤儿权限请求（异步智能体场景）
  snipReplay?: (...)  => ...             // 历史裁剪回调（Feature-Gated: HISTORY_SNIP）
}
```

这个配置类型体现了**依赖注入**原则：`QueryEngine` 不直接导入权限系统、状态管理或工具注册表——所有依赖都通过配置传入，使其可以在不同环境中独立测试。

`snipReplay` 字段的注释（第 158-172 行）特别值得注意：它被设计为回调函数而非直接调用，是为了让 Feature-Gated 的字符串保持在门控模块内部，避免构建时死代码消除问题。

### 1.3 类属性与状态管理

```typescript
// src/QueryEngine.ts:184-198
export class QueryEngine {
  private config: QueryEngineConfig              // 配置实例
  private mutableMessages: Message[]             // 可变对话历史——核心状态
  private abortController: AbortController       // 中断信号控制器
  private permissionDenials: SDKPermissionDenial[] // 权限拒绝追踪（SDK 报告用）
  private totalUsage: NonNullableUsage           // 累计 token 用量（跨轮次）
  private hasHandledOrphanedPermission = false   // 一次性标志：孤儿权限是否已处理
  private readFileState: FileStateCache          // 文件读取状态缓存
  // 轮次作用域的技能发现追踪（feeds was_discovered on
  // tengu_skill_tool_invocation）。必须在 submitMessage 内
  // 两次 processUserInputContext 重建间持久化，但在每次
  // submitMessage 开始时清除以避免 SDK 模式下无限增长。
  private discoveredSkillNames = new Set<string>()
  private loadedNestedMemoryPaths = new Set<string>() // 跨轮次重建时持久化
}
```

这些属性可以分为三类：

| 类别 | 属性 | 生命周期 |
|------|------|----------|
| **会话级状态** | `mutableMessages`, `totalUsage`, `readFileState`, `loadedNestedMemoryPaths` | 跨轮次持久化，整个会话共享 |
| **轮次级状态** | `discoveredSkillNames`, `permissionDenials` | 每次 `submitMessage()` 开始时重置 |
| **一次性标志** | `hasHandledOrphanedPermission` | 整个引擎生命周期只触发一次 |

### 1.4 构造函数

```typescript
// src/QueryEngine.ts:200-207
constructor(config: QueryEngineConfig) {
  this.config = config
  this.mutableMessages = config.initialMessages ?? []  // 初始化对话历史
  this.abortController = config.abortController ?? createAbortController()
  this.permissionDenials = []
  this.readFileState = config.readFileCache  // 共享引用，非克隆
  this.totalUsage = EMPTY_USAGE  // 零值初始化
}
```

构造函数简洁紧凑——没有异步操作，没有副作用，只做状态初始化。`readFileCache` 是**共享引用**而非克隆，意味着外部和引擎内部看到同一份文件缓存，避免同步开销。

### 1.5 核心方法 submitMessage() — 948 行的异步生成器

`submitMessage()` 是整个查询引擎的核心——它是一个 **异步生成器函数**，通过 `yield` 逐步向调用者输出消息：

```typescript
// src/QueryEngine.ts:209-212
async *submitMessage(
  prompt: string | ContentBlockParam[],         // 用户输入（文本或结构化内容块）
  options?: { uuid?: string; isMeta?: boolean }, // 可选：消息 ID 和元消息标记
): AsyncGenerator<SDKMessage, void, unknown> {
```

这个方法长达 948 行（第 209-1156 行），包含以下关键阶段：

**阶段 1：配置解构与轮次初始化（第 213-282 行）**

```typescript
// src/QueryEngine.ts:238-241
this.discoveredSkillNames.clear()   // 清除上一轮的技能发现
setCwd(cwd)                         // 设置工作目录
const persistSession = !isSessionPersistenceDisabled()
const startTime = Date.now()
```

每次 `submitMessage()` 开始时清除 `discoveredSkillNames`，防止 SDK 模式下长期运行导致无限增长。

**阶段 2：权限拒绝包装器（第 244-271 行）**

```typescript
// src/QueryEngine.ts:244-271
// 包装 canUseTool 以追踪权限拒绝
const wrappedCanUseTool: CanUseToolFn = async (
  tool, input, toolUseContext, assistantMessage, toolUseID, forceDecision,
) => {
  const result = await canUseTool(
    tool, input, toolUseContext, assistantMessage, toolUseID, forceDecision,
  )
  // 追踪拒绝以供 SDK 报告
  if (result.behavior !== 'allow') {
    this.permissionDenials.push({
      tool_name: sdkCompatToolName(tool.name),  // 转换为 SDK 兼容名称
      tool_use_id: toolUseID,
      tool_input: input,
    })
  }
  return result
}
```

这是**装饰器模式**的经典应用：不修改原始 `canUseTool` 函数，而是在其外部包装一层追踪逻辑。所有权限拒绝都被记录下来，最终包含在 SDK 结果消息中。

**阶段 3：系统提示词构建（第 284-325 行）**

```typescript
// src/QueryEngine.ts:321-325
// 组装最终系统提示词
const systemPrompt = asSystemPrompt([
  ...(customPrompt !== undefined ? [customPrompt] : defaultSystemPrompt),
  ...(memoryMechanicsPrompt ? [memoryMechanicsPrompt] : []),
  ...(appendSystemPrompt ? [appendSystemPrompt] : []),
])
```

系统提示词的组装遵循**分层合并**：默认系统提示词（或自定义提示词）作为基础层，记忆机制提示词和追加提示词依次叠加。当 SDK 调用者提供了自定义系统提示词并设置了 `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` 环境变量时，会自动注入记忆机制提示词。

**阶段 4：孤儿权限处理（第 397-408 行）**

```typescript
// src/QueryEngine.ts:397-408
// 处理孤儿权限（每个引擎生命周期只执行一次）
if (orphanedPermission && !this.hasHandledOrphanedPermission) {
  this.hasHandledOrphanedPermission = true  // 设置一次性标志
  for await (const message of handleOrphanedPermission(
    orphanedPermission,
    tools,
    this.mutableMessages,
    processUserInputContext,
  )) {
    yield message  // 将处理结果逐条传递给调用者
  }
}
```

"孤儿权限"是指在异步智能体场景中，子智能体请求权限时父智能体已断开的情况。`hasHandledOrphanedPermission` 标志确保这个恢复逻辑在整个引擎生命周期中只执行一次，避免重复处理。

**阶段 5：主查询循环（第 675-1049 行）**

这是 `submitMessage()` 的核心——调用底层 `query()` 函数并处理返回的消息流：

```typescript
// src/QueryEngine.ts:675-680（简化示意）
for await (const message of query({
  messages, systemPrompt, userContext, systemContext,
  canUseTool: wrappedCanUseTool, toolUseContext,
  fallbackModel, querySource, ...
})) {
  // 按消息类型分发处理（第 757-969 行的 switch 语句）
}
```

消息类型分发表：

| 消息类型 | 行号 | 处理逻辑 |
|----------|------|----------|
| `tombstone` | 758-760 | 跳过（控制信号，用于标记无效消息） |
| `assistant` | 761-770 | 捕获 stop_reason，推入对话历史，归一化后 yield |
| `progress` | 771-783 | 推入历史，记录到转录，归一化后 yield |
| `user` | 784-787 | 推入对话历史，归一化后 yield |
| `stream_event` | 788-828 | 更新 token 用量（message_start/delta/stop 三阶段） |
| `attachment` | 829-893 | 处理结构化输出、最大轮次达到、排队命令 |
| `system` | 897-957 | 处理 snip 回放、compact 边界、API 错误 |
| `tool_use_summary` | 959-968 | 传递给 SDK |

**阶段 6：Token 用量追踪（stream_event 处理，第 788-828 行）**

```typescript
// src/QueryEngine.ts:789-815
if (message.event.type === 'message_start') {
  // 重置当前消息用量
  currentMessageUsage = EMPTY_USAGE
  currentMessageUsage = updateUsage(
    currentMessageUsage, message.event.message.usage,
  )
}
if (message.event.type === 'message_delta') {
  // 累加增量用量
  currentMessageUsage = updateUsage(
    currentMessageUsage, message.event.usage,
  )
  if (message.event.delta.stop_reason != null) {
    lastStopReason = message.event.delta.stop_reason
  }
}
if (message.event.type === 'message_stop') {
  // 将当前消息用量累加到总用量
  this.totalUsage = accumulateUsage(
    this.totalUsage, currentMessageUsage,
  )
}
```

Token 用量追踪采用 **三阶段模式**：`message_start` 初始化、`message_delta` 累加、`message_stop` 汇总。这种模式配合流式响应，确保用量数据始终准确。

### 1.6 辅助方法

```typescript
// src/QueryEngine.ts:1158-1176
interrupt(): void {                      // 中断当前查询
  this.abortController.abort()
}

getMessages(): readonly Message[] {      // 返回只读对话历史
  return this.mutableMessages
}

getReadFileState(): FileStateCache {     // 返回文件读取状态
  return this.readFileState
}

getSessionId(): string {                 // 返回当前会话 ID
  return getBootstrapState()?.sessionId ?? ''
}

setModel(model: string): void {          // 更新后续查询使用的模型
  this.config.userSpecifiedModel = model
}
```

`getMessages()` 返回 `readonly Message[]` 类型——通过 TypeScript 类型约束防止外部修改对话历史，但运行时仍是同一数组引用。

### 1.7 ask() 便捷函数

在 `QueryEngine` 类之后（第 1186 行），文件还导出了一个 `ask()` 便捷函数，它是围绕 `QueryEngine` 的封装，为 REPL 主循环提供简化的调用接口。

---

## 第二章：查询管道 src/query.ts (1,729 行)

### 2.1 管道总览

`query.ts` 是查询引擎的**执行管道**——当 `QueryEngine.submitMessage()` 调用 `query()` 函数时，控制权转移到这里。它实现了完整的**智能体循环（agentic loop）**：消息预处理 → API 调用 → 响应解析 → 工具执行 → 结果收集 → 循环继续。

```typescript
// src/query.ts:219-239
export async function* query(
  params: QueryParams,
): AsyncGenerator<
  | StreamEvent         // 流式事件（token 粒度）
  | RequestStartEvent   // 请求开始标记
  | Message             // 完整消息
  | TombstoneMessage    // 墓碑消息（标记已废弃的消息）
  | ToolUseSummaryMessage, // 工具使用摘要
  Terminal              // 返回值：终止原因
> {
  const consumedCommandUuids: string[] = []
  const terminal = yield* queryLoop(params, consumedCommandUuids)
  // 仅在 queryLoop 正常返回时到达此处。
  // throw（错误传播）和 .return()（关闭两个生成器）都会跳过这里。
  for (const uuid of consumedCommandUuids) {
    notifyCommandLifecycle(uuid, 'completed')  // 通知命令生命周期完成
  }
  return terminal
}
```

`query()` 是一个薄封装层——真正的逻辑在 `queryLoop()` 中，长达 1,488 行。`query()` 负责命令生命周期通知，确保只在正常结束时标记命令完成。

### 2.2 QueryParams 类型

```typescript
// src/query.ts:181-199
export type QueryParams = {
  messages: Message[]                    // 当前对话消息列表
  systemPrompt: SystemPrompt            // 系统提示词
  userContext: { [k: string]: string }   // 用户上下文变量
  systemContext: { [k: string]: string } // 系统上下文变量
  canUseTool: CanUseToolFn              // 工具权限检查函数
  toolUseContext: ToolUseContext         // 工具使用上下文（包含工具列表、选项等）
  fallbackModel?: string                // 回退模型
  querySource: QuerySource              // 查询来源标识
  maxOutputTokensOverride?: number      // 输出 token 上限覆盖
  maxTurns?: number                     // 最大轮次
  skipCacheWrite?: boolean              // 跳过缓存写入
  taskBudget?: { total: number }        // API 侧 token 预算
  deps?: QueryDeps                      // 可替换的依赖（测试注入用）
}
```

注意 `deps` 字段——它允许在测试中替换 `callModel`、`autocompact` 等核心依赖，是**控制反转（IoC）**的典型应用。

### 2.3 循环状态机

`queryLoop` 使用一个显式的 `State` 类型管理循环状态：

```typescript
// src/query.ts:204-217
type State = {
  messages: Message[]                      // 当前消息列表
  toolUseContext: ToolUseContext            // 工具使用上下文
  autoCompactTracking: AutoCompactTrackingState | undefined  // 自动压缩追踪
  maxOutputTokensRecoveryCount: number     // max_output_tokens 恢复计数
  hasAttemptedReactiveCompact: boolean     // 是否已尝试反应式压缩
  maxOutputTokensOverride: number | undefined  // 输出 token 覆盖
  pendingToolUseSummary: Promise<...> | undefined  // 待处理的工具使用摘要
  stopHookActive: boolean | undefined      // 停止钩子是否激活
  turnCount: number                        // 当前轮次计数
  // 上一次迭代继续的原因。第一次迭代时为 undefined。
  // 让测试可以断言恢复路径是否触发，而无需检查消息内容。
  transition: Continue | undefined
}
```

所有"继续"站点（continue sites）都重新构造完整的 `State` 对象，而不是修改个别字段——这防止了遗漏某个字段更新的细微 bug：

```typescript
// src/query.ts:1715-1727（下一轮迭代状态更新）
const next: State = {
  messages: [...messagesForQuery, ...assistantMessages, ...toolResults],
  toolUseContext: toolUseContextWithQueryTracking,
  autoCompactTracking: tracking,
  turnCount: nextTurnCount,
  maxOutputTokensRecoveryCount: 0,        // 重置恢复计数
  hasAttemptedReactiveCompact: false,      // 重置压缩标志
  pendingToolUseSummary: nextPendingToolUseSummary,
  maxOutputTokensOverride: undefined,
  stopHookActive,
  transition: { reason: 'next_turn' },   // 标记继续原因
}
state = next
```

`transition.reason` 字段对测试特别有价值——测试可以断言 `reason === 'reactive_compact_retry'` 来验证恢复路径是否被触发，而不需要检查复杂的消息内容。

### 2.4 消息预处理管道

每次循环迭代开始时，消息经历多层预处理：

```
原始消息列表
    │
    ├── getMessagesAfterCompactBoundary()  ─── 提取压缩边界后的消息
    │
    ├── applyToolResultBudget()            ─── 应用工具结果大小上限
    │
    ├── snipCompactIfNeeded()              ─── HISTORY_SNIP: 裁剪长历史
    │
    ├── microcompact()                     ─── 客户端缓存编辑优化
    │
    ├── applyCollapsesIfNeeded()           ─── CONTEXT_COLLAPSE: 上下文折叠
    │
    ├── autocompact()                      ─── 主动压缩（80% token 阈值）
    │
    └── calculateTokenWarningState()       ─── 检查是否达到阻塞限制
```

这个管道的顺序经过精心设计——先裁剪，再压缩，最后检查限制。每一步都是**可选的**，通过 Feature Flag 或运行时条件控制。

### 2.5 API 调用与流式响应处理

```typescript
// src/query.ts:659-707（简化）
for await (const message of deps.callModel({
  messages: prependUserContext(messagesForQuery, userContext),  // 注入用户上下文
  systemPrompt: fullSystemPrompt,                              // 完整系统提示词
  thinkingConfig: toolUseContext.options.thinkingConfig,        // 思考模式配置
  tools: toolUseContext.options.tools,                         // 工具定义列表
  signal: toolUseContext.abortController.signal,               // 中断信号
  options: {
    model: currentModel,                         // 当前模型
    fallbackModel,                               // 回退模型
    querySource,                                 // 查询来源
    agents: toolUseContext.options.agentDefinitions.activeAgents,
    mcpTools: appState.mcp.tools,                // MCP 工具
    effortValue: appState.effortValue,           // 努力级别
    advisorModel: appState.advisorModel,         // 顾问模型
    ...(params.taskBudget && { taskBudget: {...} }),  // API 任务预算
  },
})) {
  // 处理流式响应...
}
```

上下文构建的三个注入点：
1. **系统提示词**：`appendSystemContext(systemPrompt, systemContext)` 将系统上下文合并到系统提示词中
2. **用户上下文**：`prependUserContext(messagesForQuery, userContext)` 将用户上下文变量注入到消息前
3. **工具描述**：通过 `tools` 参数传递工具的 schema 定义

### 2.6 错误扣留与恢复路径

查询管道的一个精妙设计是**错误扣留（error withholding）**——某些可恢复的 API 错误不会立即传递给调用者，而是被"扣留"以尝试自动恢复：

```typescript
// src/query.ts:799-825（简化）
let withheld = false
// 上下文折叠可以处理的 prompt-too-long 错误
if (contextCollapse?.isWithheldPromptTooLong(message, ...)) {
  withheld = true
}
// 反应式压缩可以处理的 prompt-too-long 错误
if (reactiveCompact?.isWithheldPromptTooLong(message)) {
  withheld = true
}
// max_output_tokens 可以通过提升限制恢复
if (isWithheldMaxOutputTokens(message)) {
  withheld = true
}
if (!withheld) {
  yield yieldMessage  // 只有不可恢复的消息才传递出去
}
```

恢复路径按优先级排列：

| 优先级 | 恢复策略 | 触发条件 | 行号 |
|--------|---------|---------|------|
| 1 | 上下文折叠排空 | prompt-too-long + 有待折叠的上下文 | 1094 |
| 2 | 反应式压缩 | prompt-too-long + 尚未尝试过 | 1119-1166 |
| 3 | max_output_tokens 提升 | stop_reason=max_tokens，8K→64K | 1199-1222 |
| 4 | max_output_tokens 恢复循环 | 持续 max_tokens，最多 3 次 | 1223-1252 |

### 2.7 工具执行：流式与批量

查询管道支持两种工具执行模式：

```typescript
// src/query.ts:1366-1382
// 流式执行器在模型生成时并行执行工具
const toolUpdates = streamingToolExecutor
  ? streamingToolExecutor.getRemainingResults()   // 流式：获取剩余结果
  : runTools(toolUseBlocks, assistantMessages,     // 批量：一次性执行所有工具
      canUseTool, toolUseContext)
```

**流式工具执行器（StreamingToolExecutor）** 是一个性能优化——当模型在生成第二个工具调用时，第一个工具已经开始执行了。这种流水线并行显著减少了多工具场景的总延迟。

### 2.8 记忆与附件注入

在工具执行完成后、进入下一轮循环前，管道会注入记忆和附件：

```typescript
// src/query.ts:1580-1614（简化）
// 获取附件消息（CLAUDE.md 记忆、技能发现结果、排队命令）
for await (const attachment of getAttachmentMessages(
  null, updatedToolUseContext, null, queuedCommandsSnapshot,
  [...messagesForQuery, ...assistantMessages, ...toolResults],
  querySource,
)) {
  yield attachment
  toolResults.push(attachment)  // 附件作为"工具结果"进入下一轮
}

// 消费记忆预取结果（在模型流式响应期间并行获取）
if (pendingMemoryPrefetch?.settledAt !== null &&
    pendingMemoryPrefetch.consumedOnIteration === -1) {
  const memoryAttachments = filterDuplicateMemoryAttachments(
    await pendingMemoryPrefetch.promise,
    toolUseContext.readFileState,         // 去重：跳过已读文件
  )
  for (const memAttachment of memoryAttachments) {
    const msg = createAttachmentMessage(memAttachment)
    yield msg
    toolResults.push(msg)
  }
  pendingMemoryPrefetch.consumedOnIteration = turnCount - 1
}
```

记忆预取（`startRelevantMemoryPrefetch`）在 API 调用**开始时**就启动了（第 301 行），利用模型 5-30 秒的响应时间窗口并行获取相关记忆文件。预取结果通过 `readFileState` 去重，避免重复注入已经通过 FileReadTool 读取过的文件。

---

## 第三章：API 客户端 src/services/api/claude.ts (3,419 行)

### 3.1 客户端架构概览

`claude.ts` 是 Claude Code 与 Anthropic API 之间的**唯一通信层**。它封装了消息流式传输、参数构建、错误处理、重试策略、token 计数和缓存管理等所有 API 交互细节。

核心函数层次：

```
queryModelWithStreaming()  ── 公开接口（流式，返回 AsyncGenerator）
queryModelWithoutStreaming() ── 公开接口（非流式，返回 Promise）
        │
        └── queryModel()  ── 私有实现（3,419 行中的 1,875 行）
                │
                ├── paramsFromContext()  ── 构建 API 请求参数
                ├── withRetry()         ── 重试包装器
                └── 流式事件处理循环     ── 解析 SSE 事件
```

### 3.2 主查询函数 queryModel()

```typescript
// src/services/api/claude.ts:1017-1027
async function* queryModel(
  messages: Message[],
  systemPrompt: SystemPrompt,
  thinkingConfig: ThinkingConfig,
  tools: Tools,
  signal: AbortSignal,
  options: Options,
): AsyncGenerator<
  StreamEvent | AssistantMessage | SystemAPIErrorMessage,
  void
> {
```

函数入口处有一个**关闸检查（off-switch）**——通过 GrowthBook 远程配置可以动态关闭对特定模型的访问：

```typescript
// src/services/api/claude.ts:1031-1049
// 先检查廉价条件——关闭开关的 await 会阻塞 GrowthBook 初始化（~10ms）。
// 对非 Opus 模型（haiku, sonnet）完全跳过这个 await。
// 订阅者根本不走这条路径。
if (
  !isClaudeAISubscriber() &&
  isNonCustomOpusModel(options.model) &&
  (await getDynamicConfig_BLOCKS_ON_INIT('tengu-off-switch', { activated: false }))
    .activated
) {
  logEvent('tengu_off_switch_query', {})
  yield getAssistantMessageFromError(new Error(CUSTOM_OFF_SWITCH_MESSAGE), options.model)
  return
}
```

这个检查的顺序经过优化：先检查是否是订阅者（内存读取），再检查模型类型（字符串比较），最后才做异步的 GrowthBook 查询——避免对大多数用户产生不必要的延迟。

### 3.3 API 参数构建 paramsFromContext()

`paramsFromContext()` 是一个闭包函数（第 1538 行），负责将所有上下文组装成 API 请求参数：

```typescript
// src/services/api/claude.ts:1538-1560（关键片段）
const paramsFromContext = (retryContext: RetryContext) => {
  const betasParams = [...betas]
  // ... 动态添加 beta headers ...
  const extraBodyParams = getExtraBodyParams(bedrockBetas)
  const outputConfig: BetaOutputConfig = { ... }

  // 配置努力级别参数
  configureEffortParams(effort, outputConfig, extraBodyParams, betasParams, options.model)
  // 配置任务预算参数
  configureTaskBudgetParams(options.taskBudget, outputConfig, betasParams)
```

最终生成的请求参数结构：

```
API 请求参数
├── model: normalizeModelStringForAPI(options.model)  ── 规范化模型名称
├── messages: addCacheBreakpoints(...)                ── 带缓存标记的消息
├── system: buildSystemPromptBlocks(...)              ── 分块系统提示词
├── tools: allTools                                   ── 工具 schema 列表
├── tool_choice: options.toolChoice                   ── 工具选择策略
├── betas: betasParams                                ── Beta 功能 headers
├── metadata: getAPIMetadata()                        ── 用户/设备/会话 ID
├── max_tokens: maxOutputTokens                       ── 输出 token 上限
├── thinking: { type: 'adaptive' | 'enabled', ... }   ── 思考模式
├── temperature: temperature                          ── 温度参数
├── output_config: { effort?, format?, task_budget? }  ── 输出配置
├── speed: 'fast'?                                    ── 快速模式
└── ...extraBodyParams                                ── 自定义扩展参数
```

**Thinking 配置**的选择逻辑（第 1596-1630 行）尤其值得注意：

```typescript
// src/services/api/claude.ts:1604-1618
// 重要：不要在未通知模型发布 DRI 和研究团队的情况下
// 更改下面的 adaptive vs budget 思考选择。
// 这是一个敏感设置，会极大影响模型质量和 bashing。
if (modelSupportsAdaptiveThinking(options.model)) {
  // 支持自适应思考的模型，始终使用无预算的自适应思考
  thinking = { type: 'adaptive' }
} else {
  // 不支持自适应思考的模型，使用默认思考预算
  let thinkingBudget = getMaxThinkingTokensForModel(options.model)
  // ... 预算计算 ...
}
```

注释中的"不要更改"警告反映了这个参数对模型行为的巨大影响——这是一个需要跨团队协调的设置。

### 3.4 流式传输实现

Claude Code 使用**原始流（Raw Stream）**而非 SDK 提供的高级流包装器：

```typescript
// src/services/api/claude.ts:1818-1836
// 使用原始流而非 BetaMessageStream 以避免 O(n²) 的部分 JSON 解析
// BetaMessageStream 在每个 input_json_delta 上调用 partialParse()，
// 但我们自己处理工具输入累积，所以不需要它
const result = await anthropic.beta.messages
  .create(
    { ...params, stream: true },
    {
      signal,
      ...(clientRequestId && {
        headers: { [CLIENT_REQUEST_ID_HEADER]: clientRequestId },
      }),
    },
  )
  .withResponse()
```

选择原始流的原因是性能：SDK 的 `BetaMessageStream` 在每个 `input_json_delta` 事件上调用部分 JSON 解析器，这在长工具输入（如大段代码）时会产生 O(n²) 的复杂度。Claude Code 自行管理工具输入的累积，避免了这个问题。

**流式空闲看门狗（第 1868-1927 行）**：

系统使用一个 90 秒的看门狗定时器来检测"静默断开"——当 TCP 连接被中间代理悄悄关闭但客户端未收到 FIN 包时，看门狗会主动中断并触发重试：

```
STREAM_IDLE_TIMEOUT_MS = 90 秒
    │
    ├── 每收到一个 SSE 事件 → 重置定时器
    ├── 90 秒无事件 → 触发 abort
    └── 记录日志事件 tengu_streaming_idle_timeout
```

**流式事件处理循环（第 1940-2304 行）** 按事件类型分派：

| SSE 事件类型 | 处理逻辑 |
|-------------|----------|
| `message_start` | 初始化消息，捕获初始 usage |
| `content_block_start` | 初始化 text/tool_use/thinking 内容块 |
| `content_block_delta` | 累积文本、思考内容、工具输入 JSON |
| `content_block_stop` | 完成内容块，yield AssistantMessage |
| `message_delta` | 更新 usage 和 stop_reason，计算费用 |

### 3.5 Token 用量追踪与费用计算

token 用量通过两个核心函数管理：

```typescript
// src/services/api/claude.ts:2924-2987
export function updateUsage(
  usage: Readonly<NonNullableUsage>,
  partUsage: BetaMessageDeltaUsage | undefined,
): NonNullableUsage {
  if (!partUsage) return { ...usage }
  return {
    input_tokens:                          // 输入 token（> 0 守卫防止覆写）
      partUsage.input_tokens !== null && partUsage.input_tokens > 0
        ? partUsage.input_tokens : usage.input_tokens,
    cache_creation_input_tokens:           // 缓存创建 token
      partUsage.cache_creation_input_tokens !== null &&
      partUsage.cache_creation_input_tokens > 0
        ? partUsage.cache_creation_input_tokens
        : usage.cache_creation_input_tokens,
    cache_read_input_tokens:               // 缓存读取 token
      partUsage.cache_read_input_tokens !== null &&
      partUsage.cache_read_input_tokens > 0
        ? partUsage.cache_read_input_tokens
        : usage.cache_read_input_tokens,
    output_tokens: partUsage.output_tokens ?? usage.output_tokens,
    server_tool_use: { ... },              // 服务器端工具使用（web 搜索/获取请求数）
    // ... 其他字段
  }
}
```

注意 `> 0` 守卫——`message_delta` 事件可能包含值为 0 的 token 字段，如果直接覆写会丢失 `message_start` 中设置的真实值。这是一个细致的**防御性编程**模式。

```typescript
// src/services/api/claude.ts:2993-3003
export function accumulateUsage(
  totalUsage: Readonly<NonNullableUsage>,
  messageUsage: Readonly<NonNullableUsage>,
): NonNullableUsage {
  return {
    input_tokens: totalUsage.input_tokens + messageUsage.input_tokens,
    cache_creation_input_tokens:
      totalUsage.cache_creation_input_tokens +
      messageUsage.cache_creation_input_tokens,
    // ... 所有字段做加法累积
  }
}
```

`updateUsage` 用于流内更新（取最新值），`accumulateUsage` 用于跨消息汇总（做加法）。这两个函数清晰地区分了"覆写"和"累加"语义。

费用计算通过 `calculateUSDCost()` 和 `addToTotalSessionCost()` 完成，在每个 `message_delta` 事件中实时更新：

```typescript
// src/services/api/claude.ts:2250-2256
const costUSDForPart = calculateUSDCost(resolvedModel, usage)
costUSD += addToTotalSessionCost(costUSDForPart, ...)
```

### 3.6 提示词缓存系统

Claude Code 使用**提示词缓存（Prompt Caching）**来减少重复发送相同内容的费用和延迟：

```typescript
// src/services/api/claude.ts:358-374
export function getCacheControl({
  scope, querySource,
} = {}): {
  type: 'ephemeral'           // 缓存类型：临时
  ttl?: '1h'                  // 可选：1 小时 TTL
  scope?: CacheScope           // 可选：全局作用域（跨会话）
} {
  return {
    type: 'ephemeral',
    ...(should1hCacheTTL(querySource) && { ttl: '1h' }),   // 符合条件则使用 1 小时
    ...(scope === 'global' && { scope }),                  // 全局作用域
  }
}
```

缓存配置有三个层次：
1. **全局禁用**：`DISABLE_PROMPT_CACHING` 环境变量
2. **模型级禁用**：`DISABLE_PROMPT_CACHING_HAIKU/SONNET/OPUS`
3. **1 小时 TTL 资格**：需要是 Anthropic 员工或付费订阅用户

缓存标记（breakpoints）的放置策略（第 3063-3145 行）：每个请求恰好一个缓存标记，放在最后一条消息上（正常流程）或倒数第二条消息上（fire-and-forget fork 场景）。

### 3.7 流式回退机制

当流式传输遇到错误时，系统会自动回退到非流式模式：

```typescript
// src/services/api/claude.ts:2404-2569（简化）
catch (streamingError) {
  clearStreamIdleTimers()
  // 区分用户主动中断 vs 系统错误
  if (error instanceof APIUserAbortError) {
    // 用户按了 Escape——不要回退
    throw error
  }
  // 系统错误——尝试非流式回退
  const result = yield* executeNonStreamingRequest(
    { model: options.model, source: options.querySource },
    { model: options.model, fallbackModel: options.fallbackModel,
      thinkingConfig, signal },
    paramsFromContext,
    (attempt, _startTime, tokens) => { ... },
    params => captureAPIRequest(params, options.querySource),
    streamRequestId,
  )
}
```

流式回退只在**系统错误**时触发——用户按 Escape 键产生的 `APIUserAbortError` 不会触发回退。回退使用 `executeNonStreamingRequest()` 发起一次完整的同步请求。

### 3.8 重试策略 src/services/api/withRetry.ts

`withRetry()` 是整个 API 层的**韧性核心**——一个异步生成器，在失败时自动重试并通过 `yield` 报告重试状态：

```typescript
// src/services/api/withRetry.ts:170-178
export async function* withRetry<T>(
  getClient: () => Promise<Anthropic>,              // 客户端工厂（支持刷新凭证）
  operation: (client: Anthropic, attempt: number,    // 要重试的操作
    context: RetryContext) => Promise<T>,
  options: RetryOptions,                            // 重试配置
): AsyncGenerator<SystemAPIErrorMessage, T> {        // yield 错误消息，return 成功结果
```

**退避算法**：

```typescript
// src/services/api/withRetry.ts:530-548
export function getRetryDelay(
  attempt: number,
  retryAfterHeader?: string | null,
  maxDelayMs = 32000,                // 最大延迟 32 秒
): number {
  // 优先使用服务器指定的 Retry-After
  if (retryAfterHeader) {
    const seconds = parseInt(retryAfterHeader, 10)
    if (!isNaN(seconds)) return seconds * 1000
  }
  // 指数退避：500ms × 2^(attempt-1)，上限 32 秒
  const baseDelay = Math.min(
    BASE_DELAY_MS * Math.pow(2, attempt - 1),  // BASE_DELAY_MS = 500
    maxDelayMs,
  )
  // 随机抖动：0-25% 的基础延迟
  const jitter = Math.random() * 0.25 * baseDelay
  return baseDelay + jitter
}
```

退避序列：500ms → 1s → 2s → 4s → 8s → 16s → 32s → 32s → ...（加 0-25% 抖动）。默认最多重试 10 次（`DEFAULT_MAX_RETRIES`），可通过 `CLAUDE_CODE_MAX_RETRIES` 环境变量配置。

**529 错误处理——过载保护**：

```typescript
// src/services/api/withRetry.ts:610-621
export function is529Error(error: unknown): boolean {
  if (!(error instanceof APIError)) return false
  return (
    error.status === 529 ||
    // SDK 在流式传输期间有时会丢失状态码
    error.message?.includes('"type":"overloaded_error"') ?? false
  )
}
```

529 错误检测同时检查**状态码和消息内容**——这是因为 SDK 在流式传输期间有时会丢失状态码（序列化问题）。连续 3 次 529 错误后，系统根据用户类型采取不同策略：

```
连续 529 错误计数器
    │
    ├── < 3: 继续重试（使用退避延迟）
    │
    └── ≥ 3 (MAX_529_RETRIES):
        ├── 有 fallbackModel → 抛出 FallbackTriggeredError（切换模型）
        ├── 外部用户 + 非沙箱 + 非持久化 → 抛出 CannotRetryError
        └── 其他 → 继续重试
```

**FallbackTriggeredError** 携带原始模型和回退模型信息，由上层捕获后用新模型重新发起请求。

**查询来源门控**——不是所有查询都值得重试 529：

```
FOREGROUND_529_RETRY_SOURCES（值得重试）:
├── repl_main_thread      ── 主交互线程
├── agent_*               ── 智能体查询
└── verification          ── 验证查询

非前台来源（立即放弃）:
├── titles                ── 标题生成
├── summaries             ── 摘要生成
└── suggestions           ── 建议生成
```

这个设计的逻辑是：后台生成任务（标题、摘要、建议）在服务过载时可以放弃，而用户正在等待的前台查询则值得重试。

**快速模式回退**：

快速模式（Fast Mode）有自己的回退策略：

| 触发条件 | 行为 | 冷却时间 |
|---------|------|---------|
| 配额超额拒绝 | 禁用快速模式，继续请求 | 永久 |
| 短 retry-after（< 20s） | 等待后仍用快速模式重试 | 无 |
| 长 retry-after（≥ 20s） | 触发冷却，切换到普通模式 | max(retryAfterMs, 10分钟) |
| API 拒绝快速模式参数 | 永久禁用快速模式 | 永久 |

短 retry-after 保留快速模式的原因是**缓存保持**——切换模式会使已建立的提示词缓存失效。

**持久化重试模式**：

```typescript
// src/services/api/withRetry.ts:100-104
function isPersistentRetryEnabled(): boolean {
  return feature('UNATTENDED_RETRY')
    ? isEnvTruthy(process.env.CLAUDE_CODE_UNATTENDED_RETRY)
    : false
}
```

持久化重试模式（通过 `CLAUDE_CODE_UNATTENDED_RETRY=true` 激活）会**无限重试** 429/529 错误，退避上限为 5 分钟，绝对上限 6 小时。它还实现了**心跳机制**——长延迟期间每 30 秒 yield 一条状态消息，防止主机将会话标记为空闲：

```typescript
// src/services/api/withRetry.ts:477-503（简化）
// 对于 > 60 秒的延迟，分成 30 秒间隔的块
while (remaining > 0) {
  yield createSystemAPIErrorMessage(error, remaining, ...)
  const chunk = Math.min(remaining, HEARTBEAT_INTERVAL_MS)  // 30 秒
  await sleep(chunk, options.signal, { abortError })
  remaining -= chunk
}
```

**max_tokens 上下文溢出自动调整**：

```typescript
// src/services/api/withRetry.ts:550-595
export function parseMaxTokensContextOverflowError(error: APIError):
  | { inputTokens: number; maxTokens: number; contextLimit: number }
  | undefined {
  // 解析错误消息："input length and `max_tokens` exceed context limit: 188059 + 20000 > 200000"
  const regex =
    /input length and `max_tokens` exceed context limit: (\d+) \+ (\d+) > (\d+)/
  const match = error.message.match(regex)
  // ... 提取 inputTokens, maxTokens, contextLimit
}
```

当 API 返回 400 "input length and max_tokens exceed context limit" 错误时，`withRetry` 解析错误消息中的数值，自动降低 `max_tokens` 使请求适合上下文窗口限制。这是**防御性编程**与**优雅降级**的结合——系统宁可生成较短的回复，也不让请求完全失败。

### 3.9 请求关联追踪

每个 API 请求通过三个 ID 进行关联追踪：

| ID 类型 | 来源 | 用途 |
|--------|------|------|
| `streamRequestId` | API 响应 header | 标识服务端请求，用于日志关联 |
| `clientRequestId` | 客户端生成（UUID） | 标识客户端请求，用于超时诊断 |
| `previousRequestId` | 上一条 assistant 消息 | 链接请求链，跟踪重试关系 |

这种三级 ID 体系使得从客户端到服务端的完整请求链路可追踪，即使跨越重试和模型回退。

---

## 第四章：工具调用循环（Tool-Call Loop）

### 4.1 循环的核心机制

工具调用循环是 Claude Code 实现**智能体行为**的核心——它让 AI 不仅能"说"，还能"做"。当模型的回复中包含 `tool_use` 类型的内容块时，循环不会结束，而是执行这些工具、收集结果、然后带着结果再次调用 API。这个过程持续进行，直到模型决定不再调用工具（返回纯文本或思考内容），或者触发了某个终止条件。

完整的工具调用循环流程图：

```
用户输入消息
    │
    ▼
┌─────────────────────────────────────────────────────┐
│            queryLoop() 主循环 (while true)            │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  1. 消息预处理管道                                │ │
│  │     ├── getMessagesAfterCompactBoundary()        │ │
│  │     ├── applyToolResultBudget()                  │ │
│  │     ├── snipCompactIfNeeded()                    │ │
│  │     ├── microcompact()                           │ │
│  │     ├── applyCollapsesIfNeeded()                 │ │
│  │     ├── autocompact()                            │ │
│  │     └── calculateTokenWarningState()             │ │
│  └─────────────────────────────────────────────────┘ │
│                     │                                 │
│                     ▼                                 │
│  ┌─────────────────────────────────────────────────┐ │
│  │  2. 调用模型 API (deps.callModel)                │ │
│  │     ├── prependUserContext() 注入用户上下文       │ │
│  │     ├── appendSystemContext() 注入系统上下文      │ │
│  │     └── 流式接收: StreamEvent / AssistantMessage  │ │
│  └─────────────────────────────────────────────────┘ │
│                     │                                 │
│                     ▼                                 │
│  ┌─────────────────────────────────────────────────┐ │
│  │  3. 收集响应                                     │ │
│  │     ├── 收集 assistantMessages[]                  │ │
│  │     ├── 提取 toolUseBlocks[]                     │ │
│  │     └── 判断 needsFollowUp = 有工具调用?          │ │
│  └─────────────────────────────────────────────────┘ │
│                     │                                 │
│              needsFollowUp?                           │
│              ╱          ╲                             │
│           YES            NO                           │
│            │              │                           │
│            ▼              ▼                           │
│  ┌──────────────┐  ┌──────────────────────┐          │
│  │ 4. 执行工具   │  │ 5. 终止条件检查       │          │
│  │   runTools()  │  │   ├── 错误恢复?       │          │
│  │   收集结果    │  │   ├── 停止钩子?       │          │
│  │   生成摘要    │  │   ├── Token 预算?     │          │
│  └──────┬───────┘  │   └── 正常完成        │          │
│         │          └──────────┬───────────┘          │
│         │                    │                       │
│         ▼                    ▼                       │
│  [组合新消息列表]      return { reason }              │
│  state = next                                        │
│  continue ──────────────► 回到步骤 1                  │
└─────────────────────────────────────────────────────┘
```

### 4.2 响应收集与工具提取

在模型的流式响应阶段，`queryLoop` 一边 yield 流式事件给上层，一边收集完整的 assistant 消息和工具调用块：

```typescript
// src/query.ts:827-862（简化）
// 收集助手消息
if (message.type === 'assistant') {
  assistantMessages.push(message)              // 收集完整消息
  // 提取所有 tool_use 类型的内容块
  const blocks = message.message.content.filter(
    (b): b is BetaToolUseBlock => b.type === 'tool_use',
  )
  toolUseBlocks.push(...blocks)                // 累积到工具调用列表
  if (blocks.length > 0) {
    needsFollowUp = true                       // 标记需要继续循环
  }
}
```

**流式工具执行器**在此阶段同步启动——当模型还在生成后续内容时，已提取的工具就开始执行了：

```typescript
// src/query.ts:838-862
// StreamingToolExecutor：在流式响应期间并行执行工具
if (streamingToolExecutor && blocks.length > 0) {
  for (const block of blocks) {
    streamingToolExecutor.startTool(block)     // 立即开始执行，不等流式结束
  }
}
```

这种**流水线并行**策略显著降低了多工具场景的延迟——当模型生成第二个工具调用时，第一个工具可能已经完成了。

### 4.3 循环终止条件

当 `needsFollowUp === false` 时（模型没有请求工具调用），循环进入终止条件检查。这不是简单的"直接返回"——系统需要处理多种边界情况：

**终止条件决策树**：

| 条件 | 处理 | 返回原因 |
|------|------|---------|
| 正常完成（纯文本/思考回复） | 执行停止钩子 → 检查 Token 预算 | `completed` |
| prompt-too-long 被扣留 + 有上下文折叠 | 排空折叠 → 重试 | `continue (collapse_drain_retry)` |
| prompt-too-long 被扣留 + 未尝试反应式压缩 | 触发反应式压缩 → 重试 | `continue (reactive_compact_retry)` |
| prompt-too-long 恢复耗尽 | 表面化错误 | `prompt_too_long` |
| 媒体大小错误（图片/PDF 过大） | 反应式压缩剥离 → 重试 | `continue` 或 `image_error` |
| max_output_tokens + 首次触发 | 提升限制 8K→64K → 重试 | `continue (max_output_tokens_escalate)` |
| max_output_tokens + 已提升 | 注入恢复消息 → 继续（最多 5 次） | `continue (max_output_tokens_recovery)` |
| max_output_tokens 恢复耗尽 | 表面化错误 | `completed` |
| 停止钩子有阻塞错误 | 注入错误消息 → 继续 | `continue (stop_hook_blocking)` |
| Token 预算 < 90% | 注入续写消息 → 继续 | `continue (token_budget_continuation)` |
| Token 预算 ≥ 90% 或递减回报 | 停止 | `completed` |
| API 错误消息（速率限制等） | 跳过停止钩子 | `completed` |

### 4.4 max_output_tokens 恢复机制

当模型回复因达到输出 token 上限而被截断时，系统有一个**两级恢复策略**：

```typescript
// src/query.ts:1185-1256（关键逻辑）
if (isWithheldMaxOutputTokens(lastMessage)) {
  // 第一级：静默提升限制（8K → 64K）
  // 条件：Feature Flag 开启 + 首次触发 + 用户未手动设置
  const capEnabled = getFeatureValue_CACHED_MAY_BE_STALE(
    'tengu_otk_slot_v1', false,       // 通过 GrowthBook 远程控制
  )
  if (capEnabled && maxOutputTokensOverride === undefined &&
      !process.env.CLAUDE_CODE_MAX_OUTPUT_TOKENS) {
    // 用 64K 限制重试同一请求——无用户消息，完全透明
    state = { ...state,
      maxOutputTokensOverride: ESCALATED_MAX_TOKENS,  // 64K
      transition: { reason: 'max_output_tokens_escalate' },
    }
    continue  // 重试
  }

  // 第二级：多轮恢复（最多 5 次）
  if (maxOutputTokensRecoveryCount < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT) {
    const recoveryMessage = createUserMessage({
      content:
        `Output token limit hit. Resume directly — no apology, ` +
        `no recap of what you were doing. Pick up mid-thought ` +
        `if that is where the cut happened. Break remaining work ` +
        `into smaller pieces.`,
      isMeta: true,                  // 元消息：不计入对话历史
    })
    state = { ...state,
      messages: [...messagesForQuery, ...assistantMessages, recoveryMessage],
      maxOutputTokensRecoveryCount: maxOutputTokensRecoveryCount + 1,
      transition: { reason: 'max_output_tokens_recovery',
                    attempt: maxOutputTokensRecoveryCount + 1 },
    }
    continue  // 继续生成
  }
  // 恢复耗尽——表面化被扣留的错误
  yield lastMessage
}
```

恢复消息的措辞经过精心设计——"no apology, no recap"指令防止模型浪费 token 重述已有内容，最大化恢复效率。

### 4.5 工具执行与结果收集

当 `needsFollowUp === true` 时，循环执行工具并收集结果：

```typescript
// src/query.ts:1377-1409（简化）
// 选择执行路径：流式执行器（如果已经在流式阶段启动了）或批量执行
const toolUpdates = streamingToolExecutor
  ? streamingToolExecutor.getRemainingResults()  // 获取流式执行的剩余结果
  : runTools(toolUseBlocks, assistantMessages,    // 批量执行所有工具
      canUseTool, toolUseContext)

// 逐个处理工具执行结果
for await (const update of toolUpdates) {
  if (update.message) {
    yield update.message                          // 传递给上层（UI 展示）
    // 检查是否有钩子阻止继续
    if (update.message.type === 'attachment' &&
        update.message.attachment.type === 'hook_stopped_continuation') {
      shouldPreventContinuation = true
    }
    // 将结果归一化为 API 消息格式
    toolResults.push(
      ...normalizeMessagesForAPI(
        [update.message], toolUseContext.options.tools,
      ).filter(_ => _.type === 'user'),            // 工具结果以 user 消息形式传给 API
    )
  }
  if (update.newContext) {
    updatedToolUseContext = { ...update.newContext, queryTracking }
  }
}
```

工具结果以 `user` 消息类型传递给 API——这是 Anthropic API 的协议要求：tool_use 块出现在 assistant 消息中，对应的 tool_result 块出现在 user 消息中。

### 4.6 工具使用摘要生成

工具执行完成后，系统异步生成一份**工具使用摘要**——使用 Haiku 模型总结工具调用的结果：

```typescript
// src/query.ts:1411-1471（简化）
if (config.gates.emitToolUseSummaries &&
    toolUseBlocks.length > 0 &&
    !toolUseContext.abortController.signal.aborted &&
    !toolUseContext.agentId) {         // 子智能体不生成摘要（移动端不展示）
  // 收集每个工具的输入和输出信息
  const toolInfoForSummary = toolUseBlocks.map(block => ({
    name: block.name,
    input: block.input,
    output: /* 从 toolResults 中查找对应结果 */,
  }))
  // 异步生成摘要——不阻塞下一轮 API 调用
  nextPendingToolUseSummary = generateToolUseSummary({
    tools: toolInfoForSummary,
    signal: toolUseContext.abortController.signal,
    lastAssistantText,                  // 提供上下文：模型最后说了什么
  }).then(summary =>
    summary ? createToolUseSummaryMessage(summary, toolUseIds) : null
  ).catch(() => null)                   // 摘要生成失败不影响主流程
}
```

摘要生成是**纯异步**的——它在后台运行，不阻塞下一轮 API 调用。`.catch(() => null)` 确保摘要生成的任何失败都被静默吞掉，不影响核心查询流程。

### 4.7 下一轮状态转换

所有工具结果收集完毕后，循环构造下一轮的状态：

```typescript
// src/query.ts:1704-1728
// 检查是否超过最大轮次限制
if (maxTurns && nextTurnCount > maxTurns) {
  yield createAttachmentMessage({
    type: 'max_turns_reached',
    maxTurns, turnCount: nextTurnCount,
  })
  return { reason: 'max_turns', turnCount: nextTurnCount }
}

// 构造完整的下一轮状态——不修改个别字段，而是重新构造整个 State 对象
const next: State = {
  messages: [...messagesForQuery, ...assistantMessages, ...toolResults],
  toolUseContext: toolUseContextWithQueryTracking,
  autoCompactTracking: tracking,
  turnCount: nextTurnCount,
  maxOutputTokensRecoveryCount: 0,        // 重置：新轮次重新开始恢复计数
  hasAttemptedReactiveCompact: false,      // 重置：新轮次允许新的压缩尝试
  pendingToolUseSummary: nextPendingToolUseSummary,
  maxOutputTokensOverride: undefined,      // 重置：不保留上一轮的限制覆盖
  stopHookActive,
  transition: { reason: 'next_turn' },
}
state = next
// 隐含的 continue —— 回到 while(true) 循环顶部
```

每个"继续站点"都重新构造完整的 `State` 对象，而非修改个别字段——这种**不可变更新模式**防止了遗漏某个字段更新的细微 bug。`transition.reason` 字段让测试可以精确断言循环走了哪条恢复路径。

---

## 第五章：Extended Thinking 支持

### 5.1 什么是 Extended Thinking

Extended Thinking（扩展思考）是 Claude 模型的一项能力——在生成最终回复之前，模型先进行一段"内部思考"。这类似于人类在回答复杂问题之前先在脑中推理。思考内容以 `thinking` 类型的内容块出现在 API 响应中，与最终的 `text` 和 `tool_use` 内容块并列。

### 5.2 ThinkingConfig 类型

```typescript
// src/utils/thinking.ts:10-13
export type ThinkingConfig =
  | { type: 'adaptive' }                    // 自适应思考：模型自行决定是否思考以及思考多少
  | { type: 'enabled'; budgetTokens: number } // 固定预算思考：分配固定的思考 token 预算
  | { type: 'disabled' }                    // 禁用思考
```

三种模式的使用场景：

| 模式 | 适用场景 | 模型要求 |
|------|---------|---------|
| `adaptive` | Claude 4.6+ 模型，模型自动判断何时需要深度思考 | Opus 4.6, Sonnet 4.6+ |
| `enabled` | 旧版 Claude 4 模型，需要显式分配思考预算 | 所有支持思考的模型 |
| `disabled` | 用户明确关闭思考，或模型不支持 | 任何模型 |

### 5.3 模型支持检测

系统通过两个函数判断模型的思考能力：

```typescript
// src/utils/thinking.ts:90-110
export function modelSupportsThinking(model: string): boolean {
  // ... 3P 覆盖检查 ...
  const canonical = getCanonicalName(model)
  const provider = getAPIProvider()
  // 1P 和 Foundry：所有 Claude 4+ 模型（包括 Haiku 4.5）
  if (provider === 'foundry' || provider === 'firstParty') {
    return !canonical.includes('claude-3-')     // 排除 Claude 3 系列
  }
  // 3P (Bedrock/Vertex)：仅 Opus 4+ 和 Sonnet 4+
  return canonical.includes('sonnet-4') || canonical.includes('opus-4')
}

// src/utils/thinking.ts:113-144
export function modelSupportsAdaptiveThinking(model: string): boolean {
  // ... 3P 覆盖检查 ...
  const canonical = getCanonicalName(model)
  // 仅 Claude 4.6 系列支持自适应思考
  if (canonical.includes('opus-4-6') || canonical.includes('sonnet-4-6')) {
    return true
  }
  // 旧版模型不支持自适应思考
  if (canonical.includes('opus') || canonical.includes('sonnet') ||
      canonical.includes('haiku')) {
    return false
  }
  // 未知模型在 1P/Foundry 上默认支持（新模型可能都支持）
  const provider = getAPIProvider()
  return provider === 'firstParty' || provider === 'foundry'
}
```

这两个函数的注释中反复出现**不要更改**的警告——思考配置对模型质量有巨大影响，需要跨团队（模型发布 DRI 和研究团队）协调。

### 5.4 默认启用逻辑

```typescript
// src/utils/thinking.ts:146-162
export function shouldEnableThinkingByDefault(): boolean {
  // 环境变量覆盖：MAX_THINKING_TOKENS > 0 则启用
  if (process.env.MAX_THINKING_TOKENS) {
    return parseInt(process.env.MAX_THINKING_TOKENS, 10) > 0
  }
  // 用户设置：alwaysThinkingEnabled === false 则禁用
  const { settings } = getSettingsWithErrors()
  if (settings.alwaysThinkingEnabled === false) {
    return false
  }
  // 默认：启用思考（除非明确禁用）
  return true
}
```

### 5.5 Thinking 在 API 调用中的配置

在 `claude.ts` 的 `paramsFromContext()` 中，思考配置被转换为 API 参数：

```typescript
// src/services/api/claude.ts:1604-1618
// 重要：不要在未通知模型发布 DRI 和研究团队的情况下
// 更改下面的 adaptive vs budget 思考选择。
if (modelSupportsAdaptiveThinking(options.model)) {
  // 支持自适应思考的模型——无需预算，模型自主决定
  thinking = { type: 'adaptive' }
} else {
  // 不支持自适应思考的模型——使用固定预算
  let thinkingBudget = getMaxThinkingTokensForModel(options.model)
  // ... 预算计算逻辑 ...
}
```

### 5.6 Thinking 内容在响应中的处理

思考内容块在流式响应中以 `content_block_start` / `content_block_delta` / `content_block_stop` 三阶段到达。关键的是，思考块**不会触发工具循环**——模型可以在一条响应中同时包含思考、文本和工具调用：

```
Assistant 消息内容块序列:
  [thinking] → "让我分析一下这个文件的结构..."
  [text]     → "我来帮你修改这个文件。"
  [tool_use] → { name: "FileEditTool", input: {...} }
```

只有 `tool_use` 块才会设置 `needsFollowUp = true`。当模型回退（fallback）到不同模型时，思考签名会被剥离（第 928 行），避免 API 验证错误。

---

## 第六章：Token 预算管理

### 6.1 预算管理的三个层次

Claude Code 在三个不同层次管理 token 预算：

| 层次 | 机制 | 位置 | 作用 |
|------|------|------|------|
| **API 调用级** | `max_tokens` 参数 | `claude.ts` | 限制单次 API 回复的输出 token |
| **轮次级** | Token Budget Feature | `query/tokenBudget.ts` | 在单个用户轮次内管理总输出量 |
| **会话级** | 费用追踪 + 自动压缩 | `cost-tracker.ts` + `compact/` | 跨轮次管理总费用和上下文大小 |

### 6.2 Token Budget 自动续写

Token Budget 是一个 Feature-Gated 机制，让模型在单轮内自动续写，而不是因为输出限制被截断后等待用户手动提示：

```typescript
// src/query/tokenBudget.ts:45-93
export function checkTokenBudget(
  tracker: BudgetTracker,
  agentId: string | undefined,       // 子智能体不使用预算续写
  budget: number | null,
  globalTurnTokens: number,
): TokenBudgetDecision {
  // 子智能体、无预算、或负预算 → 直接停止
  if (agentId || budget === null || budget <= 0) {
    return { action: 'stop', completionEvent: null }
  }

  const turnTokens = globalTurnTokens
  const pct = Math.round((turnTokens / budget) * 100)
  const deltaSinceLastCheck = globalTurnTokens - tracker.lastGlobalTurnTokens

  // 递减回报检测：连续 3+ 次续写且每次新增 < 500 token
  const isDiminishing =
    tracker.continuationCount >= 3 &&
    deltaSinceLastCheck < DIMINISHING_THRESHOLD &&    // 500
    tracker.lastDeltaTokens < DIMINISHING_THRESHOLD   // 500

  // 继续条件：未递减 + 未达 90% 预算
  if (!isDiminishing && turnTokens < budget * COMPLETION_THRESHOLD) {
    tracker.continuationCount++
    tracker.lastDeltaTokens = deltaSinceLastCheck
    tracker.lastGlobalTurnTokens = globalTurnTokens
    return {
      action: 'continue',
      nudgeMessage: getBudgetContinuationMessage(pct, turnTokens, budget),
      // ... 追踪信息
    }
  }

  // 停止条件：递减回报 或 曾经续写过
  if (isDiminishing || tracker.continuationCount > 0) {
    return { action: 'stop', completionEvent: { /* 分析数据 */ } }
  }

  return { action: 'stop', completionEvent: null }
}
```

**递减回报检测**是一个精妙的设计——如果连续 3 次续写每次只新增不到 500 个 token，说明模型已经"没什么可说的了"，继续续写只是浪费 token。

### 6.3 自动压缩触发

当对话历史占用的 token 达到上下文窗口的 ~80% 时，系统自动触发压缩：

```
Token 使用量监控
    │
    ├── < 80%: 正常运行
    │
    ├── ~80%: 触发 autocompact()
    │         ├── 按 API 轮次分组消息
    │         ├── 对每个轮次生成摘要
    │         ├── 用摘要替换原始消息
    │         └── 恢复最近 5 个编辑过的文件内容
    │
    └── 接近 100%: 阻塞限制
              ├── 有反应式压缩 → 允许 API 调用，400 后触发反应式压缩
              └── 无反应式压缩 → 阻塞查询，提示用户手动 /compact
```

### 6.4 max_tokens 上下文溢出自动调整

当 API 返回 400 错误"input length and max_tokens exceed context limit"时，`withRetry` 自动调整参数：

```typescript
// src/services/api/withRetry.ts:550-595
export function parseMaxTokensContextOverflowError(error: APIError):
  | { inputTokens: number; maxTokens: number; contextLimit: number }
  | undefined {
  // 解析错误消息中的数值
  // 例如: "input length and `max_tokens` exceed context limit: 188059 + 20000 > 200000"
  const regex =
    /input length and `max_tokens` exceed context limit: (\d+) \+ (\d+) > (\d+)/
  const match = error.message.match(regex)
  if (!match) return undefined
  return {
    inputTokens: parseInt(match[1]!, 10),    // 188059
    maxTokens: parseInt(match[2]!, 10),      // 20000
    contextLimit: parseInt(match[3]!, 10),   // 200000
  }
}
```

解析出数值后，`withRetry` 将 `max_tokens` 自动降低到 `contextLimit - inputTokens`——系统宁可生成较短的回复，也不让请求完全失败。

### 6.5 费用追踪 src/cost-tracker.ts

费用追踪系统跨越整个会话生命周期，支持恢复和持久化：

```typescript
// src/cost-tracker.ts:278-323（核心追踪函数）
export function addToTotalSessionCost(
  cost: number, usage: Usage, model: string,
): number {
  const modelUsage = addToTotalModelUsage(cost, usage, model)
  addToTotalCostState(cost, modelUsage, model)

  // 通过 OpenTelemetry 计数器上报
  getCostCounter()?.add(cost, { model })
  getTokenCounter()?.add(usage.input_tokens, { model, type: 'input' })
  getTokenCounter()?.add(usage.output_tokens, { model, type: 'output' })

  // 递归处理顾问模型的用量（嵌套费用追踪）
  let totalCost = cost
  for (const advisorUsage of getAdvisorUsage(usage)) {
    const advisorCost = calculateUSDCost(advisorUsage.model, advisorUsage)
    totalCost += addToTotalSessionCost(
      advisorCost, advisorUsage, advisorUsage.model,
    )
  }
  return totalCost
}
```

费用在每次 `message_delta` 事件中实时更新，确保用户随时能看到准确的费用信息。会话费用通过 `saveCurrentSessionCosts()` 持久化到项目配置文件（`~/.claude/projects/<project>/config.json`），支持通过 `/resume` 恢复会话时重新加载。

---

## 第七章：上下文构建 src/context.ts

### 7.1 上下文注入的两个维度

查询引擎在发送 API 请求前，从两个维度注入上下文信息：

```
系统上下文 (systemContext)          用户上下文 (userContext)
├── gitStatus: Git 仓库快照         ├── claudeMd: CLAUDE.md 记忆文件内容
└── cacheBreaker: 调试用注入         └── currentDate: 当前日期
        │                                    │
        ▼                                    ▼
appendSystemContext()               prependUserContext()
合并到系统提示词末尾                 注入到消息列表前部
```

### 7.2 系统上下文 getSystemContext()

```typescript
// src/context.ts:116-150
export const getSystemContext = memoize(
  async (): Promise<{ [k: string]: string }> => {
    // 在远程会话(CCR)中或禁用 Git 指令时跳过 Git 状态
    const gitStatus =
      isEnvTruthy(process.env.CLAUDE_CODE_REMOTE) ||
      !shouldIncludeGitInstructions()
        ? null
        : await getGitStatus()

    // 缓存断裂器（仅 Anthropic 内部 + Feature Flag 开启）
    const injection = feature('BREAK_CACHE_COMMAND')
      ? getSystemPromptInjection()
      : null

    return {
      ...(gitStatus && { gitStatus }),
      ...(injection && {
        cacheBreaker: `[CACHE_BREAKER: ${injection}]`,
      }),
    }
  },
)
```

`getSystemContext` 使用 `memoize()` 缓存——整个会话期间只获取一次 Git 状态。这是因为 Git 状态是对话开始时的快照，不需要随对话更新（模型的工具调用会直接操作 Git）。

### 7.3 Git 状态获取 getGitStatus()

```typescript
// src/context.ts:36-111
export const getGitStatus = memoize(async (): Promise<string | null> => {
  const isGit = await getIsGit()
  if (!isGit) return null               // 非 Git 仓库——跳过

  try {
    // 5 个 Git 命令并行执行
    const [branch, mainBranch, status, log, userName] = await Promise.all([
      getBranch(),                       // 当前分支
      getDefaultBranch(),                // 主分支（master/main）
      execFileNoThrow(gitExe(),          // git status --short
        ['--no-optional-locks', 'status', '--short'], ...)
        .then(({ stdout }) => stdout.trim()),
      execFileNoThrow(gitExe(),          // git log --oneline -n 5
        ['--no-optional-locks', 'log', '--oneline', '-n', '5'], ...)
        .then(({ stdout }) => stdout.trim()),
      execFileNoThrow(gitExe(),          // git config user.name
        ['config', 'user.name'], ...)
        .then(({ stdout }) => stdout.trim()),
    ])

    // 状态超过 2000 字符时截断（防御大型仓库）
    const truncatedStatus = status.length > MAX_STATUS_CHARS
      ? status.substring(0, MAX_STATUS_CHARS) +
        '\n... (truncated because it exceeds 2k characters...)'
      : status

    return [
      `This is the git status at the start of the conversation...`,
      `Current branch: ${branch}`,
      `Main branch: ${mainBranch}`,
      ...(userName ? [`Git user: ${userName}`] : []),
      `Status:\n${truncatedStatus || '(clean)'}`,
      `Recent commits:\n${log}`,
    ].join('\n\n')
  } catch (error) {
    logError(error)
    return null                          // Git 命令失败——静默降级
  }
})
```

五个 Git 命令通过 `Promise.all` **并行执行**——这是一个经典的性能优化。`--no-optional-locks` 参数防止 Git 获取不必要的锁文件。状态截断到 2000 字符是一个**防御性编程**措施——大型仓库的 `git status` 可能输出数万行，直接注入上下文会浪费大量 token。

### 7.4 用户上下文 getUserContext()

```typescript
// src/context.ts:155-189
export const getUserContext = memoize(
  async (): Promise<{ [k: string]: string }> => {
    // 禁用条件：
    // 1. CLAUDE_CODE_DISABLE_CLAUDE_MDS 环境变量
    // 2. --bare 模式 且 没有 --add-dir 参数
    const shouldDisableClaudeMd =
      isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_CLAUDE_MDS) ||
      (isBareMode() && getAdditionalDirectoriesForClaudeMd().length === 0)

    // 获取 CLAUDE.md 记忆文件内容
    const claudeMd = shouldDisableClaudeMd
      ? null
      : getClaudeMds(filterInjectedMemoryFiles(await getMemoryFiles()))

    // 缓存给自动模式分类器使用（避免循环依赖）
    setCachedClaudeMdContent(claudeMd || null)

    return {
      ...(claudeMd && { claudeMd }),
      currentDate: `Today's date is ${getLocalISODate()}.`,
    }
  },
)
```

`--bare` 模式的处理逻辑值得注意——它跳过自动发现（不遍历目录找 CLAUDE.md），但**保留**用户通过 `--add-dir` 显式指定的目录。这是"跳过我没要求的，但尊重我要求的"原则。

### 7.5 缓存清除机制

```typescript
// src/context.ts:25-34
export function setSystemPromptInjection(value: string | null): void {
  systemPromptInjection = value
  // 注入内容变化时立即清除上下文缓存
  getUserContext.cache.clear?.()
  getSystemContext.cache.clear?.()
}
```

`setSystemPromptInjection()` 同时清除**两个**缓存——因为系统提示词注入可能影响上下文的构建方式。使用可选调用 `?.()` 是因为 lodash-es 的 `memoize` 返回的函数可能没有 `cache.clear` 方法（取决于配置的缓存实现）。

---

## 第八章：完整查询时序图

以下时序图展示了从用户输入到最终回复的完整数据流，包含一次工具调用循环：

```
用户                   REPL.tsx          QueryEngine        query.ts           claude.ts         Anthropic API
 │                       │                  │                  │                  │                  │
 │  按 Enter 提交        │                  │                  │                  │                  │
 │──────────────────────>│                  │                  │                  │                  │
 │                       │                  │                  │                  │                  │
 │                       │ submitMessage()  │                  │                  │                  │
 │                       │─────────────────>│                  │                  │                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │ 1. 清除轮次状态  │                  │                  │
 │                       │                  │    discoveredSkillNames.clear()     │                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │ 2. 构建系统提示词│                  │                  │
 │                       │                  │    systemPrompt = [default + memory + append]          │
 │                       │                  │                  │                  │                  │
 │                       │                  │ 3. 获取上下文    │                  │                  │
 │                       │                  │    ├── getUserContext()  ──── CLAUDE.md + 日期          │
 │                       │                  │    └── getSystemContext() ── Git 状态                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │ 4. 处理孤儿权限  │                  │                  │
 │                       │                  │    (如有) handleOrphanedPermission()│                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │     query()      │                  │                  │
 │                       │                  │─────────────────>│                  │                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │ 消息预处理管道    │                  │
 │                       │                  │                  │ ├── compactBoundary                 │
 │                       │                  │                  │ ├── toolResultBudget                │
 │                       │                  │                  │ ├── microcompact                   │
 │                       │                  │                  │ └── autocompact (if 80%)            │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │  callModel()     │                  │
 │                       │                  │                  │─────────────────>│                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │                  │ paramsFromContext()
 │                       │                  │                  │                  │ ├── system prompt │
 │                       │                  │                  │                  │ ├── thinking cfg  │
 │                       │                  │                  │                  │ ├── tools schema  │
 │                       │                  │                  │                  │ └── cache ctrl    │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │                  │  withRetry()     │
 │                       │                  │                  │                  │────────────────>│
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │                  │  SSE Stream      │
 │                       │                  │                  │                  │<─ ─ ─ ─ ─ ─ ─ ─│
 │                       │                  │                  │                  │  message_start   │
 │                       │                  │                  │  StreamEvent     │  content_delta   │
 │  ◄── 流式文本渲染 ──  │ ◄── yield ────  │ ◄── yield ────  │ ◄── yield ────  │  content_delta   │
 │  (用户看到部分回复)    │                  │                  │                  │  ...             │
 │                       │                  │                  │                  │  tool_use 开始   │
 │                       │                  │                  │                  │  tool_use 输入   │
 │                       │                  │                  │                  │  message_stop    │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │ needsFollowUp=true                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │ ┌─────────────────────────────┐     │
 │                       │                  │                  │ │ 工具执行阶段                 │     │
 │                       │                  │                  │ │                             │     │
 │                       │                  │                  │ │ canUseTool() → allow/deny   │     │
 │  ◄── 权限提示? ────── │                  │                  │ │   ↓ (如果 ask 模式)         │     │
 │  ── 用户允许 ───────> │                  │                  │ │                             │     │
 │                       │                  │                  │ │ runTools()                  │     │
 │                       │                  │                  │ │ ├── validateInput()          │     │
 │                       │                  │                  │ │ ├── checkPermissions()       │     │
 │                       │                  │                  │ │ ├── tool.call()              │     │
 │                       │                  │                  │ │ └── 收集 toolResults[]       │     │
 │  ◄── 工具执行结果 ──  │ ◄── yield ────  │ ◄── yield ────  │ │                             │     │
 │                       │                  │                  │ └─────────────────────────────┘     │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │ state = { messages + toolResults }  │
 │                       │                  │                  │ continue → 回到循环顶部              │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │  callModel() (第二次)               │
 │                       │                  │                  │─────────────────>│────────────────>│
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │                  │  SSE Stream      │
 │  ◄── 流式文本渲染 ──  │ ◄── yield ────  │ ◄── yield ────  │ ◄── yield ────  │  (纯文本回复)    │
 │  (最终回复)           │                  │                  │                  │  message_stop    │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │ needsFollowUp=false                 │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │ 停止钩子检查     │                  │
 │                       │                  │                  │ Token 预算检查   │                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │                  │ return {reason:'completed'}          │
 │                       │                  │                  │<────────────────                    │
 │                       │                  │                  │                  │                  │
 │                       │                  │ 5. token 用量汇总│                  │                  │
 │                       │                  │    totalUsage = accumulateUsage()   │                  │
 │                       │                  │                  │                  │                  │
 │                       │                  │ 6. 费用持久化    │                  │                  │
 │                       │                  │    saveCurrentSessionCosts()        │                  │
 │                       │                  │                  │                  │                  │
 │                       │ yield SDKMessage  │                  │                  │                  │
 │  ◄── 最终结果 ──────  │ ◄──────────────  │                  │                  │                  │
 │                       │                  │                  │                  │                  │
```

**关键时间节点注释**：

| 阶段 | 近似耗时 | 说明 |
|------|---------|------|
| 系统提示词构建 | ~5ms | 内存读取 + 字符串拼接 |
| 上下文获取（首次） | ~50-200ms | Git 命令 + CLAUDE.md 读取（并行），后续缓存 |
| API 调用（首次响应 token） | ~1-5s | 网络延迟 + 模型推理，模型越大越慢 |
| 流式传输 | ~5-30s | 取决于回复长度和模型速度 |
| 工具执行 | ~100ms-60s | 取决于工具类型（FileRead ~50ms, BashTool ~1-60s） |
| API 调用（第二次） | ~1-5s | 带工具结果的上下文更长，可能稍慢 |

---

## 设计哲学分析

查询引擎是 Claude Code 中所有十大设计哲学的**汇聚点**——几乎每一个设计原则都能在查询引擎中找到最深刻的体现。

### 可组合性（Composability）：工具调用循环的运行时表达

工具调用循环是**可组合性**在运行时最生动的体现。每个工具通过统一的 `Tool` 接口注册，每次循环迭代遵循相同的模式：API 调用 → 解析响应 → 执行工具 → 收集结果 → 再次调用。新工具的加入不需要修改循环逻辑——只要实现 `Tool` 接口并注册，循环就能自动处理它。这种**开放-封闭原则**的运行时应用让 Claude Code 从几个文件操作工具扩展到 40+ 工具而没有增加核心复杂度。工具甚至可以组合使用——模型在一轮中同时调用 `GrepTool` 搜索代码和 `FileReadTool` 读取文件，循环并行执行它们，结果统一收集后传给下一轮 API 调用。

### 优雅降级（Graceful Degradation）：withRetry 的韧性层次

`withRetry` 包装器是**优雅降级**的教科书级实现。它不是简单地"失败了就重试"，而是根据错误类型采取不同的降级策略：

1. **瞬时错误**（网络中断、TCP 静默断开）→ 指数退避重试
2. **过载错误**（529）→ 退避重试，连续 3 次后切换到 fallbackModel
3. **上下文溢出**（400 prompt-too-long）→ 解析数值，自动降低 max_tokens
4. **快速模式限流**→ 短延迟保留快速模式（维护缓存），长延迟切换到普通模式
5. **配额耗尽**→ 永久降级到非快速模式

每一级降级都比前一级更"激烈"，但每一级都优于完全失败。`FallbackTriggeredError` 尤其精妙——它不是一个"错误"，而是一个**策略信号**，告诉上层"我已经尽力了，请用备选方案重试"。

### 上下文窗口经济学（Context Window Economics）：稀缺资源的主动管理

整个查询引擎的设计围绕一个核心约束：**上下文窗口是最稀缺的资源**。Token 预算管理不是事后补救，而是贯穿整个管道的主动策略：

- **消息预处理管道**在每次循环迭代时运行，包含 7 层处理（从 compactBoundary 到 autocompact），每一层都在减少 token 消耗
- **autocompact 在 80% 阈值**主动触发——不等到窗口满了才压缩，而是留出 20% 的缓冲区
- **工具结果持久化**（`toolResultStorage.ts`）将大结果写入磁盘，模型只看到引用——一个 10KB 的 grep 结果变成一行引用标记
- **记忆预取去重**通过 `readFileState` 避免将已读文件再次注入上下文
- **缓存标记**（ephemeral cache control）减少重复发送相同内容的费用

Token Budget Feature 的**递减回报检测**是这个原则最微妙的表达——当续写的边际收益低于阈值（每次 < 500 token），系统主动停止，而不是浪费预算直到耗尽。

### 防御性编程（Defensive Programming）：80% 阈值的哲学

autocompact 的 80% 触发阈值体现了防御性编程的核心原则——**在失败发生之前就预防它**。如果等到 100% 才压缩，系统已经无法发送新消息了；在 80% 时主动压缩，为后续操作预留了充足空间。类似地，`updateUsage()` 中的 `> 0` 守卫防止了一个微妙的数据覆写 bug——`message_delta` 事件可能包含 0 值的 token 字段，不加守卫会丢失 `message_start` 设置的真实值。

`parseMaxTokensContextOverflowError` 使用**正则表达式**从错误消息中提取数值——这看起来脆弱，但实际上是对 API 合约的务实解读：错误消息的格式是稳定的，正则提取比维护一个专门的错误类型更灵活。

### 无需修改的可扩展性（Extensibility Without Modification）

上下文构建展示了这个原则的精妙应用：

- **系统上下文**通过 `systemContext` 字典注入——新的上下文源只需添加一个键值对
- **用户上下文**通过 `userContext` 字典注入——CLAUDE.md 记忆文件是用户自定义上下文的主要渠道
- **工具描述**通过 `tools` 参数传递——新工具注册后其 schema 自动出现在上下文中
- **附件消息**通过 `getAttachmentMessages()` 注入——技能发现、排队命令等都以附件形式添加

`QueryEngine` 本身不知道上下文的具体内容——它只负责将各个来源**合并**到 API 请求中。这使得添加新的上下文源（如 MCP 服务器上下文、IDE 桥接上下文）不需要修改 `QueryEngine` 的任何代码。

### 人在回路（Human-in-the-Loop）：异步权限的保全

`orphanedPermission` 机制是"人在回路"原则在异步场景中的保全。当子智能体请求权限时，如果父智能体已断开（用户按了 Stop），权限决策被保存为"孤儿权限"。下次会话恢复时，`handleOrphanedPermission()` 将缓存的权限决策重新应用——确保每一个工具调用都经过了人类的授权，即使这个授权是在"时间上错位的"。

`hasHandledOrphanedPermission` 一次性标志确保这个恢复逻辑只执行一次——防止在长对话中重复处理同一个孤儿权限。这是一个用最小的实现复杂度保全核心安全原则的典范。

### 十大设计哲学的汇聚

QueryEngine 是整个 Claude Code 系统中**唯一一个**所有十大设计哲学都直接体现的组件：

- **安全优先**：每个工具调用都经过权限检查，模型回退不泄露思考签名
- **渐进信任**：权限模式通过 `canUseTool` 回调注入，引擎不关心具体的信任级别
- **可组合性**：工具循环、上下文注入、消息管道都是可组合的
- **优雅降级**：从 withRetry 到 autocompact 到 max_output_tokens 恢复，层层降级
- **性能敏感启动**：上下文缓存、记忆预取、流式工具执行器
- **人在回路**：权限提示、孤儿权限、停止钩子
- **隔离与遏制**：子智能体独立上下文、工具结果隔离到磁盘
- **无需修改的可扩展性**：上下文字典、工具注册、依赖注入
- **上下文窗口经济学**：七层预处理管道、80% 自动压缩、递减回报检测
- **防御性编程**：> 0 守卫、错误扣留、transition.reason 可测试性

这不是巧合——查询引擎是系统的中枢神经，所有子系统都通过它交互，自然成为所有设计原则交汇的焦点。

### 流式工具执行器的性能哲学

`StreamingToolExecutor` 值得单独讨论——它体现了**性能敏感启动**的深层哲学延伸到运行时。在传统的"请求-响应"模式中，工具执行必须等待模型完成所有输出后才能开始。但 Claude Code 的流式执行器在模型**生成第一个工具调用块后立即开始执行**——当模型还在生成第二个工具调用的参数时，第一个工具已经在磁盘上搜索文件了。这种流水线并行不仅减少了延迟，还在心理层面提升了用户体验——用户看到工具结果"立刻"出现，而不是等待一个长时间的空白期。

这与 `generateToolUseSummary` 的异步模式形成呼应——摘要生成在后台运行，不阻塞下一轮 API 调用，使得"做总结"这个低优先级任务不影响"继续对话"这个高优先级任务。整个查询管道的设计思想是**让关键路径尽可能短**，将所有可以延迟或并行的工作推到关键路径之外。

### 错误扣留的设计权衡

"错误扣留"（error withholding）模式是一个有趣的设计权衡——某些 API 错误不立即传递给调用者，而是被系统"扣住"尝试自动恢复。这意味着用户可能永远不知道发生了错误（如果恢复成功），这提升了使用体验但降低了透明度。设计者选择了**用户体验优先于完全透明**——对于可自动恢复的错误，静默恢复比弹出错误提示再自动重试更自然。但对于不可恢复的错误（`!withheld`），系统立即传递，确保用户能及时介入。

这个决策链条——"能自动恢复的就恢复，不能的就报告"——正是"优雅降级"和"人在回路"两个原则的平衡点。系统在能力范围内自主处理，超出能力时将控制权交还人类。

---

## 关键要点总结

1. **工具调用循环**是一个显式状态机——通过 `State` 类型管理循环状态，每个"继续站点"都重新构造完整状态对象，`transition.reason` 使恢复路径可测试
2. **多级错误恢复**策略按优先级排列：上下文折叠排空 → 反应式压缩 → max_output_tokens 提升（8K→64K） → 多轮恢复 → 表面化错误
3. **Extended Thinking** 有三种模式（adaptive/enabled/disabled），自适应思考仅限 Claude 4.6+ 模型，配置变更需要跨团队协调
4. **Token 预算**在三个层次管理：API 调用级（max_tokens）、轮次级（Token Budget Feature）、会话级（费用追踪 + 自动压缩）
5. **上下文构建**通过两个维度注入：系统上下文（Git 状态）和用户上下文（CLAUDE.md 记忆 + 日期），均使用 memoize 缓存
6. **费用追踪**实时更新、支持多模型（含顾问模型嵌套）、通过 OpenTelemetry 上报、持久化到项目配置
7. **查询引擎是十大设计哲学的汇聚点**——从可组合的工具循环到防御性的 token 守卫，每个原则都有深刻体现

---

## 下一篇预览

**Doc 8：权限系统** 将深入 Claude Code 最核心的安全机制——五种权限模式（default/plan/auto/bypassPermissions/dangerously_allow_all）如何形成信任阶梯，规则引擎如何实现细粒度的 Allow/Deny 控制，文件系统沙箱如何限制目录访问，Hook 系统如何提供无需修改核心代码的自定义安全规则，以及 ML 分类器如何在自动模式下做出权限决策。权限系统是"安全优先"和"渐进信任"两大设计哲学的**完整深度分析**所在。
