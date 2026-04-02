# Doc 6: 工具系统

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）、Doc 2（构建系统与运行时）、Doc 3（入口点与初始化流程）、Doc 4（终端 UI 系统）、Doc 5（命令系统）

在前五篇文档中，我们理解了 Claude Code 的语言基础、架构全景、构建系统、启动流程、UI 渲染和命令系统。命令系统是**用户主动发起**的交互通道（`/commit`、`/compact`），而工具系统则是 **AI 模型主动调用**的能力通道。当 Claude 需要读取文件、执行命令、搜索代码、创建子智能体时，它通过工具系统发起请求。工具系统是 Claude Code 能力的基石——没有工具，Claude 只能输出文本；有了工具，Claude 可以真正地与文件系统、Shell、网络和多智能体协作网络交互。

本文档将深入工具系统的接口定义、注册中心、核心内置工具，以及网络与智能体类工具。

---

## 第一章：工具接口定义 src/Tool.ts (792 行)

### 1.1 Tool 泛型接口

工具系统的类型基础定义在 `src/Tool.ts`。每个工具都实现 `Tool` 接口，这是一个带三个泛型参数的完整类型：

```typescript
// src/Tool.ts:362-366
// Tool 接口的三个泛型参数：
// Input  — 工具输入的 Zod schema 类型（必须是对象类型）
// Output — 工具执行结果的数据类型
// P      — 工具进度事件的数据类型
export type Tool<
  Input extends AnyObject = AnyObject,        // 输入 schema，默认为任意对象
  Output = unknown,                           // 输出类型，默认为 unknown
  P extends ToolProgressData = ToolProgressData, // 进度数据类型
> = {
  // ... 完整的接口定义
}
```

这三个泛型使得每个工具的输入验证、输出处理和进度追踪都是**类型安全**的。例如 `BashTool` 的 `Input` 包含 `command: string` 和 `timeout?: number`，而 `FileReadTool` 的 `Input` 包含 `file_path: string` 和 `offset?: number`。

### 1.2 核心字段：身份与约束

```typescript
// src/Tool.ts:456-472
// 工具的身份和基本约束字段
readonly name: string                   // 工具唯一标识符，如 'Bash'、'Read'、'Edit'
aliases?: string[]                      // 向后兼容的别名（工具重命名时使用）
searchHint?: string                     // ToolSearch 关键词匹配的一行描述
readonly shouldDefer?: boolean          // 是否延迟加载（需要 ToolSearch 才能调用）
readonly alwaysLoad?: boolean           // 是否始终加载（跳过延迟加载）
maxResultSizeChars: number              // 结果超过此大小写入磁盘（Infinity 表示永不持久化）
readonly strict?: boolean               // 是否启用严格模式（更严格的参数验证）
isMcp?: boolean                         // 是否是 MCP 工具
isLsp?: boolean                         // 是否是 LSP 工具
```

`maxResultSizeChars` 是上下文窗口经济学的核心机制之一——当工具输出超过阈值时，系统将结果保存到磁盘文件，只向模型发送一个预览和文件路径引用，避免大量结果耗尽宝贵的上下文窗口。

### 1.3 输入验证与权限检查：两阶段门控

工具调用的安全性通过**两阶段门控**实现：

```typescript
// src/Tool.ts:489-503
// 第一阶段：输入验证 — 检查输入是否合法
// 不向用户显示 UI，直接告知模型为什么输入不合法
validateInput?(
  input: z.infer<Input>,
  context: ToolUseContext,
): Promise<ValidationResult>

// 第二阶段：权限检查 — 仅在 validateInput 通过后调用
// 决定是否需要向用户请求权限
// 通用权限逻辑在 permissions.ts 中，这里只包含工具特定逻辑
checkPermissions(
  input: z.infer<Input>,
  context: ToolUseContext,
): Promise<PermissionResult>
```

`ValidationResult` 的定义也体现了防御性设计：

```typescript
// src/Tool.ts:95-101
// 验证结果：成功只需 result: true
// 失败必须提供 message（给模型看的错误说明）和 errorCode（分类编码）
export type ValidationResult =
  | { result: true }                     // 验证通过
  | {
      result: false                      // 验证失败
      message: string                    // 错误描述（发送给模型）
      errorCode: number                  // 错误分类编码
    }
```

### 1.4 执行与结果：call() 方法

工具的核心执行逻辑在 `call()` 方法中：

```typescript
// src/Tool.ts:379-385
// call() 是工具的核心执行方法
call(
  args: z.infer<Input>,                  // 经过 Zod schema 验证的输入
  context: ToolUseContext,               // 执行上下文（消息历史、状态、权限等）
  canUseTool: CanUseToolFn,              // 权限检查回调（用于嵌套工具调用）
  parentMessage: AssistantMessage,       // 触发此工具调用的助手消息
  onProgress?: ToolCallProgress<P>,      // 进度报告回调（流式进度）
): Promise<ToolResult<Output>>           // 返回结果
```

`ToolResult` 包含执行结果数据和可选的副作用：

```typescript
// src/Tool.ts:321-336
// 工具执行结果
export type ToolResult<T> = {
  data: T                                // 主数据（工具特定类型）
  newMessages?: (                        // 可选：工具产生的新消息
    | UserMessage                        //   用户消息
    | AssistantMessage                   //   助手消息
    | AttachmentMessage                  //   附件消息
    | SystemMessage                      //   系统消息
  )[]
  contextModifier?: (context: ToolUseContext) => ToolUseContext  // 上下文修改器
  mcpMeta?: {                            // MCP 协议元数据
    _meta?: Record<string, unknown>
    structuredContent?: Record<string, unknown>
  }
}
```

### 1.5 行为特征方法

每个工具通过一组方法声明自己的行为特征，这些特征影响并发调度、权限判断和 UI 展示：

```typescript
// src/Tool.ts:400-436
// 行为特征声明方法
isConcurrencySafe(input: z.infer<Input>): boolean   // 能否并行执行？
isEnabled(): boolean                                 // 当前是否启用？
isReadOnly(input: z.infer<Input>): boolean           // 是否只读操作？
isDestructive?(input: z.infer<Input>): boolean       // 是否不可逆操作？

// 用户中断行为策略
interruptBehavior?(): 'cancel' | 'block'
// 'cancel' — 用户提交新消息时停止工具并丢弃结果
// 'block'  — 继续运行，新消息等待（默认行为）

// 搜索/读取操作分类（用于 UI 折叠显示）
isSearchOrReadCommand?(input: z.infer<Input>): {
  isSearch: boolean      // 搜索操作（grep, find, glob）
  isRead: boolean        // 读取操作（cat, head, tail）
  isList?: boolean       // 目录列出操作（ls, tree, du）
}
```

注意 `isConcurrencySafe` 默认为 `false`（假设不安全）。这是**安全优先设计**的典型体现——不确定时，选择更保守的行为。

### 1.6 ToolUseContext：执行上下文

`ToolUseContext` 是传递给每个工具的上下文对象，包含工具执行所需的一切：

```typescript
// src/Tool.ts:158-300 (简化)
// 工具执行上下文 — 每个工具调用都能访问的运行时环境
export type ToolUseContext = {
  options: {
    commands: Command[]              // 可用命令列表
    tools: Tools                     // 可用工具列表
    debug: boolean                   // 调试模式
    mainLoopModel: string            // 当前模型名称
    mcpClients: MCPServerConnection[] // MCP 客户端连接
    agentDefinitions: AgentDefinitionsResult // 智能体定义
    thinkingConfig: ThinkingConfig   // Extended Thinking 配置
    // ...
  }
  abortController: AbortController   // 中止控制器（用于取消执行）
  readFileState: FileStateCache      // 文件状态缓存（LRU）
  getAppState(): AppState            // 获取应用全局状态
  setAppState(f: (prev: AppState) => AppState): void // 更新应用状态
  messages: Message[]                // 当前对话消息历史
  agentId?: AgentId                  // 子智能体 ID（仅子智能体上下文有）

  // 文件操作相关
  updateFileHistoryState: (...)      // 更新文件历史（用于 git 归因）
  updateAttributionState: (...)      // 更新提交归因状态

  // UI 交互相关（仅 REPL 上下文）
  setToolJSX?: SetToolJSXFn          // 设置工具 JSX UI
  addNotification?: (notif) => void  // 添加通知
  sendOSNotification?: (opts) => void // 发送系统通知

  // 权限相关
  localDenialTracking?: DenialTrackingState  // 本地拒绝追踪（异步子智能体）
  contentReplacementState?: ContentReplacementState // 内容替换状态
  // ...
}
```

### 1.7 buildTool() 工厂函数与 ToolDef

每个工具通过 `buildTool()` 工厂函数创建，它为常见方法提供安全的默认值：

```typescript
// src/Tool.ts:757-792
// 安全默认值 — 故障关闭原则（fail-closed）
const TOOL_DEFAULTS = {
  isEnabled: () => true,                        // 默认启用
  isConcurrencySafe: (_input?: unknown) => false, // 默认不安全（假设有副作用）
  isReadOnly: (_input?: unknown) => false,       // 默认非只读（假设有写入）
  isDestructive: (_input?: unknown) => false,    // 默认非破坏性
  checkPermissions: (input, _ctx?) =>            // 默认允许（委托给通用权限系统）
    Promise.resolve({ behavior: 'allow', updatedInput: input }),
  toAutoClassifierInput: (_input?) => '',        // 默认跳过分类器
  userFacingName: (_input?) => '',               // 默认空名称
}

// buildTool 是简单的展开合并：{ ...TOOL_DEFAULTS, userFacingName: () => def.name, ...def }
export function buildTool<D extends AnyToolDef>(def: D): BuiltTool<D> {
  return {
    ...TOOL_DEFAULTS,
    userFacingName: () => def.name,  // 默认用户可见名称 = 工具名
    ...def,                          // 工具定义覆盖默认值
  } as BuiltTool<D>
}
```

`ToolDef` 类型使这些方法变为可选的，而 `BuiltTool<D>` 类型保证合并后所有方法都存在：

```typescript
// src/Tool.ts:721-741
// ToolDef：工具定义，可省略 DefaultableToolKeys 中的方法
export type ToolDef<Input, Output, P> =
  Omit<Tool<Input, Output, P>, DefaultableToolKeys> &
  Partial<Pick<Tool<Input, Output, P>, DefaultableToolKeys>>

// BuiltTool<D>：构建后的工具，保证所有方法都存在
// 类型层面精确镜像了运行时 { ...TOOL_DEFAULTS, ...def } 的行为
type BuiltTool<D> = Omit<D, DefaultableToolKeys> & {
  [K in DefaultableToolKeys]-?: K extends keyof D
    ? undefined extends D[K]   // 如果 D 中该方法是可选的
      ? ToolDefaults[K]        // → 使用默认值类型
      : D[K]                   // → 使用 D 的类型
    : ToolDefaults[K]          // D 中没有 → 使用默认值类型
}
```

### 1.8 UI 渲染方法族

Tool 接口还包含丰富的 UI 渲染方法，使每个工具控制自己在终端中的展示方式：

| 方法 | 用途 | 调用时机 |
|------|------|---------|
| `renderToolUseMessage` | 渲染工具调用消息 | 模型发出工具调用时 |
| `renderToolResultMessage` | 渲染工具结果 | 工具执行完成后 |
| `renderToolUseProgressMessage` | 渲染进度状态 | 工具执行期间 |
| `renderToolUseRejectedMessage` | 渲染拒绝消息 | 用户拒绝权限时 |
| `renderToolUseErrorMessage` | 渲染错误消息 | 工具执行出错时 |
| `renderToolUseQueuedMessage` | 渲染排队状态 | 工具等待执行时 |
| `renderGroupedToolUse` | 渲染分组并行调用 | 多个同类工具并行时 |
| `renderToolUseTag` | 渲染附加标签 | 工具调用后（如超时、模型名） |

每个工具可以选择性实现这些方法——未实现时系统使用通用的 Fallback 组件。

### 1.9 Tools 集合类型

最后，`Tools` 是一个简单的只读数组类型：

```typescript
// src/Tool.ts:701
// 使用 readonly Tool[] 而非 Tool[]，防止意外修改工具列表
export type Tools = readonly Tool[]
```

---

## 第二章：工具注册中心 src/tools.ts (389 行)

### 2.1 工具导入与条件加载

`src/tools.ts` 是所有工具的注册中心。它采用三种导入策略：

**策略一：无条件 ES Module 导入** — 始终可用的核心工具：

```typescript
// src/tools.ts:2-11
// 核心工具通过标准 ES Module 导入 — 始终可用
import { AgentTool } from './tools/AgentTool/AgentTool.js'
import { SkillTool } from './tools/SkillTool/SkillTool.js'
import { BashTool } from './tools/BashTool/BashTool.js'
import { FileEditTool } from './tools/FileEditTool/FileEditTool.js'
import { FileReadTool } from './tools/FileReadTool/FileReadTool.js'
import { FileWriteTool } from './tools/FileWriteTool/FileWriteTool.js'
import { GlobTool } from './tools/GlobTool/GlobTool.js'
import { NotebookEditTool } from './tools/NotebookEditTool/NotebookEditTool.js'
import { WebFetchTool } from './tools/WebFetchTool/WebFetchTool.js'
// ... 更多无条件导入
```

**策略二：Feature Flag 条件 require()** — 编译时死代码消除：

```typescript
// src/tools.ts:16-53
// 通过 Feature Flag 控制的工具 — 使用 require() 而非 import
// 当 feature() 返回 false 时，Bun 打包器将整个分支消除

// ANT_ONLY：仅 Anthropic 内部可用的工具
const REPLTool =
  process.env.USER_TYPE === 'ant'        // 内部用户条件
    ? require('./tools/REPLTool/REPLTool.js').REPLTool
    : null                               // 外部用户：工具不存在

// Feature Flag 门控的工具
const SleepTool =
  feature('PROACTIVE') || feature('KAIROS')  // 两个 Flag 之一启用
    ? require('./tools/SleepTool/SleepTool.js').SleepTool
    : null

// 多工具 Feature Flag 组
const cronTools = feature('AGENT_TRIGGERS')
  ? [
      require('./tools/ScheduleCronTool/CronCreateTool.js').CronCreateTool,
      require('./tools/ScheduleCronTool/CronDeleteTool.js').CronDeleteTool,
      require('./tools/ScheduleCronTool/CronListTool.js').CronListTool,
    ]
  : []                                   // Flag 关闭：空数组

const WebBrowserTool = feature('WEB_BROWSER_TOOL')
  ? require('./tools/WebBrowserTool/WebBrowserTool.js').WebBrowserTool
  : null
```

**策略三：延迟 require() 工厂** — 打破循环依赖：

```typescript
// src/tools.ts:62-72
// 延迟加载以打破循环依赖：tools.ts → TeamCreateTool → ... → tools.ts
const getTeamCreateTool = () =>
  require('./tools/TeamCreateTool/TeamCreateTool.js')
    .TeamCreateTool as typeof import('./tools/TeamCreateTool/TeamCreateTool.js').TeamCreateTool
const getTeamDeleteTool = () =>
  require('./tools/TeamDeleteTool/TeamDeleteTool.js')
    .TeamDeleteTool as typeof import('./tools/TeamDeleteTool/TeamDeleteTool.js').TeamDeleteTool
const getSendMessageTool = () =>
  require('./tools/SendMessageTool/SendMessageTool.js')
    .SendMessageTool as typeof import('./tools/SendMessageTool/SendMessageTool.js').SendMessageTool
```

### 2.2 getAllBaseTools()：完整工具池

`getAllBaseTools()` 是工具注册的核心函数，返回当前环境下所有可能可用的工具：

```typescript
// src/tools.ts:193-251
// 注意：此函数必须与 Statsig 缓存配置保持同步（系统提示缓存）
export function getAllBaseTools(): Tools {
  return [
    // 始终可用的核心工具
    AgentTool,                           // 子智能体
    TaskOutputTool,                      // 任务输出
    BashTool,                            // Shell 命令

    // 条件性嵌入搜索工具 — 当 bfs/ugrep 内嵌时跳过 Glob/Grep
    ...(hasEmbeddedSearchTools() ? [] : [GlobTool, GrepTool]),

    ExitPlanModeV2Tool,                  // 退出计划模式
    FileReadTool,                        // 文件读取
    FileEditTool,                        // 文件编辑
    FileWriteTool,                       // 文件写入
    NotebookEditTool,                    // Jupyter 笔记本编辑
    WebFetchTool,                        // 网络请求
    TodoWriteTool,                       // 待办事项
    WebSearchTool,                       // 网络搜索
    TaskStopTool,                        // 停止任务
    AskUserQuestionTool,                 // 向用户提问
    SkillTool,                           // 技能工具
    EnterPlanModeTool,                   // 进入计划模式

    // ANT_ONLY 工具
    ...(process.env.USER_TYPE === 'ant' ? [ConfigTool] : []),
    ...(process.env.USER_TYPE === 'ant' ? [TungstenTool] : []),

    // Feature-Gated 工具（条件展开）
    ...(SuggestBackgroundPRTool ? [SuggestBackgroundPRTool] : []),
    ...(WebBrowserTool ? [WebBrowserTool] : []),
    ...(isTodoV2Enabled()
      ? [TaskCreateTool, TaskGetTool, TaskUpdateTool, TaskListTool] : []),
    ...(isWorktreeModeEnabled()
      ? [EnterWorktreeTool, ExitWorktreeTool] : []),
    getSendMessageTool(),
    ...(isAgentSwarmsEnabled()
      ? [getTeamCreateTool(), getTeamDeleteTool()] : []),
    ...(SleepTool ? [SleepTool] : []),
    ...cronTools,                        // Cron 定时工具组
    ...(RemoteTriggerTool ? [RemoteTriggerTool] : []),
    ...(WorkflowTool ? [WorkflowTool] : []),
    BriefTool,                           // 简洁输出工具

    // MCP 资源工具
    ListMcpResourcesTool,
    ReadMcpResourceTool,
    // ToolSearch 工具（当工具数量超过阈值时启用）
    ...(isToolSearchEnabledOptimistic() ? [ToolSearchTool] : []),
  ]
}
```

### 2.3 getTools()：权限过滤后的工具列表

`getTools()` 在 `getAllBaseTools()` 基础上应用权限过滤和模式适配：

```typescript
// src/tools.ts:271-327
export const getTools = (permissionContext: ToolPermissionContext): Tools => {
  // 简单模式：仅 Bash、Read、Edit
  if (isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)) {
    const simpleTools: Tool[] = [BashTool, FileReadTool, FileEditTool]
    // 协调器模式额外添加 AgentTool 和 TaskStopTool
    if (feature('COORDINATOR_MODE') &&
        coordinatorModeModule?.isCoordinatorMode()) {
      simpleTools.push(AgentTool, TaskStopTool, getSendMessageTool())
    }
    return filterToolsByDenyRules(simpleTools, permissionContext)
  }

  // 获取所有基础工具并过滤特殊工具
  const tools = getAllBaseTools().filter(tool => !specialTools.has(tool.name))

  // 按权限规则过滤拒绝的工具
  let allowedTools = filterToolsByDenyRules(tools, permissionContext)

  // REPL 模式：隐藏原始工具（它们在 REPL VM 内部可用）
  if (isReplModeEnabled()) {
    const replEnabled = allowedTools.some(t => toolMatchesName(t, REPL_TOOL_NAME))
    if (replEnabled) {
      allowedTools = allowedTools.filter(t => !REPL_ONLY_TOOLS.has(t.name))
    }
  }

  // 最终过滤：只保留 isEnabled() 返回 true 的工具
  const isEnabled = allowedTools.map(_ => _.isEnabled())
  return allowedTools.filter((_, i) => isEnabled[i])
}
```

### 2.4 assembleToolPool()：合并内置工具与 MCP 工具

最终的工具池由 `assembleToolPool()` 组装，合并内置工具和 MCP 外部工具：

```typescript
// src/tools.ts:345-367
// 合并内置工具与 MCP 工具的完整工具池
// REPL.tsx（通过 useMergedTools Hook）和 runAgent.ts 都使用此函数
export function assembleToolPool(
  permissionContext: ToolPermissionContext,
  mcpTools: Tools,
): Tools {
  const builtInTools = getTools(permissionContext)

  // 按权限规则过滤 MCP 工具
  const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext)

  // 排序策略：内置工具作为连续前缀，MCP 工具附加在后面
  // 这保证了系统提示缓存的稳定性 — 如果 MCP 工具插入到内置工具之间，
  // 会使所有下游缓存键失效
  const byName = (a: Tool, b: Tool) => a.name.localeCompare(b.name)
  return uniqBy(
    [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
    'name',                              // 按名称去重，内置工具优先
  )
}
```

### 2.5 filterToolsByDenyRules()：权限预过滤

在模型看到工具列表之前，被全局拒绝的工具就已经被移除：

```typescript
// src/tools.ts:262-269
// 过滤被权限规则全局拒绝的工具
// 使用与运行时权限检查相同的匹配器（getDenyRuleForTool）
// MCP 服务器前缀规则（如 mcp__server）会在此阶段移除该服务器的所有工具
export function filterToolsByDenyRules<T extends { name: string; mcpInfo?: {...} }>(
  tools: readonly T[],
  permissionContext: ToolPermissionContext,
): T[] {
  return tools.filter(tool => !getDenyRuleForTool(permissionContext, tool))
}
```

### 2.6 工具加载架构图

```
┌─────────────────────────────────────────────────────────┐
│                   工具加载流程                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  编译时                                                  │
│  ┌─────────────┐   ┌──────────────┐                     │
│  │ feature()   │──→│ DCE 消除      │  require() 分支     │
│  │ Flag 检查    │   │ 死代码        │  被打包器消除        │
│  └─────────────┘   └──────────────┘                     │
│                                                         │
│  运行时                                                  │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐  │
│  │ getAllBase   │──→│ getTools()   │──→│ assembleTool  │  │
│  │ Tools()     │   │ 权限过滤      │   │ Pool()       │  │
│  │ 全部候选     │   │ 模式适配      │   │ + MCP 合并   │  │
│  └─────────────┘   │ isEnabled    │   │ + 排序去重    │  │
│                    └──────────────┘   └──────────────┘  │
│                                                         │
│  过滤链：                                                │
│  全部工具 → filterByDenyRules → REPL模式过滤              │
│         → isEnabled过滤 → MCP合并 → 按名称排序去重         │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 第三章：文件操作类工具

文件操作类工具是 Claude Code 最基础、使用最频繁的工具组。它们覆盖了从命令执行到文件读写、搜索的完整文件系统交互能力。

### 3.1 BashTool — Shell 命令执行 (12,411 行, 18 个文件)

BashTool 是整个工具系统中**最大、最复杂**的工具，其代码量超过所有其他工具之和。它负责在 Shell 中执行任意命令，是 Claude Code 与操作系统交互的核心通道。

**输入 Schema：**

```typescript
// src/tools/BashTool/BashTool.tsx:227-247
const fullInputSchema = lazySchema(() => z.strictObject({
  command: z.string()                    // 要执行的命令（必需）
    .describe('The command to execute'),
  timeout: semanticNumber(              // 超时时间（毫秒）
    z.number().optional()
  ).describe(`Optional timeout in milliseconds (max ${getMaxTimeoutMs()})`),
  description: z.string().optional()     // 命令描述（给用户看）
    .describe('Clear, concise description of what this command does...'),
  run_in_background: semanticBoolean(    // 是否后台运行
    z.boolean().optional()
  ).describe('Set to true to run this command in the background...'),
  dangerouslyDisableSandbox: semanticBoolean( // 禁用沙箱
    z.boolean().optional()
  ).describe('Set this to true to dangerously override sandbox mode...'),
  _simulatedSedEdit: z.object({          // 内部：sed 编辑预览结果
    filePath: z.string(),                // （不暴露给模型）
    newContent: z.string()
  }).optional()
}))

// 安全处理：_simulatedSedEdit 字段从模型可见 schema 中移除
// 如果暴露，模型可以绕过权限检查，用无害命令搭配任意文件写入
const inputSchema = lazySchema(() =>
  fullInputSchema().omit({ _simulatedSedEdit: true })
)
```

**核心架构——18 个文件的职责分工：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `BashTool.tsx` | 主文件 | 工具定义、`call()` 方法、命令执行循环 |
| `bashPermissions.ts` | - | 命令权限检查、通配符匹配 |
| `bashSecurity.ts` | - | 安全策略（命令黑名单、危险命令检测） |
| `commandSemantics.ts` | - | 命令结果语义解释（非零退出码含义） |
| `readOnlyValidation.ts` | - | 只读模式约束检查 |
| `shouldUseSandbox.ts` | - | 沙箱使用决策 |
| `sedEditParser.ts` | - | `sed -i` 编辑命令解析器 |
| `sedValidation.ts` | - | sed 编辑的安全验证 |
| `pathValidation.ts` | - | 路径验证 |
| `modeValidation.ts` | - | 模式验证 |
| `prompt.ts` | - | 系统提示词生成 |
| `UI.tsx` | - | 进度和结果渲染 |

**命令执行流程：**

```
用户/模型发起 Bash 调用
        │
        ▼
  ┌─────────────┐
  │ validateInput│  检查命令合法性
  └──────┬──────┘
         │
         ▼
  ┌──────────────┐
  │ checkPerms   │  bashPermissions.ts: 权限规则匹配
  │              │  parseForSecurity(): AST 级命令解析
  │              │  shouldUseSandbox(): 沙箱决策
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │ call()       │  runShellCommand() 异步生成器
  │              │  stdoutAccumulator 端截断
  │              │  进度流式报告（每 2 秒）
  │              │  超时控制
  └──────┬───────┘
         │
         ├── 正常完成 → interpretCommandResult() 语义解释
         ├── 后台运行 → backgroundTaskId 返回
         ├── 超时中断 → wasInterrupted: true
         └── 图片输出 → resizeShellImageOutput() → base64
```

**关键安全机制：**

1. **AST 级命令解析**（`parseForSecurity()`）：不是简单的字符串匹配，而是解析 Shell 命令的抽象语法树，确保 `ls && git push` 中的 `git push` 也能被安全规则捕获
2. **沙箱支持**：`shouldUseSandbox()` 决定是否在沙箱中执行命令
3. **sed 编辑拦截**：`parseSedEditCommand()` 解析 `sed -i` 命令，将其转换为预览+确认模式，确保用户看到的修改就是实际写入的修改
4. **命令语义解释**：`interpretCommandResult()` 为非零退出码提供语义解释（如 `grep` 的退出码 1 表示"无匹配"而非错误）
5. **自动后台化**：在 assistant 模式下，阻塞命令超过 15 秒自动转为后台任务

**搜索/读取命令分类：**

```typescript
// src/tools/BashTool/BashTool.tsx:60-72
// 搜索类命令（UI 中折叠显示）
const BASH_SEARCH_COMMANDS = new Set([
  'find', 'grep', 'rg', 'ag', 'ack', 'locate', 'which', 'whereis'
])
// 读取类命令
const BASH_READ_COMMANDS = new Set([
  'cat', 'head', 'tail', 'less', 'more',
  'wc', 'stat', 'file', 'strings',
  'jq', 'awk', 'cut', 'sort', 'uniq', 'tr'
])
// 目录列出命令
const BASH_LIST_COMMANDS = new Set(['ls', 'tree', 'du'])
// 语义中性命令（不改变管道的搜索/读取性质）
const BASH_SEMANTIC_NEUTRAL_COMMANDS = new Set(['echo', 'printf', 'true', 'false', ':'])
```

### 3.2 FileReadTool — 文件读取 (1,602 行)

FileReadTool 支持多种文件类型的智能读取，是 Claude 理解代码库的主要通道。

**输入 Schema：**

```typescript
// src/tools/FileReadTool/FileReadTool.ts:227-242
const inputSchema = lazySchema(() =>
  z.strictObject({
    file_path: z.string()                // 文件绝对路径
      .describe('The absolute path to the file to read'),
    offset: semanticNumber(              // 起始行号（大文件分段读取）
      z.number().int().nonnegative().optional()
    ).describe('The line number to start reading from...'),
    limit: semanticNumber(               // 读取行数（大文件分段读取）
      z.number().int().positive().optional()
    ).describe('The number of lines to read...'),
    pages: z.string().optional()          // PDF 页码范围
      .describe('Page range for PDF files (e.g., "1-5", "3", "10-20")...'),
  })
)
```

**多格式输出——辨别联合类型：**

```typescript
// src/tools/FileReadTool/FileReadTool.ts:257-331
// 输出使用 Zod discriminatedUnion — 按 type 字段区分 6 种输出格式
const outputSchema = lazySchema(() =>
  z.discriminatedUnion('type', [
    z.object({ type: z.literal('text'), file: z.object({    // 文本文件
      filePath: z.string(),    content: z.string(),
      numLines: z.number(),    startLine: z.number(),
      totalLines: z.number()
    })}),
    z.object({ type: z.literal('image'), file: z.object({   // 图片文件
      base64: z.string(),      type: imageMediaTypes,        // JPEG/PNG/GIF/WebP
      originalSize: z.number(), dimensions: z.object({...}).optional()
    })}),
    z.object({ type: z.literal('notebook'), file: z.object({ // Jupyter 笔记本
      filePath: z.string(),    cells: z.array(z.any())
    })}),
    z.object({ type: z.literal('pdf'), file: z.object({     // PDF 文件
      filePath: z.string(),    base64: z.string(),
      originalSize: z.number()
    })}),
    z.object({ type: z.literal('parts'), file: z.object({   // PDF 分页图片
      filePath: z.string(),    count: z.number(),
      outputDir: z.string()
    })}),
    z.object({ type: z.literal('file_unchanged'), file: ... }), // 文件未变
  ])
)
```

**关键特性：**
- `maxResultSizeChars: Infinity` — 永不持久化到磁盘（因为持久化后模型用 Read 读回会产生循环）
- 文件状态缓存（`readFileState`）：LRU 缓存已读文件的内容和修改时间，避免重复读取未变的文件
- 语言自动检测：根据文件扩展名推断代码语言，用于语法高亮
- 行号截断：对于大文件，自动限制返回行数，提示模型使用 offset/limit 分段读取

### 3.3 FileEditTool — 文件编辑 (1,812 行)

FileEditTool 通过精确的字符串替换修改文件内容。它不是覆写整个文件，而是定位并替换特定文本片段。

**输入 Schema：**

```typescript
// src/tools/FileEditTool/types.ts:6-19
const inputSchema = lazySchema(() =>
  z.strictObject({
    file_path: z.string()                // 文件绝对路径
      .describe('The absolute path to the file to modify'),
    old_string: z.string()               // 要被替换的文本
      .describe('The text to replace'),
    new_string: z.string()               // 替换后的新文本
      .describe('The text to replace it with (must be different from old_string)'),
    replace_all: semanticBoolean(        // 是否替换所有匹配项
      z.boolean().default(false).optional()
    ).describe('Replace all occurrences of old_string (default false)'),
  })
)
```

**安全机制三层防护：**

```typescript
// src/tools/FileEditTool/FileEditTool.ts:137-181
async validateInput(input: FileEditInput, toolUseContext: ToolUseContext) {
  const fullFilePath = expandPath(file_path)

  // 第 1 层：团队记忆文件的秘密检测
  const secretError = checkTeamMemSecrets(fullFilePath, new_string)
  if (secretError) {
    return { result: false, message: secretError, errorCode: 0 }
  }

  // 第 2 层：无变更检测
  if (old_string === new_string) {
    return { result: false, message: 'No changes to make...', errorCode: 1 }
  }

  // 第 3 层：UNC 路径保护（防止 NTLM 凭据泄露）
  // Windows 上，对 UNC 路径的 fs.existsSync() 调用会触发 SMB 认证，
  // 可能将凭据泄露到恶意服务器
  if (fullFilePath.startsWith('\\\\') || fullFilePath.startsWith('//')) {
    return { result: true }  // 跳过文件系统操作，让权限检查处理
  }

  // 第 4 层：文件大小限制（防止 OOM）
  const { size } = await fs.stat(fullFilePath)
  if (size > MAX_EDIT_FILE_SIZE) {    // 1 GiB 上限
    return { result: false, message: `File is too large to edit...`, errorCode: 10 }
  }
  // ...
}
```

**编辑核心逻辑：**
- 字符串精确匹配：在文件内容中查找 `old_string`，替换为 `new_string`
- 唯一性要求：默认模式下 `old_string` 必须在文件中唯一出现，否则报错
- `replace_all: true`：替换所有匹配项
- 行尾保留：保持文件原有的行尾格式（LF/CRLF）
- 编码保留：保持文件原有编码
- Git diff 生成：生成 unified diff 用于 UI 展示和历史追踪

### 3.4 FileWriteTool — 文件创建/覆写 (856 行)

FileWriteTool 用于创建新文件或完全覆写现有文件。它比 FileEditTool 更"重"，通常用于创建全新文件。

```typescript
// FileWriteTool 的核心字段
name: 'Write'                            // 工具名称
inputSchema: {
  file_path: z.string()                  // 文件绝对路径
  content: z.string()                    // 文件内容
}
```

**关键特性：**
- 自动创建目录：如果父目录不存在，自动递归创建
- 文件历史追踪：`fileHistoryTrackEdit()` 记录修改前后状态
- 条件技能发现：`discoverSkillDirsForPaths()` 检测新目录是否包含技能定义
- 团队记忆秘密检测：与 FileEditTool 共享 `checkTeamMemSecrets()` 机制
- LSP 诊断清除：写入后清除该文件的 LSP 诊断缓存
- VS Code 通知：`notifyVscodeFileUpdated()` 通知 IDE 文件已变更

### 3.5 GlobTool — 文件模式搜索 (267 行)

GlobTool 使用 glob 模式匹配文件路径，是 Claude 发现文件结构的主要工具。

```typescript
// src/tools/GlobTool/GlobTool.ts:26-36
const inputSchema = lazySchema(() =>
  z.strictObject({
    pattern: z.string()                  // glob 模式（如 "**/*.ts"）
      .describe('The glob pattern to match files against'),
    path: z.string().optional()          // 搜索目录（默认为 cwd）
      .describe('The directory to search in...'),
  })
)
```

**关键特性：**
- 底层实现：使用 `src/utils/glob.ts` 中的 glob 实现
- 结果限制：默认最多返回 100 个文件（通过 `globLimits.maxResults` 配置）
- 结果排序：按修改时间排序，最近修改的文件排在前面
- 路径相对化：结果转换为相对路径显示
- 权限检查：通过 `checkReadPermissionForTool()` 验证目录访问权限

### 3.6 GrepTool — 内容搜索 (795 行)

GrepTool 基于 ripgrep 实现高性能的文件内容搜索，支持正则表达式和丰富的过滤选项。

```typescript
// src/tools/GrepTool/GrepTool.ts:33-90
const inputSchema = lazySchema(() =>
  z.strictObject({
    pattern: z.string()                  // 正则表达式模式
      .describe('The regular expression pattern to search for...'),
    path: z.string().optional()          // 搜索路径（文件或目录）
      .describe('File or directory to search in...'),
    glob: z.string().optional()          // 文件过滤 glob
      .describe('Glob pattern to filter files (e.g. "*.js")...'),
    output_mode: z.enum([               // 输出模式
      'content',                         // 显示匹配行
      'files_with_matches',              // 仅显示文件路径（默认）
      'count'                            // 显示匹配计数
    ]).optional(),
    '-B': semanticNumber(...).optional(), // 匹配前上下文行数
    '-A': semanticNumber(...).optional(), // 匹配后上下文行数
    '-C': semanticNumber(...).optional(), // 上下文行数（前后）
    context: semanticNumber(...).optional(),
    '-n': semanticBoolean(...).optional(), // 显示行号
    '-i': semanticBoolean(...).optional(), // 大小写不敏感
    type: z.string().optional(),          // 文件类型过滤
    head_limit: semanticNumber(...).optional(), // 结果数量限制（默认 250）
    offset: semanticNumber(...).optional(),     // 跳过前 N 项
    multiline: semanticBoolean(...).optional(), // 多行匹配模式
  })
)
```

**关键特性：**
- 底层实现：调用 `ripGrep()` 封装的 ripgrep 命令行工具
- 自动排除：版本控制目录（`.git`, `.svn`, `.hg`, `.bzr`）自动排除
- 插件缓存排除：`getGlobExclusionsForPluginCache()` 排除插件缓存目录
- 结果截断：默认 `head_limit: 250`，大结果集通过 offset 分页
- 三种输出模式：`files_with_matches`（发现文件）、`content`（查看匹配行）、`count`（统计匹配数）

---

## 第四章：网络与智能体类工具

网络与智能体类工具扩展了 Claude Code 的能力边界，从本地文件系统延伸到网络资源和多智能体协作。

### 4.1 WebFetchTool — 网络请求 (1,131 行)

WebFetchTool 使 Claude 能够获取网页内容和 API 响应。

```typescript
// WebFetchTool 核心定义
name: 'WebFetch'
searchHint: 'download web pages, fetch URLs, HTTP requests'
inputSchema: {
  url: z.string()                        // 请求 URL
  prompt: z.string().optional()          // 可选提示（指导内容提取）
  format: z.enum(['text', 'markdown']).optional() // 输出格式
}
```

**关键特性：**
- HTML 到 Markdown 转换：自动将网页 HTML 转换为 Markdown 格式
- 内容截断：大型页面自动截断，避免耗尽上下文窗口
- URL 安全验证：检查 URL 合法性，防止 SSRF 攻击
- 独立于浏览器：使用 HTTP 客户端直接请求，不依赖浏览器引擎

### 4.2 WebSearchTool — 网络搜索 (569 行)

WebSearchTool 使 Claude 能够搜索互联网获取实时信息。

```typescript
// WebSearchTool 核心定义
name: 'WebSearch'
searchHint: 'search the internet, find information online'
inputSchema: {
  query: z.string()                      // 搜索查询
  allowed_domains: z.array(z.string()).optional()  // 域名白名单
}
```

### 4.3 AgentTool — 子智能体 (6,782 行)

AgentTool 是多智能体协作的核心工具，允许 Claude 生成独立的子智能体来并行处理复杂任务。这是工具系统中第二大的工具（仅次于 BashTool），其复杂性源于子智能体的生命周期管理、权限隔离和通信机制。

```typescript
// AgentTool 核心定义
name: 'Agent'
searchHint: 'launch autonomous sub-agent for complex tasks'
inputSchema: {
  prompt: z.string()                     // 子智能体的任务描述
  description: z.string()               // 简短描述（3-5 词）
  subagent_type: z.string().optional()   // 智能体类型
  model: z.enum([...]).optional()        // 模型覆盖
  name: z.string().optional()            // 智能体名称
  isolation: z.enum(['worktree']).optional() // 隔离模式
  mode: z.enum([...]).optional()         // 权限模式
  run_in_background: z.boolean().optional() // 后台运行
}
```

**关键特性：**
- 独立上下文：每个子智能体拥有独立的消息历史和状态
- 权限继承与隔离：子智能体继承父智能体的权限上下文，但有自己的拒绝追踪
- Worktree 隔离：`isolation: 'worktree'` 在独立的 git worktree 中运行
- 后台执行：`run_in_background: true` 支持异步并行
- 模型选择：子智能体可以使用不同的模型
- 工具限制：子智能体默认被限制不能使用某些工具（`ALL_AGENT_DISALLOWED_TOOLS`）

> 子智能体架构的完整分析将在 Doc 10（多智能体系统）中深入展开。

### 4.4 TeamCreateTool / TeamDeleteTool — 团队管理 (359 + 175 行)

这两个工具管理智能体团队（Agent Swarms）的生命周期。

```typescript
// TeamCreateTool 核心定义
name: 'TeamCreate'
inputSchema: {
  team_name: z.string()                  // 团队名称
}

// TeamDeleteTool 核心定义
name: 'TeamDelete'
inputSchema: {
  team_name: z.string()                  // 要删除的团队名称
}
```

通过延迟 `require()` 加载以打破循环依赖链，仅在 `isAgentSwarmsEnabled()` 返回 `true` 时可用。

### 4.5 SendMessageTool — 智能体间通信 (997 行)

SendMessageTool 实现智能体之间的消息传递，支持团队成员间的协作。

```typescript
// SendMessageTool 核心定义
name: 'SendMessage'
inputSchema: {
  to: z.string()                         // 目标智能体名称或 ID
  prompt: z.string()                     // 消息内容
}
```

它是智能体协作网络的通信原语——主智能体可以向子智能体发送指令，子智能体可以向团队成员发送消息。

### 4.6 EnterWorktreeTool / ExitWorktreeTool — 工作树隔离 (177 + 386 行)

这对工具管理 git worktree 的进出，为子智能体提供文件系统级别的隔离。

```typescript
// EnterWorktreeTool 核心定义
name: 'EnterWorktree'
inputSchema: {
  // 通常无参数，自动创建临时 worktree
}

// ExitWorktreeTool 核心定义
name: 'ExitWorktree'
inputSchema: {
  // 退出并清理或保留 worktree 更改
}
```

仅在 `isWorktreeModeEnabled()` 返回 `true` 时可用。Worktree 提供了完整的代码隔离——子智能体在独立的目录副本中工作，不影响主分支。

### 4.7 TaskCreateTool / TaskUpdateTool / TaskGetTool / TaskListTool — 任务管理 (195 + 484 + 153 + 166 行)

任务管理工具组（Todo v2），仅在 `isTodoV2Enabled()` 时可用：

```typescript
// TaskCreateTool
name: 'TaskCreate'
inputSchema: { description: z.string(), status: z.enum([...]).optional() }

// TaskUpdateTool
name: 'TaskUpdate'
inputSchema: { task_id: z.string(), status: z.enum([...]), description: z.string().optional() }

// TaskGetTool
name: 'TaskGet'
inputSchema: { task_id: z.string() }

// TaskListTool
name: 'TaskList'
inputSchema: { /* 无必需参数 */ }
```

### 4.8 TodoWriteTool — 待办事项 (300 行)

TodoWriteTool 是更简单的任务管理工具，始终可用。

```typescript
// TodoWriteTool 核心定义
name: 'TodoWrite'
inputSchema: {
  todos: z.array(z.object({
    content: z.string(),                 // 任务描述
    status: z.enum(['pending', 'in_progress', 'completed']),
    activeForm: z.string()               // 进行时描述
  }))
}
```

### 4.9 TaskOutputTool / TaskStopTool — 任务输出与停止 (584 + 179 行)

```typescript
// TaskOutputTool — 获取后台任务输出
name: 'TaskOutput'
inputSchema: { task_id: z.string() }

// TaskStopTool — 停止运行中的任务
name: 'TaskStop'
inputSchema: { task_id: z.string() }
```

### 4.10 ScheduleCronTool — 定时任务 (543 行, 3 个工具)

定时任务工具组，仅在 `feature('AGENT_TRIGGERS')` 启用时可用：

```typescript
// CronCreateTool — 创建定时任务
name: 'CronCreate'
inputSchema: {
  schedule: z.string()                   // Cron 表达式
  prompt: z.string()                     // 触发时执行的提示
  // ...
}

// CronDeleteTool — 删除定时任务
name: 'CronDelete'
inputSchema: { cron_id: z.string() }

// CronListTool — 列出定时任务
name: 'CronList'
inputSchema: { /* 无参数 */ }
```

### 4.11 SkillTool — 技能执行 (1,477 行)

SkillTool 是命令系统和工具系统的桥梁——它允许模型通过工具调用的方式触发斜杠命令（技能）。

```typescript
// SkillTool 核心定义
name: 'Skill'
inputSchema: {
  skill: z.string()                      // 技能名称
  args: z.string().optional()            // 可选参数
}
```

**关键特性：**
- 双向桥梁：用户通过 `/command` 触发命令，模型通过 `SkillTool` 触发同一命令
- 技能发现：模型可以搜索可用技能列表
- 版本管理：技能可以有版本号，支持向后兼容

### 4.12 MCPTool — Model Context Protocol 工具 (1,086 行)

MCPTool 是 MCP 协议的运行时载体。外部 MCP 服务器提供的工具通过 MCPTool 的工厂模式实例化为标准 Tool 接口。

**关键特性：**
- 动态工具发现：MCP 服务器在连接时注册其工具
- Schema 转换：MCP 的 JSON Schema 转换为系统内部的 Zod schema
- 服务器前缀命名：工具名格式为 `mcp__serverName__toolName`
- 权限隔离：可以按 MCP 服务器前缀批量拒绝所有工具

### 4.13 LSPTool — 语言服务器协议工具 (2,005 行)

LSPTool 集成了 Language Server Protocol，提供代码智能功能。

```typescript
// LSPTool 核心定义
name: 'LSP'
searchHint: 'code intelligence, diagnostics, type checking'
inputSchema: {
  command: z.enum([...])                 // LSP 命令类型
  file_path: z.string().optional()       // 文件路径
  // ...
}
```

仅在 `ENABLE_LSP_TOOL` 环境变量启用时可用。

### 4.14 NotebookEditTool — Jupyter 笔记本编辑 (587 行)

NotebookEditTool 支持对 Jupyter Notebook (.ipynb) 文件的结构化编辑。

```typescript
// NotebookEditTool 核心定义
name: 'NotebookEdit'
searchHint: 'edit jupyter notebook cells, ipynb files'
inputSchema: {
  notebook_path: z.string()              // 笔记本文件路径
  command: z.enum([                      // 编辑命令
    'add_cell', 'edit_cell',
    'delete_cell', 'move_cell'
  ])
  // ...
}
```

### 4.15 其他工具

| 工具名 | 行数 | 描述 | 可用条件 |
|--------|------|------|---------|
| `AskUserQuestion` | 309 | 向用户提出问题 | 始终可用 |
| `EnterPlanMode` | 329 | 进入计划模式 | 始终可用 |
| `ExitPlanModeV2` | 605 | 退出计划模式 | 始终可用 |
| `BriefTool` | 610 | 控制输出详略 | 始终可用 |
| `ToolSearchTool` | 593 | 搜索延迟加载的工具 | 工具数量超阈值时 |
| `ConfigTool` | 809 | 配置管理 | ANT_ONLY |
| `TungstenTool` | — | Tungsten 集成 | ANT_ONLY |
| `RemoteTriggerTool` | 192 | 远程触发 | `AGENT_TRIGGERS_REMOTE` |
| `ListMcpResourcesTool` | 171 | 列出 MCP 资源 | 始终可用 |
| `ReadMcpResourceTool` | 210 | 读取 MCP 资源 | 始终可用 |

### 4.16 工具系统完整分类表

```
┌──────────────────────────────────────────────────────────────┐
│                    工具系统分类总览                            │
├────────────┬────────────────────────────────────────────────┤
│ 文件操作    │ Bash(12K) Read(1.6K) Edit(1.8K) Write(856)    │
│            │ Glob(267) Grep(795) NotebookEdit(587)          │
├────────────┼────────────────────────────────────────────────┤
│ 网络       │ WebFetch(1.1K) WebSearch(569)                  │
├────────────┼────────────────────────────────────────────────┤
│ 智能体     │ Agent(6.8K) TeamCreate(359) TeamDelete(175)    │
│            │ SendMessage(997)                                │
├────────────┼────────────────────────────────────────────────┤
│ 任务管理    │ TodoWrite(300) TaskCreate(195)                 │
│            │ TaskUpdate(484) TaskGet(153) TaskList(166)      │
│            │ TaskOutput(584) TaskStop(179)                   │
├────────────┼────────────────────────────────────────────────┤
│ 工作树     │ EnterWorktree(177) ExitWorktree(386)           │
├────────────┼────────────────────────────────────────────────┤
│ 定时触发    │ CronCreate CronDelete CronList (543 合计)      │
│            │ RemoteTrigger(192)                              │
├────────────┼────────────────────────────────────────────────┤
│ 扩展集成    │ Skill(1.5K) MCPTool(1.1K) LSP(2K)             │
│            │ ListMcpResources(171) ReadMcpResources(210)    │
│            │ ToolSearch(593)                                 │
├────────────┼────────────────────────────────────────────────┤
│ 控制流     │ EnterPlanMode(329) ExitPlanModeV2(605)         │
│            │ AskUserQuestion(309) Brief(610)                 │
├────────────┼────────────────────────────────────────────────┤
│ ANT_ONLY   │ REPLTool(85) Config(809) Tungsten              │
│            │ SuggestBackgroundPR PowerShell(9K)              │
└────────────┴────────────────────────────────────────────────┘
```

总计：**40+ 个工具**，核心代码量约 **50,000 行**，其中 BashTool 独占近 25%。

---

## 第五章：Feature-Gated 工具

Part 1 展示了核心工具（BashTool、FileReadTool 等）和网络/智能体类工具。但工具系统中有大量工具**并非始终可用**——它们受 Feature Flag、平台检测、用户类型等条件门控。这些条件门控在 `src/tools.ts` 的顶层通过条件 `require()` 实现，使得 Bun 的死代码消除（DCE）能在编译时移除整个工具的代码。

### 5.1 REPLTool (ANT_ONLY)

```typescript
// src/tools.ts:16-19
// 条件导入：仅 Anthropic 内部用户加载 REPLTool
// process.env.USER_TYPE 在构建时确定，DCE 移除 external 构建中的 require()
const REPLTool =
  process.env.USER_TYPE === 'ant'           // 编译时常量判断
    ? require('./tools/REPLTool/REPLTool.js').REPLTool  // 内部用户：加载
    : null                                  // 外部用户：null，后续 .filter(Boolean) 移除
```

REPLTool 是一个**包装型工具**：它将 Bash、Read、Edit 等原始工具封装在一个 VM 上下文中，当启用时会隐藏原始工具，让模型只与 REPLTool 交互。可通过 `CLAUDE_CODE_REPL=0` 禁用。

### 5.2 SleepTool (PROACTIVE / KAIROS)

```typescript
// src/tools.ts:25-28
// SleepTool 用于主动式智能体的等待场景
// 比 Bash(sleep ...) 更优：不占用 shell 进程，支持用户随时中断
const SleepTool =
  feature('PROACTIVE') || feature('KAIROS')  // 两个 Feature Flag 之一启用即可
    ? require('./tools/SleepTool/SleepTool.js').SleepTool
    : null
```

SleepTool 专为后台智能体设计——当智能体需要等待（例如等待 CI 结果、等待用户操作完成），它使用 SleepTool 暂停而非阻塞 shell。支持并发执行，用户可在等待期间中断。

### 5.3 PowerShellTool (平台检测 + 用户类型)

```typescript
// src/utils/shell/shellToolUtils.ts:17-22
// PowerShellTool 启用逻辑：多维度条件组合
// 1. 必须是 Windows 平台
// 2. ANT 用户默认开启（CLAUDE_CODE_USE_POWERSHELL_TOOL=0 可关闭）
// 3. 外部用户默认关闭（CLAUDE_CODE_USE_POWERSHELL_TOOL=1 可开启）
```

PowerShellTool 展示了**跨平台适配**模式：在 Windows 上替代 BashTool 的角色，支持 Win32 特有的路径规范化和权限检查。内部用户默认启用（opt-out），外部用户默认关闭（opt-in）——这是渐进信任在平台层面的体现。

### 5.4 WebBrowserTool (WEB_BROWSER_TOOL)

```typescript
// src/tools.ts:117-119
// 浏览器工具：受 Feature Flag 门控的实验性功能
// 编译时决定是否包含，当 Flag 关闭时整个工具代码不进入构建产物
```

WebBrowserTool 是 Feature Flag 系统的经典用例——通过编译时门控控制实验性功能的发布节奏，不同构建可以包含或排除此工具。

### 5.5 Cron/Scheduler 工具族 (AGENT_TRIGGERS)

```typescript
// src/tools.ts:29-35
// Cron 工具族：一个 Feature Flag 门控三个工具
// 与单工具不同，这里返回一个数组
const cronTools = feature('AGENT_TRIGGERS')
  ? [
      require('./tools/ScheduleCronTool/CronCreateTool.js').CronCreateTool,  // 创建定时任务
      require('./tools/ScheduleCronTool/CronDeleteTool.js').CronDeleteTool,  // 删除定时任务
      require('./tools/ScheduleCronTool/CronListTool.js').CronListTool,      // 列出定时任务
    ]
  : []   // 注意：空数组而非 null，因为后续通过 ...cronTools 展开
```

Cron 工具族展示了工具分组模式——三个工具共享一个 Feature Flag，通过数组展开统一注入工具池。运行时还有二次门控：`tengu_kairos_cron` GrowthBook 标志控制是否真正调度任务。

### 5.6 KAIROS 系列工具

| 工具名 | Feature Flag | 描述 |
|--------|-------------|------|
| `SendUserFileTool` | `KAIROS` | 向用户发送文件 |
| `PushNotificationTool` | `KAIROS` 或 `KAIROS_PUSH_NOTIFICATION` | 推送通知给用户设备 |
| `SubscribePRTool` | `KAIROS_GITHUB_WEBHOOKS` | 订阅 GitHub PR 事件 |
| `RemoteTriggerTool` | `AGENT_TRIGGERS_REMOTE` | 管理远程定时触发 |

这些工具支撑 KAIROS 后台智能体模式——智能体在后台持续运行，通过推送通知、文件发送、PR 订阅等方式与用户保持异步通信。

### 5.7 其他条件工具

| 工具名 | 条件 | 描述 |
|--------|------|------|
| `MonitorTool` | `MONITOR_TOOL` Flag | 监控工具 |
| `SnipTool` | `HISTORY_SNIP` Flag | 历史片段管理 |
| `WorkflowTool` | `WORKFLOW_SCRIPTS` Flag | 工作流脚本执行 |
| `ListPeersTool` | `UDS_INBOX` Flag | 列出 UDS 对等连接 |
| `LSPTool` | `ENABLE_LSP_TOOL` 环境变量 | 语言服务器协议工具 |
| `TeamCreate/DeleteTool` | `isAgentSwarmsEnabled()` | 团队管理（运行时检测） |
| `EnterWorktree/ExitWorktree` | `isWorktreeModeEnabled()` | Git Worktree 隔离 |
| `TaskTools` (4个) | `isTodoV2Enabled()` | 任务管理系统 |
| `ToolSearchTool` | `isToolSearchEnabledOptimistic()` | 延迟工具搜索发现 |

### 5.8 Feature-Gated 工具分层图

```
┌─────────────────────────────────────────────────────────────────────┐
│                     工具门控层次结构                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  第 1 层：编译时门控（feature() — 死代码消除）                         │
│  ├── PROACTIVE/KAIROS → SleepTool                                   │
│  ├── AGENT_TRIGGERS → CronCreate/Delete/List                        │
│  ├── AGENT_TRIGGERS_REMOTE → RemoteTrigger                          │
│  ├── KAIROS → SendUserFile, PushNotification                        │
│  ├── WEB_BROWSER_TOOL → WebBrowser                                  │
│  ├── MONITOR_TOOL → Monitor                                         │
│  └── HISTORY_SNIP/WORKFLOW_SCRIPTS/UDS_INBOX → ...                  │
│                                                                     │
│  第 2 层：构建时门控（process.env.USER_TYPE — 用户类型）              │
│  ├── ant → REPLTool, SuggestBackgroundPR, Config, Tungsten          │
│  └── external → 上述工具编译时移除                                    │
│                                                                     │
│  第 3 层：运行时门控（环境变量 + 函数检测）                            │
│  ├── isPowerShellToolEnabled() → PowerShell (平台+用户类型)          │
│  ├── isAgentSwarmsEnabled() → TeamCreate/Delete                     │
│  ├── isWorktreeModeEnabled() → EnterWorktree/ExitWorktree           │
│  └── isToolSearchEnabledOptimistic() → ToolSearch                   │
│                                                                     │
│  第 4 层：GrowthBook 运行时门控（远程配置）                           │
│  ├── tengu_kairos_cron → Cron 工具是否实际调度                       │
│  └── tengu_surreal_dali → RemoteTrigger 是否可用                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

四层门控形成**纵深防御**：编译时移除不需要的代码，构建时区分用户群体，运行时检测平台能力，GrowthBook 远程控制灰度发布。每一层都减少了攻击面和运行时开销。

---

## 第六章：工具执行生命周期

工具系统的核心价值体现在执行生命周期中——从 AI 模型发出工具调用请求（`tool_use` block），到最终结果（`tool_result`）返回给模型的完整链路。这个链路跨越多个文件和子系统，涉及输入验证、权限检查、Hook 执行、进度流和错误处理。

### 6.1 入口：runToolUse()

```typescript
// src/services/tools/toolExecution.ts:337-342
// runToolUse 是工具执行的入口——接收模型的 tool_use block，返回消息更新流
export async function* runToolUse(
  toolUse: ToolUseBlock,          // 模型返回的工具调用块：{ id, name, input }
  assistantMessage: AssistantMessage, // 包含此 tool_use 的完整 assistant 消息
  canUseTool: CanUseToolFn,       // 权限检查函数（来自 useCanUseTool Hook）
  toolUseContext: ToolUseContext,  // 执行上下文（140+ 字段：工具列表、MCP 客户端、状态等）
): AsyncGenerator<MessageUpdateLazy, void> {
```

入口函数首先通过工具名查找工具实例。如果在当前可用工具池中找不到，还会检查 `getAllBaseTools()` 中的别名匹配——这支持旧版 transcript 中已重命名的工具（如 `KillShell` 现在是 `TaskStop` 的别名）。

### 6.2 输入验证阶段

工具找到后，进入两阶段验证：

```
阶段 1：Schema 验证（Zod）
├── 使用工具声明的 inputSchema 解析输入
├── 类型不匹配 → 返回格式化的 Zod 错误
└── 如果是延迟加载工具且 schema 未发送 → 附加 ToolSearch 提示

阶段 2：语义验证（validateInput）
├── 工具特定的业务逻辑检查
├── 例如：FileEditTool 检查 old_string 是否在文件中存在
└── 失败 → 返回 ValidationResult.message 给模型
```

这两阶段的分离是防御性编程的体现——Schema 验证捕获类型错误（"期望字符串，得到数字"），语义验证捕获逻辑错误（"文件中找不到要替换的字符串"）。模型看到的错误消息足够具体，可以自行修正再次尝试。

### 6.3 Pre-Tool Hooks

验证通过后，在实际权限检查之前，系统运行 `runPreToolUseHooks()`：

```
Pre-Tool Hooks
├── 用户在 settings.json 中定义的 Hook 规则
├── Hook 可以修改输入（updatedInput）
├── Hook 可以直接决定权限（allow/deny）
├── Hook 执行时间被记录用于性能监控
└── Hook 失败不阻止工具执行（优雅降级）
```

Hook 系统让用户无需修改源码就能定制工具行为——例如，企业可以通过 Hook 自动拒绝所有写入 `/etc/` 的操作。

### 6.4 三分支权限流程：useCanUseTool

权限检查是工具生命周期中最复杂的阶段。`useCanUseTool` Hook（`src/hooks/useCanUseTool.tsx`）实现了三分支决策流程：

```typescript
// src/hooks/useCanUseTool.tsx:37
// 权限检查入口：先检查是否有强制决策，否则调用权限引擎
const decisionPromise = forceDecision !== undefined
  ? Promise.resolve(forceDecision)        // 已有决策（如 Hook 提前决定）
  : hasPermissionsToUseTool(              // 调用权限引擎
      tool, input, toolUseContext,
      assistantMessage, toolUseID
    );
```

决策返回后，进入三分支处理：

**分支 1：Allow（允许）**

```typescript
// src/hooks/useCanUseTool.tsx:39-53
// 分支 1：权限引擎决定允许
if (result.behavior === "allow") {
  // 如果是 auto mode 的分类器批准，记录分类器审批
  if (feature("TRANSCRIPT_CLASSIFIER") &&
      result.decisionReason?.type === "classifier" &&
      result.decisionReason.classifier === "auto-mode") {
    setYoloClassifierApproval(toolUseID, result.decisionReason.reason);
  }
  // 记录日志，解析 Promise，执行继续
  ctx.logDecision({ decision: "accept", source: "config" });
  resolve(ctx.buildAllow(result.updatedInput ?? input, {
    decisionReason: result.decisionReason
  }));
}
```

**分支 2：Deny（拒绝）**

```typescript
// src/hooks/useCanUseTool.tsx:65-91
// 分支 2：权限引擎决定拒绝
case "deny":
  // 记录权限决策日志
  logPermissionDecision({ tool, input, toolUseContext, messageId, toolUseID },
    { decision: "reject", source: "config" });
  // auto mode 拒绝时记录到拒绝追踪器
  if (feature("TRANSCRIPT_CLASSIFIER") &&
      result.decisionReason?.type === "classifier") {
    recordAutoModeDenial({
      toolName: tool.name,
      display: description,
      reason: result.decisionReason.reason ?? "",
      timestamp: Date.now()
    });
    // 向 UI 添加通知
    toolUseContext.addNotification?.({
      key: "auto-mode-denied",
      jsx: <Text color="error">
             {tool.userFacingName(input).toLowerCase()} denied by auto mode
           </Text>
    });
  }
  resolve(result);  // 返回拒绝决策
```

**分支 3：Ask（询问）——最复杂的分支**

当权限引擎无法自动决定时，系统通过**竞争机制**（race）并行尝试多种自动化决策，同时准备交互式对话：

```
Ask 分支：四级竞争决策
│
├── 1. Coordinator 模式（awaitAutomatedChecksBeforeDialog）
│   ├── handleCoordinatorPermission()
│   ├── 依次尝试 Hook → Bash 分类器
│   └── 如果自动决策 → 返回，否则穿透到交互式
│
├── 2. Swarm Worker 模式（智能体集群）
│   ├── handleSwarmWorkerPermission()
│   ├── 先试分类器自动批准
│   ├── 否则创建权限请求 → 发送到 leader 邮箱
│   ├── 注册 onAllow/onReject 回调 → 等待 leader 决策
│   └── 使用原子 claim() 防止多重解析
│
├── 3. Bash 分类器投机检查（2 秒超时）
│   ├── peekSpeculativeClassifierCheck() — 窥视预计算结果
│   ├── Promise.race([分类器结果, 2秒超时])
│   ├── 高置信度匹配 → 自动批准，跳过对话
│   └── 超时或低置信度 → 穿透到交互式
│
└── 4. 交互式权限对话（handleInteractivePermission）
    ├── 推入 UI 队列显示权限提示
    ├── 同时竞争多个信号源：
    │   ├── 用户键盘交互
    │   ├── Bridge 响应（来自 claude.ai CCR）
    │   ├── Channel 中继（MCP 远程批准）
    │   └── 后台 Hook/分类器结果
    └── createResolveOnce() 保证只有一个胜者
```

```typescript
// src/hooks/useCanUseTool.tsx:93-168
// Ask 分支的核心竞争逻辑（简化展示）
case "ask":
  // 第 1 级：Coordinator 模式 — 先等自动化检查
  if (appState.toolPermissionContext.awaitAutomatedChecksBeforeDialog) {
    const coordinatorDecision = await handleCoordinatorPermission({...});
    if (coordinatorDecision) { resolve(coordinatorDecision); return; }
  }
  // 第 2 级：Swarm Worker — 转发给 leader
  const swarmDecision = await handleSwarmWorkerPermission({...});
  if (swarmDecision) { resolve(swarmDecision); return; }
  // 第 3 级：Bash 分类器投机检查（2秒超时）
  if (feature("BASH_CLASSIFIER") && result.pendingClassifierCheck
      && tool.name === BASH_TOOL_NAME) {
    const raceResult = await Promise.race([
      speculativePromise,             // 分类器结果
      new Promise(res => setTimeout(res, 2000, { type: "timeout" }))  // 2秒超时
    ]);
    if (raceResult.type === "result" && raceResult.result.confidence === "high") {
      resolve(ctx.buildAllow(...));   // 高置信度自动批准
      return;
    }
  }
  // 第 4 级：交互式对话（最终兜底）
  handleInteractivePermission({ ctx, description, result, ... }, resolve);
```

### 6.5 工具执行

权限通过后，进入实际执行：

```typescript
// src/services/tools/toolExecution.ts（概念流程）
// 工具执行核心
const result = await tool.call(
  callInput,                        // 经过验证和权限修改的输入
  {
    ...toolUseContext,              // 完整执行上下文
    toolUseId: toolUseID,           // 本次调用的唯一 ID
    userModified: permissionDecision.userModified ?? false  // 用户是否修改了输入
  },
  canUseTool,                       // 传递权限函数（支持递归工具调用）
  assistantMessage,                 // 关联的 assistant 消息
  progress => onToolProgress(...)   // 进度回调
);
```

注意 `canUseTool` 被传递给工具——这使得工具可以在执行过程中**递归调用**其他工具（例如 AgentTool 创建的子智能体会调用自己的工具）。

### 6.6 进度流（Progress Streaming）

```typescript
// src/services/tools/toolExecution.ts:492-570
// 进度流实现：通过 Stream 适配器将回调式进度转为异步迭代器
function streamedCheckPermissionsAndCallTool(...): AsyncIterable<MessageUpdateLazy> {
  const stream = new Stream<MessageUpdateLazy>()   // 创建流适配器

  checkPermissionsAndCallTool(
    ...,
    progress => {                                   // 进度回调
      logEvent('tengu_tool_use_progress', {...})    // 记录分析事件
      stream.enqueue({                              // 推入进度消息
        message: createProgressMessage({
          toolUseID: progress.toolUseID,
          parentToolUseID: toolUseID,                // 关联父工具调用
          data: progress.data,
        }),
      })
    },
  )
  .then(results => {                                // 执行完成
    for (const result of results) stream.enqueue(result)
  })
  .catch(error => stream.error(error))              // 错误传播
  .finally(() => stream.done())                     // 流结束

  return stream
}
```

进度流将工具的异步执行过程实时传递给 UI——用户可以看到 BashTool 的命令输出逐行出现、AgentTool 的子智能体状态更新、文件写入的进度等。

### 6.7 错误分类与恢复

```typescript
// src/services/tools/toolExecution.ts:150-171
// 错误分类器：将运行时错误转为遥测安全的字符串
export function classifyToolError(error: unknown): string {
  // TelemetrySafeError：使用预审核的 telemetryMessage
  if (error instanceof TelemetrySafeError_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS) {
    return error.telemetryMessage.slice(0, 200)   // 截断到 200 字符
  }
  if (error instanceof Error) {
    // Node.js 文件系统错误：使用错误码（ENOENT、EACCES 等）
    const errnoCode = getErrnoCode(error)
    if (typeof errnoCode === 'string') {
      return `Error:${errnoCode}`                  // 如 "Error:ENOENT"
    }
    // 已知错误类型：使用稳定的 .name 属性（不受代码压缩影响）
    if (error.name && error.name !== 'Error' && error.name.length > 3) {
      return error.name.slice(0, 60)               // 如 "ShellError"
    }
    return 'Error'                                  // 通用回退
  }
  return 'UnknownError'                             // 非 Error 实例
}
```

这个分类器解决了一个微妙的问题：在压缩后的外部构建中，`error.constructor.name` 会被混淆成无意义的短标识符（如 "nJT"），所以系统通过多个策略提取有意义的错误信息。

特殊错误类型的处理：
- **`ShellError`**：Shell 命令执行失败，包含退出码 + stderr + stdout
- **`McpToolCallError`**：MCP 工具调用失败，包含远程错误信息
- **`McpAuthError`**：MCP 认证失败，更新客户端状态为 `needs-auth`，允许用户重新授权
- **`AbortError`**：用户中断，最小化日志记录
- **超长错误**：超过 10,000 字符的错误消息被截断为首尾各 5,000 字符

### 6.8 完整执行流程图

```
┌──────────────────────────────────────────────────────────────────────┐
│                    工具执行完整生命周期                                │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  模型返回 tool_use block: { id, name:"Edit", input:{...} }           │
│       │                                                              │
│       ▼                                                              │
│  ① runToolUse()                                                      │
│  ├── 查找工具：toolPool[name] ?? aliases[name]                       │
│  └── 未找到 → 返回 is_error 消息 ──→ 流程终止                        │
│       │                                                              │
│       ▼                                                              │
│  ② 输入验证                                                          │
│  ├── Zod schema 解析 → 类型检查                                      │
│  ├── tool.validateInput() → 语义检查                                 │
│  └── 失败 → 返回错误（含修正提示）──→ 模型可自行重试                  │
│       │                                                              │
│       ▼                                                              │
│  ③ Pre-Tool Hooks                                                    │
│  ├── runPreToolUseHooks()                                            │
│  ├── Hook 可修改输入或直接决定权限                                    │
│  └── Hook 失败 → 忽略，继续正常流程                                  │
│       │                                                              │
│       ▼                                                              │
│  ④ 权限检查（useCanUseTool 三分支）                                   │
│  ├── hasPermissionsToUseTool()                                       │
│  │   ├── 1a. 工具整体被 deny 规则拒绝？                              │
│  │   ├── 1b. 工具需要 ask 规则？                                     │
│  │   ├── 1c. tool.checkPermissions() → 工具特定权限                  │
│  │   ├── 1d-1g. 安全检查（.git/、.claude/ 等）                       │
│  │   ├── 2a. bypassPermissions 模式 → 直接允许                      │
│  │   ├── 2b. always-allowed 规则匹配？                               │
│  │   └── 3. 回退 → ask                                              │
│  │                                                                   │
│  ├── allow → 记录日志，继续执行                                      │
│  ├── deny  → 记录日志，返回拒绝消息 ──→ 流程终止                    │
│  └── ask   → 四级竞争：                                              │
│      ├── Coordinator → Hook + 分类器                                 │
│      ├── Swarm → 转发 leader                                        │
│      ├── Bash 分类器（2s 超时）                                      │
│      └── 交互式对话 + Bridge + Channel                               │
│       │                                                              │
│       ▼                                                              │
│  ⑤ tool.call(input, context, canUseTool, msg, progress)             │
│  ├── 传入权限函数支持递归工具调用                                     │
│  ├── progress 回调 → Stream → UI 实时渲染                            │
│  └── try/catch 捕获并分类错误                                        │
│       │                                                              │
│       ▼                                                              │
│  ⑥ 结果处理                                                          │
│  ├── tool.mapToolResultToToolResultBlockParam() → API 格式转换        │
│  ├── maybePersistLargeToolResult() → 大结果持久化                    │
│  └── Post-Tool Hooks → 后处理                                       │
│       │                                                              │
│       ▼                                                              │
│  ⑦ 返回 tool_result 消息给模型                                       │
│  └── 模型根据结果决定下一步：回复用户 or 调用更多工具                │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 第七章：工具结果处理

工具执行完成后，结果必须以适当的大小返回给模型。工具结果处理系统解决了一个核心矛盾：工具可能产生巨大的输出（一个 `cat` 命令可能输出数十万行），但模型的上下文窗口是有限且昂贵的。

### 7.1 大小限制体系

```typescript
// src/constants/toolLimits.ts:13
// 全局默认阈值：50,000 字符
// 单个工具输出超过此大小 → 自动持久化到磁盘
export const DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000

// src/constants/toolLimits.ts:22-28
// Token 级上限和换算常量
export const MAX_TOOL_RESULT_TOKENS = 100_000    // 约 400KB 文本
export const BYTES_PER_TOKEN = 4                  // 保守估计
export const MAX_TOOL_RESULT_BYTES = MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN

// src/constants/toolLimits.ts:49
// 单条消息聚合预算：200,000 字符
// 防止 N 个并行工具各产生 40K → 总计 N×40K 超出预算
export const MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000
```

系统采用**双层限制**设计：
- **单工具限制**：每个工具的输出不超过其 `maxResultSizeChars`（默认 50K，受全局 cap 约束）
- **消息聚合限制**：一条消息中所有并行工具结果的总和不超过 200K

### 7.2 持久化机制

当工具结果超过阈值时，`maybePersistLargeToolResult()` 将完整结果保存到磁盘：

```typescript
// src/utils/toolResultStorage.ts:272-334
// 大结果持久化：检查大小 → 保存到磁盘 → 替换为预览
async function maybePersistLargeToolResult(
  toolResultBlock: ToolResultBlockParam,
  toolName: string,
  persistenceThreshold?: number,
): Promise<ToolResultBlockParam> {
  const content = toolResultBlock.content

  // 空结果注入标记：避免模型误解空输出
  // 某些模型遇到空 tool_result 会生成停止序列
  if (isToolResultContentEmpty(content)) {
    return {
      ...toolResultBlock,
      content: `(${toolName} completed with no output)`,  // 注入占位符
    }
  }

  // 图片内容不持久化——必须原样发送给 Claude
  if (hasImageBlock(content)) return toolResultBlock

  const size = contentSize(content)
  const threshold = persistenceThreshold ?? MAX_TOOL_RESULT_BYTES
  if (size <= threshold) return toolResultBlock    // 未超阈值，原样返回

  // 持久化完整内容到文件
  const result = await persistToolResult(content, toolResultBlock.tool_use_id)
  if (isPersistError(result)) return toolResultBlock  // 持久化失败 → 原样回退

  // 构建预览消息替换原内容
  const message = buildLargeToolResultMessage(result)
  return { ...toolResultBlock, content: message }
}
```

### 7.3 持久化存储细节

```typescript
// src/utils/toolResultStorage.ts:137-184
// persistToolResult：将工具结果写入磁盘
export async function persistToolResult(
  content: NonNullable<ToolResultBlockParam['content']>,
  toolUseId: string,
): Promise<PersistedToolResult | PersistToolResultError> {
  const isJson = Array.isArray(content)

  // 只能持久化文本内容
  if (isJson) {
    const hasNonTextContent = content.some(block => block.type !== 'text')
    if (hasNonTextContent) {
      return { error: 'Cannot persist tool results containing non-text content' }
    }
  }

  await ensureToolResultsDir()
  const filepath = getToolResultPath(toolUseId, isJson)
  const contentStr = isJson ? jsonStringify(content, null, 2) : content

  // 原子写入：使用 'wx' 标志（写且排他）
  // tool_use_id 每次调用唯一，内容对于同一 id 是确定性的
  // 所以如果文件已存在，说明是重放（如 microcompact 回放原始消息）
  try {
    await writeFile(filepath, contentStr, { encoding: 'utf-8', flag: 'wx' })
  } catch (error) {
    if (getErrnoCode(error) !== 'EEXIST') {
      return { error: getFileSystemErrorMessage(toError(error)) }
    }
    // EEXIST：已持久化过，跳过写入，继续生成预览
  }

  // 生成预览
  const { preview, hasMore } = generatePreview(contentStr, PREVIEW_SIZE_BYTES)
  return { filepath, originalSize: contentStr.length, isJson, preview, hasMore }
}
```

存储路径为 `~/.claude/sessions/{sessionId}/tool-results/{toolUseId}.{txt|json}`。

### 7.4 预览生成与 `<persisted-output>` 标签

模型看到的不是完整结果，而是一个包含预览和文件路径的标签：

```typescript
// src/utils/toolResultStorage.ts:189-199
// 构建模型看到的替代消息
export function buildLargeToolResultMessage(result: PersistedToolResult): string {
  let message = `${PERSISTED_OUTPUT_TAG}\n`            // <persisted-output>
  message += `Output too large (${formatFileSize(result.originalSize)}). `
  message += `Full output saved to: ${result.filepath}\n\n`
  message += `Preview (first ${formatFileSize(PREVIEW_SIZE_BYTES)}):\n`
  message += result.preview                             // 前 2KB 内容
  message += result.hasMore ? '\n...\n' : '\n'          // 截断标记
  message += PERSISTED_OUTPUT_CLOSING_TAG               // </persisted-output>
  return message
}
```

预览大小为 `PREVIEW_SIZE_BYTES = 2000`（约 2KB）。预览生成时智能截断——优先在换行符处截断（避免切断一行的中间），如果换行符位置不合适（小于限制的 50%），则在精确限制处截断：

```typescript
// src/utils/toolResultStorage.ts:339-356
// 预览生成：在换行符边界处智能截断
export function generatePreview(content: string, maxBytes: number) {
  if (content.length <= maxBytes) return { preview: content, hasMore: false }

  const truncated = content.slice(0, maxBytes)
  const lastNewline = truncated.lastIndexOf('\n')

  // 如果换行符在限制的 50% 以内，使用精确限制
  // 否则在最近的换行符处截断，保持行完整性
  const cutPoint = lastNewline > maxBytes * 0.5 ? lastNewline : maxBytes

  return { preview: content.slice(0, cutPoint), hasMore: true }
}
```

这意味着模型通常可以看到结果的前 2KB 预览（足以理解输出结构），并可以通过 `FileReadTool` 读取完整文件获取剩余内容。

### 7.5 GrowthBook 动态阈值覆盖

系统允许通过 GrowthBook 远程配置动态调整每个工具的持久化阈值：

```typescript
// src/utils/toolResultStorage.ts:43-78
// GrowthBook 覆盖标志：tengu_satin_quoll
// 值为 { "bash": 100000, "web_search": 75000 } 格式的 JSON 映射
const PERSIST_THRESHOLD_OVERRIDE_FLAG = 'tengu_satin_quoll'

export function getPersistenceThreshold(
  toolName: string,
  declaredMaxResultSizeChars: number,
): number {
  // Infinity = 硬性排除（如 FileReadTool 自己限制输出大小）
  if (!Number.isFinite(declaredMaxResultSizeChars)) {
    return declaredMaxResultSizeChars    // 不受 GrowthBook 覆盖影响
  }

  // 查询 GrowthBook 覆盖映射
  const overrides = getFeatureValue_CACHED_MAY_BE_STALE<
    Record<string, number> | null
  >(PERSIST_THRESHOLD_OVERRIDE_FLAG, {})
  const override = overrides?.[toolName]

  // 覆盖值有效 → 直接使用（绕过 Math.min 限制）
  if (typeof override === 'number' && Number.isFinite(override) && override > 0) {
    return override
  }

  // 默认：取声明值和全局默认值的较小者
  return Math.min(declaredMaxResultSizeChars, DEFAULT_MAX_RESULT_SIZE_CHARS)
}
```

三个 GrowthBook 标志控制结果处理行为：
- **`tengu_satin_quoll`**：每工具持久化阈值覆盖映射
- **`tengu_hawthorn_window`**：每消息聚合预算覆盖
- **`tengu_hawthorn_steeple`**：聚合预算执行开关

### 7.6 消息聚合预算

单工具限制无法防止并行调用的聚合爆炸。`enforceToolResultBudget()` 在所有工具结果收集完成后进行消息级别的预算执行：

```
单消息聚合预算执行流程
│
├── 收集一条消息中所有 tool_result 块
├── 计算总大小
├── 总大小 ≤ 200K → 不做任何操作
└── 总大小 > 200K →
    ├── 按大小降序排列所有 FRESH（首次出现）的结果
    ├── 选择最大的结果持久化到磁盘
    ├── 替换为预览 + 路径引用
    └── 重复直到总大小 ≤ 200K
```

状态追踪使用 `ContentReplacementState`：
- `seenIds: Set<string>`：已处理过的工具调用 ID（命运已冻结）
- `replacements: Map<string, string>`：工具调用 ID → 预览字符串的缓存

### 7.7 文件系统错误处理

持久化可能因文件系统问题失败。系统将 errno 映射为人类可读的错误消息：

| errno | 含义 | 处理策略 |
|-------|------|---------|
| `ENOENT` | 目录不存在 | 原样返回结果 |
| `EACCES` | 权限不足 | 原样返回结果 |
| `ENOSPC` | 磁盘空间不足 | 原样返回结果 |
| `EROFS` | 只读文件系统 | 原样返回结果 |
| `EMFILE` | 打开文件数过多 | 原样返回结果 |
| `EEXIST` | 文件已存在（重放） | 跳过写入，正常生成预览 |

注意所有持久化失败的处理策略都是**原样返回结果**——这是优雅降级的体现：持久化是优化而非必需品，失败时模型仍能收到完整（虽然可能很大的）结果。

---

## 设计哲学分析

工具系统是 Claude Code 中设计哲学表达最集中、最深刻的子系统。它不仅实现了具体的功能，更在每一个设计决策中体现了整个系统的核心哲学。

### 组合性（Composability）：统一接口的力量

`Tool<Input, Output, P>` 泛型接口是组合性设计的最纯粹表达。无论是读取文件的 `FileReadTool`（1,600 行），还是管理多智能体协作的 `AgentTool`（6,782 行），还是连接外部服务器的 `MCPTool`，它们都实现完全相同的接口：`name`、`inputSchema`、`validateInput()`、`checkPermissions()`、`call()`。

这种统一接口带来了"零集成点"的扩展模式——添加一个新工具不需要修改任何现有文件。新工具只需实现 `Tool` 接口，在 `tools.ts` 中注册一行，就自动获得权限检查、进度流、错误处理、结果持久化等全部基础设施。这不是理论上的可能性，而是系统中 40+ 个工具实际遵循的模式。

`buildTool()` 工厂函数通过 `TOOL_DEFAULTS`（故障关闭默认值）进一步强化了这种组合性——新工具自动继承安全的默认行为（`isConcurrencySafe: false`、`isReadOnly: false`），开发者只需声明偏离默认值的属性。这让"安全"成为默认状态而非需要记住的检查项。

### 安全优先设计（Safety-First Design）与防御性编程（Defensive Programming）：两阶段门控

两阶段门控（`validateInput()` → `checkPermissions()`）是安全优先设计和防御性编程的深度融合。第一阶段——输入验证——在安全检查之前就拒绝了格式错误的请求。这不是过度工程：Zod schema 验证在类型层面阻止了整类攻击（如通过注入恶意路径的目录遍历），而 `validateInput()` 在语义层面阻止了逻辑错误（如试图编辑不存在的字符串）。

第二阶段——权限检查——才进入真正的安全决策。这种分离意味着权限系统永远不会收到格式错误的输入，减少了安全代码中的边界情况。`FileEditTool` 的 UNC 路径保护和秘密检测是防御性编程的具体实例——它不信任输入路径的格式，在执行之前主动检查潜在的安全风险。

### 隔离与遏制（Isolation & Containment）：BashTool 的纵深防御

BashTool 代表了工具系统中最深层的隔离设计。12,411 行代码中的很大一部分用于在执行命令**之前**理解命令的含义：AST 级别的命令解析、`shouldUseSandbox()` 的三级沙箱决策（`stripAllLeadingEnvVars` + `stripSafeWrappers` 的迭代候选构建）、`containsExcludedCommand()` 的模式匹配——所有这些都在 shell 进程启动之前完成。

这种"先理解，再执行"的哲学将隔离从运行时推到了分析时。传统沙箱在执行时限制权限，BashTool 在分析时就决定**是否应该在沙箱中执行**，以及**是否应该执行**。这是隔离的最深层次表达——不是限制爆炸的影响范围，而是在爆炸发生之前判断是否存在风险。

### 上下文窗口经济学（Context Window Economics）：结果持久化系统

工具结果持久化系统是上下文窗口经济学在工具层面的具体实现。`maxResultSizeChars`、`DEFAULT_MAX_RESULT_SIZE_CHARS`（50K）、`MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`（200K）——这些常量定义了一个精密的预算管理系统。

这个系统的精妙之处在于它的分层设计：单工具阈值防止单个工具独占上下文；消息聚合预算防止并行工具的累积效应；GrowthBook 覆盖允许远程调整平衡点而无需发版。模型仍然能通过 `FileReadTool` 按需读取持久化的完整结果——这意味着信息没有丢失，只是从昂贵的上下文窗口移到了廉价的文件系统。

### 可扩展性无需修改（Extensibility Without Modification）

`buildTool() satisfies ToolDef` 模式让 TypeScript 的类型系统在编译时验证工具定义的完整性，同时 `satisfies` 关键字保留了具体类型信息（不丢失到宽泛的 `ToolDef` 类型）。这意味着新工具的开发者在编写代码时就能获得完整的类型检查和自动补全，而不需要修改 `ToolDef` 类型本身。

Feature-Gated 工具更进一步展示了无修改扩展——通过编译时 `feature()` 检查，不同的构建产物可以包含不同的工具集合，而核心代码无需任何改变。KAIROS 系列工具（SleepTool、SendUserFileTool、PushNotificationTool）作为一个完整的后台智能体能力包被整体门控，体现了可组合的产品变体策略。

### 人在回路（Human-in-the-Loop）：权限模型

工具的权限模型将每一次工具调用都变成了一个潜在的人在回路交互点。`useCanUseTool` 的三分支设计（allow/deny/ask）确保了：在默认模式下，任何有副作用的操作都必须经过用户确认。Swarm Worker 模式将权限请求转发给 leader 智能体（它可能再转发给用户），保持了即使在多智能体场景中用户仍然是最终决策者的原则。

`createResolveOnce()` 的原子解析机制确保了在多个竞争的决策源中只有一个能生效——用户交互、分类器结果、Hook 决策、Bridge 响应、Channel 中继，这些都在竞争中"赛跑"，但只有第一个 `claim()` 成功的决策源会被采纳。这防止了并发决策导致的不一致状态。

### 优雅降级（Graceful Degradation）：工具错误的分级处理

工具执行中的错误分类（`classifyToolError`）和恢复策略展示了优雅降级的多个层次：持久化失败时原样返回结果（优化降级，功能不降级）；MCP 认证失败时更新状态允许重新授权（暂时失败，可恢复）；shell 错误时提供退出码和完整输出（失败但可诊断）。空结果注入 `(toolName completed with no output)` 标记甚至防止了某些模型在面对空 tool_result 时产生的停止序列错误——这是在极端边界情况下的防御性降级。

---

## 关键要点总结

1. **统一接口**：所有工具实现相同的 `Tool<Input, Output, P>` 泛型接口，通过 `buildTool()` 工厂函数创建，安全默认值遵循故障关闭原则
2. **两阶段门控**：每个工具调用经过 `validateInput()`（输入合法性）→ `checkPermissions()`（权限授权）两阶段安全检查
3. **四层条件门控**：编译时 Feature Flag → 构建时 USER_TYPE → 运行时环境检测 → GrowthBook 远程配置，层层递进控制工具可用性
4. **三分支权限流程**：`useCanUseTool` 的 allow/deny/ask 分支，其中 ask 分支通过四级竞争（Coordinator → Swarm → 分类器 → 交互式）自动决策
5. **结果持久化**：超过 50K 的工具输出自动存储到磁盘，模型收到 2KB 预览 + 文件路径引用，可按需通过 FileReadTool 访问完整内容
6. **双层预算管理**：单工具阈值（50K 默认）+ 消息聚合预算（200K）+ GrowthBook 远程调整（`tengu_satin_quoll`、`tengu_hawthorn_window`）
7. **进度流**：`Stream` 适配器将回调式进度转为异步迭代器，工具执行过程实时传递给 UI
8. **错误分类与恢复**：多层次错误分类（遥测安全、errno、命名错误）+ 多种恢复策略（原样降级、重新授权、用户中断）

---

## 下一篇预览

**Doc 7：查询引擎与 LLM 交互** 将深入 Claude Code 的"大脑"——QueryEngine 和查询管道。我们将看到工具系统如何嵌入到 LLM 的工具调用循环中：模型发出 tool_use → 权限检查 → 执行 → 结果返回 → 模型再次推理。这个循环是工具系统的运行时编排者，也是上下文窗口经济学的中央管理者。我们还将深入 API 客户端的重试策略、Token 预算管理、Extended Thinking 支持，以及上下文构建的完整机制。
