# Doc 9: 状态管理

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）、Doc 2（构建系统与运行时）、Doc 3（入口点与初始化流程）、Doc 4（终端 UI 系统）、Doc 5（命令系统）、Doc 6（工具系统）、Doc 7（查询引擎与 LLM 交互）、Doc 8（权限系统）

在前八篇文档中，我们已经深入分析了 Claude Code 的每一个核心子系统——从终端 UI 到权限系统，从查询引擎到工具执行。但有一个关键问题始终隐含在所有讨论之下：**这些子系统如何共享和同步数据？** 当用户切换权限模式时，UI 如何立即反映变化？当工具修改了文件，系统如何追踪这些变化以供后续的 commit 归因？当子智能体在后台运行时，主界面如何知道它们的状态？

这就是状态管理系统的核心职责。Claude Code 没有使用 Redux、MobX 或 Zustand 等第三方状态管理库，而是构建了一套极其精简的自研状态管理方案——仅用 **34 行代码**实现了核心 Store，却足以驱动一个拥有 400+ 字段、支持多智能体、跨进程同步的复杂应用。

本文档将从 Store 的 34 行核心实现开始，逐层展开 AppState 的完整类型结构、状态变更的副作用系统、消息系统的类型层次、文件状态缓存机制，最终通过状态流转图将所有概念串联起来。

---

## 第一章：AppState 定义 src/state/AppStateStore.ts

### 1.1 AppState 类型的整体设计

`AppState` 是 Claude Code 的全局状态容器，定义在 `src/state/AppStateStore.ts` 中。这个类型的设计体现了一个重要的架构决策：**将不可变数据与可变数据在类型层面显式分离**。

```typescript
// src/state/AppStateStore.ts:89-158
// AppState 类型由两部分组成：
// 1. DeepImmutable<{...}> 包裹的不可变部分 —— 主要是简单的配置和状态值
// 2. & {...} 交叉的可变部分 —— 包含 Map、Set、函数等无法深度冻结的类型
export type AppState = DeepImmutable<{
  settings: SettingsJson              // 用户设置（从配置文件加载）
  verbose: boolean                    // 是否启用详细输出模式
  mainLoopModel: ModelSetting         // 主循环使用的模型（别名或完整名）
  mainLoopModelForSession: ModelSetting // 当前会话的模型设置
  statusLineText: string | undefined  // 状态栏显示文本
  expandedView: 'none' | 'tasks' | 'teammates' // 展开视图模式
  isBriefOnly: boolean                // 是否为简洁输出模式
  selectedIPAgentIndex: number        // 当前选中的进程内智能体索引
  coordinatorTaskIndex: number        // 协调器任务面板选择
  viewSelectionMode: 'none' | 'selecting-agent' | 'viewing-agent'
  footerSelection: FooterItem | null  // 底部栏当前聚焦的标签
  toolPermissionContext: ToolPermissionContext // 工具权限上下文（包含当前模式）
  agent: string | undefined           // --agent CLI 标志指定的智能体名称
  kairosEnabled: boolean              // 助手模式是否完全启用
  remoteSessionUrl: string | undefined // 远程会话 URL
  remoteConnectionStatus:             // 远程会话 WebSocket 状态
    | 'connecting' | 'connected' | 'reconnecting' | 'disconnected'
  remoteBackgroundTaskCount: number   // 远程后台任务计数
  // ... 还有多个 replBridge* 字段用于远程控制桥接状态
}> & {
  // 以下部分不在 DeepImmutable 包裹中
  // 因为它们包含 Map、Set、函数等无法深度冻结的类型
  tasks: { [taskId: string]: TaskState }        // 统一任务状态注册表
  agentNameRegistry: Map<string, AgentId>       // 智能体名称→ID 映射
  foregroundedTaskId?: string                    // 前台任务 ID
  viewingAgentTaskId?: string                   // 正在查看的智能体任务 ID
  // ... 更多可变字段
}
```

### 1.2 核心字段分类解析

AppState 包含超过 **80 个字段**，可以按职责分为以下几大类：

**（1）UI 状态字段**

这些字段直接驱动终端 UI 的渲染：

```typescript
// src/state/AppStateStore.ts:95-108
expandedView: 'none' | 'tasks' | 'teammates'  // 控制任务/队友面板展开
isBriefOnly: boolean                           // 控制输出详略
viewSelectionMode: 'none' | 'selecting-agent' | 'viewing-agent'
footerSelection: FooterItem | null             // Footer 导航焦点
statusLineText: string | undefined             // 状态栏文字
```

**（2）智能体与任务字段**

多智能体系统的运行时状态：

```typescript
// src/state/AppStateStore.ts:159-167
tasks: { [taskId: string]: TaskState }          // 所有任务的统一注册表
agentNameRegistry: Map<string, AgentId>         // 名称→ID 映射（SendMessage 路由用）
foregroundedTaskId?: string                     // 前台展示的任务
viewingAgentTaskId?: string                     // 正在查看队友的任务 ID
```

**（3）MCP 与插件字段**

外部扩展系统的连接状态：

```typescript
// src/state/AppStateStore.ts:173-216
mcp: {
  clients: MCPServerConnection[]    // 活跃的 MCP 服务器连接
  tools: Tool[]                     // MCP 注册的工具
  commands: Command[]               // MCP 注册的命令
  resources: Record<string, ServerResource[]>  // MCP 资源
  pluginReconnectKey: number        // 递增以触发 MCP 重连
}
plugins: {
  enabled: LoadedPlugin[]           // 已启用的插件
  disabled: LoadedPlugin[]          // 已禁用的插件
  commands: Command[]               // 插件注册的命令
  errors: PluginError[]             // 加载/初始化错误
  installationStatus: { ... }       // 后台安装进度
  needsRefresh: boolean             // 磁盘状态已变更，需要刷新
}
```

**（4）持久化与归因字段**

追踪文件变更和 Claude 贡献：

```typescript
// src/state/AppStateStore.ts:218-220
fileHistory: FileHistoryState       // 文件检查点快照（可撤销变更）
attribution: AttributionState       // Claude 代码贡献追踪
todos: { [agentId: string]: TodoList }  // 每个智能体的待办事项
```

**（5）投机执行字段**

预测用户意图的前沿特性：

```typescript
// src/state/AppStateStore.ts:52-77
// 投机执行状态：空闲或活跃
export type SpeculationState =
  | { status: 'idle' }          // 空闲——没有投机执行在运行
  | {
      status: 'active'
      id: string                // 投机会话 ID
      abort: () => void         // 中止投机执行的回调
      startTime: number         // 开始时间戳
      messagesRef: { current: Message[] }     // 可变引用——避免每条消息都展开数组
      writtenPathsRef: { current: Set<string> } // 写入路径的可变引用
      boundary: CompletionBoundary | null      // 完成边界
      suggestionLength: number  // 建议文本长度
      toolUseCount: number      // 工具调用次数
      isPipelined: boolean      // 是否为流水线模式
      contextRef: { current: REPLHookContext } // Hook 上下文的可变引用
    }
```

### 1.3 默认状态工厂

`getDefaultAppState()` 是 AppState 的工厂函数，负责初始化所有字段的默认值：

```typescript
// src/state/AppStateStore.ts:456-569
export function getDefaultAppState(): AppState {
  // 确定初始权限模式——队友进程可能需要以 plan 模式启动
  const teammateUtils =
    require('../utils/teammate.js') as typeof import('../utils/teammate.js')
  const initialMode: PermissionMode =
    teammateUtils.isTeammate() && teammateUtils.isPlanModeRequired()
      ? 'plan'      // 队友被要求使用 plan 模式
      : 'default'   // 正常用户使用默认交互模式

  return {
    settings: getInitialSettings(),       // 从配置文件加载设置
    tasks: {},                            // 空任务表
    agentNameRegistry: new Map(),         // 空名称注册表
    verbose: false,                       // 默认非详细模式
    mainLoopModel: null,                  // null 表示使用默认模型
    // ... 所有字段的安全默认值
    toolPermissionContext: {
      ...getEmptyToolPermissionContext(), // 空权限上下文
      mode: initialMode,                 // 使用上面计算的初始模式
    },
    fileHistory: {
      snapshots: [],                     // 空快照列表
      trackedFiles: new Set(),           // 空追踪文件集
      snapshotSequence: 0,               // 序列号从 0 开始
    },
    attribution: createEmptyAttributionState(), // 空归因状态
    speculation: IDLE_SPECULATION_STATE,         // 投机执行初始为空闲
    activeOverlays: new Set<string>(),          // 空覆盖层集合
  }
}
```

注意一个有趣的细节：`getDefaultAppState()` 使用了 `require()` 而非 `import` 来加载 `teammate.js`。这是为了**避免循环依赖**——`teammate.ts` 可能间接依赖 `AppState`，使用运行时 `require()` 打破了编译时的循环引用。

---

## 第二章：Store 模式 src/state/store.ts

### 2.1 createStore() 的 34 行实现

Claude Code 的状态管理核心是一个极简的 Store 实现——整个文件只有 34 行：

```typescript
// src/state/store.ts:1-34
// 监听器类型：无参数的回调函数
type Listener = () => void
// 变更回调类型：接收新旧状态对
type OnChange<T> = (args: { newState: T; oldState: T }) => void

// Store 的公开接口——仅三个方法
export type Store<T> = {
  getState: () => T                           // 获取当前状态
  setState: (updater: (prev: T) => T) => void // 通过更新函数修改状态
  subscribe: (listener: Listener) => () => void // 订阅变更，返回取消订阅函数
}

// 核心工厂函数——创建一个带变更回调的泛型 Store
export function createStore<T>(
  initialState: T,           // 初始状态值
  onChange?: OnChange<T>,    // 可选的变更回调（用于副作用）
): Store<T> {
  let state = initialState   // 闭包中的可变状态——这就是"可变 Store"
  const listeners = new Set<Listener>()  // 监听器集合

  return {
    getState: () => state,   // 直接返回当前状态引用

    setState: (updater: (prev: T) => T) => {
      const prev = state     // 保存旧状态引用
      const next = updater(prev)  // 通过更新函数计算新状态
      if (Object.is(next, prev)) return  // 引用相同则跳过——关键优化
      state = next           // 更新闭包中的状态
      onChange?.({ newState: next, oldState: prev })  // 触发变更回调
      for (const listener of listeners) listener()    // 通知所有订阅者
    },

    subscribe: (listener: Listener) => {
      listeners.add(listener)         // 添加到 Set
      return () => listeners.delete(listener)  // 返回取消订阅函数
    },
  }
}
```

### 2.2 可变 Store 的设计决策

这个 Store 的设计做出了一个**与主流 React 生态相悖**的选择：使用**可变状态引用**而非不可变数据流。让我们对比理解：

| 特征 | Redux / Zustand | Claude Code Store |
|------|----------------|-------------------|
| 状态不可变性 | 强制不可变（每次返回新对象） | 闭包中直接赋值 `state = next` |
| 变更检测 | 深比较或结构共享 | `Object.is()` 引用比较 |
| 中间件 | 丰富的中间件生态 | 单一 `onChange` 回调 |
| 开发工具 | Redux DevTools | 无 |
| 代码量 | 数千行 | 34 行 |
| 性能 | 优秀但有不可变性开销 | 极低开销——没有序列化、无拷贝 |

为什么做出这个选择？Claude Code 的状态中包含大量**无法深度冻结的数据**——`Map`、`Set`、回调函数、`vm.Context`、`AbortController` 等。强制不可变性不仅带来性能开销，而且会与这些类型产生根本冲突。34 行的极简实现恰好满足了需求，既支持 React 的 `useSyncExternalStore` 订阅，又允许灵活的可变子结构。

### 2.3 Object.is() 快速路径

`setState` 中的 `Object.is(next, prev)` 检查是一个关键优化。如果更新函数返回了与当前状态完全相同的引用（即更新函数判断无需变更后直接返回 `prev`），则跳过所有通知。这避免了不必要的 React 重渲染和副作用触发。

---

## 第三章：状态变更监听 src/state/onChangeAppState.ts

### 3.1 副作用回调系统

当 Store 的状态发生变化时，`createStore` 的 `onChange` 参数负责处理**非 UI 的副作用**。在 Claude Code 中，这个回调是 `onChangeAppState`：

```typescript
// src/state/onChangeAppState.ts:43-49
// 核心变更处理函数——接收新旧状态对
export function onChangeAppState({
  newState,
  oldState,
}: {
  newState: AppState
  oldState: AppState
}) {
  // 以下是各类副作用处理...
}
```

### 3.2 权限模式同步（最复杂的副作用）

当用户通过任何方式切换权限模式（Shift+Tab 循环、/plan 命令、ExitPlanMode 对话框等），`onChangeAppState` 确保变更同步到所有外部系统：

```typescript
// src/state/onChangeAppState.ts:65-92
// 检测权限模式是否发生了变化
const prevMode = oldState.toolPermissionContext.mode
const newMode = newState.toolPermissionContext.mode
if (prevMode !== newMode) {
  // CCR（Command and Control Renderer）不能接收内部专用的模式名称
  // 例如 'bubble' 模式在外部化时变成 'default'
  const prevExternal = toExternalPermissionMode(prevMode)
  const newExternal = toExternalPermissionMode(newMode)
  if (prevExternal !== newExternal) {
    // 仅当外部化后的模式确实变化时才通知 CCR
    // 避免 default→bubble→default 这种内部转换产生无效通知
    const isUltraplan =
      newExternal === 'plan' &&
      newState.isUltraplanMode &&
      !oldState.isUltraplanMode
        ? true
        : null  // null 符合 RFC 7396（移除该键）
    notifySessionMetadataChanged({
      permission_mode: newExternal,       // 通知 CCR 更新外部元数据
      is_ultraplan_mode: isUltraplan,
    })
  }
  notifyPermissionModeChanged(newMode)    // 通知 SDK 状态流
}
```

这段代码揭示了一个重要的架构模式：**集中式副作用处理**。在重构之前，权限模式的同步分散在 8+ 个不同的代码路径中，只有 2 个正确地通知了 CCR。通过将所有副作用集中到 `onChangeAppState`，任何修改 `toolPermissionContext.mode` 的代码路径都会自动触发正确的同步，**零修改成本**。

### 3.3 模型设置持久化

```typescript
// src/state/onChangeAppState.ts:94-112
// 当用户通过 /model 命令切换模型时，自动持久化到设置文件
if (
  newState.mainLoopModel !== oldState.mainLoopModel &&
  newState.mainLoopModel === null
) {
  // 模型被清除（恢复默认）——从设置中移除
  updateSettingsForSource('userSettings', { model: undefined })
  setMainLoopModelOverride(null)
}

if (
  newState.mainLoopModel !== oldState.mainLoopModel &&
  newState.mainLoopModel !== null
) {
  // 模型被设置为新值——保存到设置文件
  updateSettingsForSource('userSettings', { model: newState.mainLoopModel })
  setMainLoopModelOverride(newState.mainLoopModel)
}
```

### 3.4 视图与配置持久化

```typescript
// src/state/onChangeAppState.ts:114-140
// expandedView 变更 → 持久化到全局配置
if (newState.expandedView !== oldState.expandedView) {
  const showExpandedTodos = newState.expandedView === 'tasks'
  const showSpinnerTree = newState.expandedView === 'teammates'
  if (
    getGlobalConfig().showExpandedTodos !== showExpandedTodos ||
    getGlobalConfig().showSpinnerTree !== showSpinnerTree
  ) {
    saveGlobalConfig(current => ({
      ...current,
      showExpandedTodos,  // 向后兼容旧配置字段名
      showSpinnerTree,
    }))
  }
}

// verbose 变更 → 持久化
if (
  newState.verbose !== oldState.verbose &&
  getGlobalConfig().verbose !== newState.verbose
) {
  saveGlobalConfig(current => ({ ...current, verbose: newState.verbose }))
}
```

### 3.5 认证缓存清除

```typescript
// src/state/onChangeAppState.ts:154-170
// settings 变更时清除所有认证缓存
// 确保 API Key、AWS、GCP 凭证变更立即生效
if (newState.settings !== oldState.settings) {
  try {
    clearApiKeyHelperCache()    // 清除 API Key 缓存
    clearAwsCredentialsCache()  // 清除 AWS 凭证缓存
    clearGcpCredentialsCache()  // 清除 GCP 凭证缓存

    // 当 settings.env 变更时重新应用环境变量
    // 这是仅增量操作：新变量被添加，已有变量可能被覆盖，不会删除
    if (newState.settings.env !== oldState.settings.env) {
      applyConfigEnvironmentVariables()
    }
  } catch (error) {
    logError(toError(error))  // 错误不应阻断状态变更流
  }
}
```

### 3.6 副作用完整清单

汇总 `onChangeAppState` 处理的所有副作用：

| 状态变更 | 副作用 | 外部系统 |
|---------|--------|---------|
| `toolPermissionContext.mode` | 通知 CCR + SDK 状态流 | 远程 Web UI、SDK 客户端 |
| `mainLoopModel` | 持久化到用户设置文件 | `~/.claude/settings.json` |
| `expandedView` | 持久化到全局配置 | `~/.claude/config.json` |
| `verbose` | 持久化到全局配置 | `~/.claude/config.json` |
| `tungstenPanelVisible`（Ant 限定） | 持久化到全局配置 | `~/.claude/config.json` |
| `settings` | 清除认证缓存、重新应用环境变量 | 内存缓存、`process.env` |

---

## 第四章：消息系统 src/utils/messages.ts

### 4.1 消息类型层次结构

消息系统是 Claude Code 中最大的单个模块之一（`src/utils/messages.ts` 达 5,512 行），它定义了用户、模型、系统之间所有通信的数据结构。消息类型构成了一个丰富的类型层次结构：

```
Message（联合类型）
├── UserMessage          ─── 用户输入消息
├── AssistantMessage     ─── 模型回复消息
├── AttachmentMessage    ─── 文件/图片附件
├── ProgressMessage      ─── 实时进度消息
├── SystemMessage        ─── 系统消息（13 个子类型）
│   ├── SystemInformationalMessage       ─── 一般信息
│   ├── SystemLocalCommandMessage        ─── 本地命令输出
│   ├── SystemCompactBoundaryMessage     ─── 压缩边界标记
│   ├── SystemMicrocompactBoundaryMessage ─── 微压缩边界
│   ├── SystemPermissionRetryMessage     ─── 权限重试通知
│   ├── SystemBridgeStatusMessage        ─── 远程控制状态
│   ├── SystemScheduledTaskFireMessage   ─── 定时任务触发
│   ├── SystemStopHookSummaryMessage     ─── Hook 执行摘要
│   ├── SystemTurnDurationMessage        ─── 回合耗时指标
│   ├── SystemAwaySummaryMessage         ─── 离开模式摘要
│   ├── SystemMemorySavedMessage         ─── 记忆持久化通知
│   ├── SystemAgentsKilledMessage        ─── 智能体终止通知
│   └── SystemAPIErrorMessage            ─── API 错误消息
├── ToolUseSummaryMessage ─── 工具批次完成摘要
└── TombstoneMessage     ─── 消息删除标记（墓碑）
```

### 4.2 核心消息类型详解

**UserMessage** 是用户输入的载体：

```typescript
// 从 src/utils/messages.ts:460-523 中的 createUserMessage() 推导出的结构
// UserMessage 的关键字段
type UserMessage = {
  type: 'user'
  message: {
    role: 'user'
    content: string | ContentBlockParam[]  // 纯文本或多内容块
  }
  uuid: UUID                   // 唯一标识符
  timestamp: string            // ISO 时间戳
  isMeta?: boolean             // true 表示隐藏于会话记录
  isVisibleInTranscriptOnly?: boolean  // 仅在记录中可见
  isVirtual?: boolean          // 仅用于显示，不发送到 API
  toolUseResult?: unknown      // 工具结果（合并消息时用）
  imagePasteIds?: number[]     // 粘贴图片 ID
  origin?: MessageOrigin       // 来源（undefined = 人类键盘输入）
}
```

**AssistantMessage** 包裹模型的完整响应：

```typescript
// 从 src/utils/messages.ts:411-432 中的 createAssistantMessage() 推导
type AssistantMessage = {
  type: 'assistant'
  message: BetaMessage         // Anthropic API 的完整响应对象
  uuid: UUID
  timestamp: string
  requestId?: string           // 请求追踪 ID
  apiError?: APIError          // API 错误信息
  isApiErrorMessage?: boolean  // 是否为 API 错误消息
  isVirtual?: boolean          // 仅用于显示
}
```

**TombstoneMessage**（墓碑消息）是一个特殊的设计：

```typescript
// 从 src/utils/messages.ts:70 导入，2930-2958 处理
// 墓碑消息——标记一条已被删除的消息
type TombstoneMessage = {
  type: 'tombstone'
  message: Message             // 被删除的原始消息
}
```

墓碑消息用于流式传输中的消息撤回。当服务器决定某条消息应该被移除时，发送一条 `TombstoneMessage`，客户端收到后通过 `onTombstone` 回调将原始消息从对话中移除：

```typescript
// src/utils/messages.ts:2930-2958（简化）
// 处理流式消息时的墓碑分发
export function handleMessageFromStream(
  message: Message | TombstoneMessage | StreamEvent | ...,
  // ...
  onTombstone?: (message: Message) => void,
) {
  if (message.type === 'tombstone') {
    onTombstone?.(message.message)  // 提取内部消息并回调
    return
  }
  // ... 处理其他消息类型
}
```

### 4.3 消息创建工厂函数

`src/utils/messages.ts` 提供了一套完整的工厂函数来创建各类消息：

| 工厂函数 | 行号 | 用途 |
|---------|------|------|
| `createAssistantMessage()` | 411 | 创建模型回复消息 |
| `createAssistantAPIErrorMessage()` | 435 | 创建 API 错误回复 |
| `createUserMessage()` | 460 | 创建用户输入消息 |
| `createUserInterruptionMessage()` | 545 | 创建用户中断消息 |
| `createProgressMessage()` | 603 | 创建实时进度消息 |
| `createSystemMessage()` | 4335 | 创建系统信息消息 |
| `createCompactBoundaryMessage()` | 4530 | 创建压缩边界标记 |
| `createMicrocompactBoundaryMessage()` | 4557 | 创建微压缩边界 |
| `createPermissionRetryMessage()` | 4354 | 创建权限重试通知 |
| `createStopHookSummaryMessage()` | 4398 | 创建 Hook 摘要 |
| `createTurnDurationMessage()` | 4428 | 创建回合耗时指标 |
| `createMemorySavedMessage()` | 4460 | 创建记忆保存通知 |
| `createAgentsKilledMessage()` | 4473 | 创建智能体终止通知 |
| `createToolUseSummaryMessage()` | 5101 | 创建工具批次完成摘要 |

### 4.4 消息预处理管道

当消息需要发送到 API 时，必须经过一个复杂的预处理管道，确保格式合规：

```
原始 Message[]
  │
  ▼
normalizeMessages()              ─── 将多内容块消息拆分为单块
  │                                   每块使用 deriveUUID() 生成确定性 UUID
  ▼
reorderMessagesInUI()            ─── 重排工具调用组：
  │                                   tool_use → pre_hooks → tool_result → post_hooks
  ▼
normalizeMessagesForAPI()        ─── 完整的 API 准备管道（2000~2370 行）
  │  ├── 附件重排序（冒泡到合适位置）
  │  ├── 虚拟消息过滤（移除 isVirtual 消息）
  │  ├── 错误块剥离（移除导致错误的 PDF/图片块）
  │  ├── 进度消息、系统消息过滤
  │  ├── 连续用户消息合并
  │  ├── 工具引用处理
  │  ├── 孤立 thinking 消息过滤
  │  ├── 空白助手消息过滤
  │  ├── 工具结果内容清理
  │  └── 图片尺寸验证
  ▼
ensureToolResultPairing()        ─── 防御性验证：
  │  ├── 为未配对的 tool_use 插入合成错误 tool_result
  │  ├── 剥离孤立的 tool_result
  │  └── 去重重复的 tool_use ID
  ▼
API-ready messages
```

`normalizeMessages()` 的核心逻辑：

```typescript
// src/utils/messages.ts:731-741（简化）
// 将多内容块消息拆分为单块标准化消息
export function normalizeMessages(messages: Message[]): NormalizedMessage[] {
  // 每个 AssistantMessage 可能包含多个 content blocks
  // （例如 text + tool_use + text）
  // 拆分后每块一条 NormalizedMessage
  // 使用 deriveUUID(parentUUID, index) 生成确定性子 UUID
  // 确保相同输入永远产生相同 ID
}
```

### 4.5 消息查找表优化

为了避免渲染时的 O(n) 遍历，`buildMessageLookups()` 预构建 O(1) 查找结构：

```typescript
// src/utils/messages.ts:1146（类型定义）
export type MessageLookups = {
  siblingToolUseIDs: Map<string, Set<string>>        // tool_use 的兄弟关系
  progressMessagesByToolUseID: Map<string, ProgressMessage[]>  // 进度消息
  inProgressHookCounts: Map<string, Map<HookEvent, number>>    // 进行中的 Hook
  resolvedHookCounts: Map<string, Map<HookEvent, number>>      // 已完成的 Hook
  toolResultByToolUseID: Map<string, NormalizedMessage>        // tool_use→result 映射
  toolUseByToolUseID: Map<string, ToolUseBlockParam>          // ID→tool_use 映射
  resolvedToolUseIDs: Set<string>      // 已完成的 tool_use ID 集合
  erroredToolUseIDs: Set<string>       // 出错的 tool_use ID 集合
  normalizedMessageCount: number       // 标准化消息总数
}
```

### 4.6 消息合并函数

当 API 不允许连续的同角色消息时，合并函数将它们组合：

```typescript
// src/utils/messages.ts:2411-2449（简化）
// 合并两条连续的用户消息
export function mergeUserMessages(a: UserMessage, b: UserMessage): UserMessage {
  // 通过 joinTextAtSeam() 拼接文本内容
  // 通过 hoistToolResults() 提升工具结果
  // 保留非 meta 消息的 UUID（供 snip 工具引用时使用）
  // 处理 meta/non-meta 优先级
}
```

---

## 第五章：文件状态缓存 src/utils/fileStateCache.ts

### 5.1 FileStateCache 的 LRU 设计

文件状态缓存使用 LRU（最近最少使用）策略管理 Claude 读取过的文件内容，防止内存无限增长：

```typescript
// src/utils/fileStateCache.ts:4-15
// 文件状态条目的数据结构
export type FileState = {
  content: string              // 文件内容
  timestamp: number            // 读取时间戳
  offset: number | undefined   // 读取的起始行偏移
  limit: number | undefined    // 读取的行数限制
  // 当此条目来自自动注入（如 CLAUDE.md）且注入内容
  // 与磁盘不一致时（剥离了 HTML 注释、frontmatter、截断了 MEMORY.md），
  // 标记为 partial view。Edit/Write 必须先执行显式 Read。
  // 这里的 content 保存的是磁盘原始内容（用于 diff），而非模型看到的内容。
  isPartialView?: boolean
}
```

```typescript
// src/utils/fileStateCache.ts:17-22
// 缓存配置常量
export const READ_FILE_STATE_CACHE_SIZE = 100  // 默认最大条目数
const DEFAULT_MAX_CACHE_SIZE_BYTES = 25 * 1024 * 1024  // 25MB 大小限制
```

### 5.2 路径规范化

`FileStateCache` 的一个精妙细节是所有路径键在访问前都经过 `normalize()` 处理：

```typescript
// src/utils/fileStateCache.ts:30-48
export class FileStateCache {
  private cache: LRUCache<string, FileState>

  constructor(maxEntries: number, maxSizeBytes: number) {
    this.cache = new LRUCache<string, FileState>({
      max: maxEntries,           // 最大条目数
      maxSize: maxSizeBytes,     // 最大字节数
      // 计算每个条目的大小——用于 LRU 的基于大小的驱逐
      sizeCalculation: value => Math.max(1, Buffer.byteLength(value.content)),
    })
  }

  get(key: string): FileState | undefined {
    return this.cache.get(normalize(key))   // 规范化路径后访问
  }

  set(key: string, value: FileState): this {
    this.cache.set(normalize(key), value)   // 规范化路径后存储
    return this
  }

  has(key: string): boolean {
    return this.cache.has(normalize(key))   // 规范化路径后检查
  }
}
```

路径规范化确保 `/foo/../bar/file.ts` 和 `/bar/file.ts` 命中同一个缓存条目，避免了由路径格式差异导致的缓存未命中。

### 5.3 缓存辅助函数

```typescript
// src/utils/fileStateCache.ts:108-142
// 将缓存转换为普通对象（用于压缩服务 compact.ts）
export function cacheToObject(cache: FileStateCache): Record<string, FileState> {
  return Object.fromEntries(cache.entries())
}

// 克隆缓存——保留大小配置（用于投机执行的隔离副本）
export function cloneFileStateCache(cache: FileStateCache): FileStateCache {
  const cloned = createFileStateCacheWithSizeLimit(cache.max, cache.maxSize)
  cloned.load(cache.dump())    // 使用 LRU 的 dump/load 进行高效克隆
  return cloned
}

// 合并两个缓存——较新条目（按 timestamp）覆盖较旧的
export function mergeFileStateCaches(
  first: FileStateCache,
  second: FileStateCache,
): FileStateCache {
  const merged = cloneFileStateCache(first)
  for (const [filePath, fileState] of second.entries()) {
    const existing = merged.get(filePath)
    // 仅当新条目的时间戳更新时才覆盖
    if (!existing || fileState.timestamp > existing.timestamp) {
      merged.set(filePath, fileState)
    }
  }
  return merged
}
```

### 5.4 归因追踪系统

文件状态缓存的一个重要消费者是**归因追踪系统**（`src/utils/commitAttribution.ts`），它在字符级别追踪 Claude 的代码贡献：

```typescript
// src/utils/commitAttribution.ts 的 AttributionState 类型
type AttributionState = {
  fileStates: Map<string, FileAttributionState>  // 每个文件的贡献追踪
  sessionBaselines: Map<string, { contentHash: string; mtime: number }>
  surface: string                 // 客户端类型（cli/ide/web/api）
  startingHeadSha: string | null  // 会话开始时的 Git HEAD
  promptCount: number             // 会话中的总提示次数
  promptCountAtLastCommit: number // 上次提交时的提示次数
  permissionPromptCount: number   // 权限提示次数
  escapeCount: number             // ESC 按键次数（取消权限提示）
}

// 每个文件的归因状态
type FileAttributionState = {
  contentHash: string              // 文件内容的 SHA-256 哈希
  claudeContribution: number       // Claude 写入的字符数
  mtime: number                    // 文件修改时间
}
```

归因系统通过 `trackFileModification()` 在每次文件编辑时计算 Claude 贡献的字符数，最终在 `git commit` 时生成类似 "93% by Claude" 的归因统计。

### 5.5 文件历史快照

文件历史系统（`src/utils/fileHistory.ts`）提供类似"撤销"的能力：

```typescript
// src/utils/fileHistory.ts:39-54
type FileHistorySnapshot = {
  messageId: UUID                              // 关联的消息 ID
  trackedFileBackups: Record<string, FileHistoryBackup>  // 文件备份
  timestamp: Date                              // 快照时间
}

type FileHistoryState = {
  snapshots: FileHistorySnapshot[]   // 快照列表（最多 100 个）
  trackedFiles: Set<string>          // 追踪的文件路径
  snapshotSequence: number           // 活动信号，每次快照递增
}
```

---

## 第六章：状态流转图

### 6.1 用户输入到 UI 更新的完整流程

```
用户按下 Enter
  │
  ▼
PromptInput.tsx                    ─── 捕获输入文本
  │
  ▼
REPL.tsx:onSubmit()                ─── 处理提交
  │ ├── createUserMessage()        ─── 创建用户消息
  │ └── setState(prev => ({        ─── 更新 AppState
  │       ...prev,                     添加消息到对话
  │     }))
  │       │
  │       ├── onChange 触发          ─── onChangeAppState() 检查副作用
  │       └── listeners 通知        ─── 所有 useAppState() 订阅者收到通知
  │             │
  │             ▼
  │           useSyncExternalStore  ─── React 检查 selector 返回值
  │             │                       Object.is 比较决定是否重渲染
  │             ▼
  │           组件重渲染             ─── 消息列表、状态栏等 UI 更新
  │
  ▼
QueryEngine.submitMessage()        ─── 提交到查询引擎
  │
  ▼
query.ts:queryLoop()               ─── 进入查询循环
  │
  ▼
claude.ts:queryModel()             ─── 调用 API
  │ └── 流式响应                    ─── 逐块接收
  │       │
  │       ▼
  │     handleMessageFromStream()   ─── 分发流事件
  │       │ ├── 文本块 → 更新消息内容 → setState → UI 实时渲染
  │       │ ├── 工具调用 → 进入工具执行流程
  │       │ └── 墓碑 → onTombstone() → 移除消息
  │       │
  ▼       ▼
  工具调用分支
  │
  ├── useCanUseTool()              ─── 权限检查
  │     └── setState 更新权限弹窗状态
  │
  ├── tool.call()                  ─── 执行工具
  │     └── 文件工具 → trackFileModification()
  │           └── setState 更新 attribution
  │
  └── 结果返回 → 继续查询循环
        └── setState 更新消息列表
```

### 6.2 多智能体场景下的状态隔离

```
主智能体（Leader）                      子智能体（Agent）
┌──────────────────────┐           ┌──────────────────────┐
│ AppState             │           │ 隔离的 AppState 切片    │
│ ├── tasks: {         │           │                      │
│ │   "agent-1": {     │  创建     │ 独立的消息历史          │
│ │     type: ...      │ ◄─────── │ 独立的权限上下文         │
│ │     messages: []   │           │ 独立的文件状态缓存       │
│ │     status: 'run'  │           │                      │
│ │   }                │           └──────────────────────┘
│ │ }                  │
│ ├── agentNameRegistry│
│ │   "explore" → "agent-1"
│ ├── viewingAgentTaskId│
│ │   → undefined (查看主视图)
│ │   → "agent-1" (查看子智能体)
│ └── foregroundedTaskId│
│     → "agent-1" (前台任务)
└──────────────────────┘
         │
         ▼
  enterTeammateView("agent-1")
         │ setState: viewingAgentTaskId = "agent-1"
         │           task.retain = true
         ▼
  UI 切换到子智能体的消息流
         │
         ▼
  exitTeammateView()
         │ setState: viewingAgentTaskId = undefined
         │           task.retain = false
         │           task.evictAfter = Date.now() + 30000
         ▼
  30秒后自动清除子智能体消息（释放内存）
```

### 6.3 状态同步在多进程场景中的边界

```
CLI 进程                          CCR Web 进程
┌──────────────────┐             ┌──────────────────┐
│ AppState (权威源)  │             │ 远程状态镜像       │
│                  │  WS 事件     │                  │
│ onChangeAppState │ ──────────► │ 接收 metadata     │
│  ├── 权限模式变更  │  CCR 通知   │  ├── 权限模式      │
│  ├── 模型变更     │             │  ├── ultraplan    │
│  └── 设置变更     │             │  └── ...          │
│                  │             │                  │
│ SDK 状态流       │ ──────────► │ IDE 扩展/Web UI    │
│  └── 权限模式    │  SSE 通知   │  └── 状态同步      │
└──────────────────┘             └──────────────────┘
```

---

## 设计哲学分析

### 可变 Store 的务实选择——工具服务于问题而非教条

Claude Code 选择可变 Store 而非 Redux 式不可变状态管理，体现了**务实主义优于教条主义**的设计理念。Redux 的不可变性保证虽然优雅，但在面对 `Map<string, AgentId>`、`Set<string>`、`vm.Context`、`AbortController`、回调函数等大量无法深度冻结的类型时，不可变性反而成为障碍。34 行的极简实现不仅完全满足了 React 集成的需求（通过 `useSyncExternalStore`），还避免了序列化/反序列化、结构共享、中间件管道等不可变方案的固有开销。这是一个"选择正确的工具解决正确的问题"的经典案例。

### 消息类型层次——可组合性的体现

消息系统的类型层次是**可组合性（Composability）**设计哲学的典型表现。通过定义统一的 `Message` 联合类型和 13 种 `SystemMessage` 子类型，系统可以在不修改渲染逻辑的情况下添加新的消息类型。每种子类型通过 `subtype` 判别联合（discriminated union）实现类型安全的分发。当需要添加新功能（如 `SystemMemorySavedMessage`、`SystemAgentsKilledMessage`）时，只需定义新的子类型和对应的工厂函数，UI 层通过 `subtype` 自动路由到正确的渲染分支。

### TombstoneMessage——优雅降级的实践

`TombstoneMessage` 是**优雅降级（Graceful Degradation）**原则的一个微观体现。在流式传输中，消息可能需要被撤回——而不是简单地从数组中删除（这在并发流中会导致索引混乱），系统使用"墓碑"标记被删除的消息。这确保了即使消息删除操作与流式接收竞争，消息历史仍然保持一致性。留下墓碑而非直接删除，也使得调试和审计成为可能。

### 文件缓存的 LRU——上下文窗口经济学的本地表现

`FileStateCache` 的 LRU 策略是**上下文窗口经济学（Context Window Economics）**在本地状态层面的延伸。正如 QueryEngine 使用 auto-compact 管理 API 的 token 预算，文件缓存使用 LRU 管理本地内存预算。25MB 的大小限制和 100 条目的数量限制确保缓存不会无限增长，而 `sizeCalculation` 回调根据实际内容大小进行驱逐决策。`mergeFileStateCaches()` 在合并时使用 timestamp 选择较新的条目，体现了"最新数据最有价值"的经济学原则。

### 归因追踪——人在回路的延伸

归因追踪系统是**人在回路（Human-in-the-Loop）**原则的一个延伸维度。通过在字符级别追踪 Claude 的代码贡献（`claudeContribution` 字段），系统让人类开发者能够精确了解"哪些代码是 Claude 写的"。`promptCount`、`escapeCount` 等统计进一步量化了人机交互的模式。这不仅服务于 commit message 中的归因标注（如 "93% by Claude"），更本质上是让人类始终保持对 AI 贡献的可见性和控制权。

### 集中式副作用——防御性编程的体现

`onChangeAppState` 的集中式副作用处理是**防御性编程（Defensive Programming）**的一个架构级实践。在重构之前，权限模式的同步分散在 8+ 个代码路径中，其中只有 2 个正确同步——这意味着 6 个路径存在静默 bug。将所有副作用集中到 `onChangeAppState`，从架构上消除了"忘记同步"的可能性。每一个 `setState` 调用自动触发完整的副作用链，**无论变更来自哪个代码路径**。这体现了"在失败发生之前预防失败"的防御性思维。

### 状态隔离——多智能体场景的安全边界

在多智能体场景中，每个子智能体通过 `createSubagentContext()` 获得隔离的状态切片——独立的消息历史、权限上下文和文件缓存。`tasks` 字典以 `taskId` 为键实现了松耦合的状态注册，使得智能体可以独立创建、运行和销毁而不影响其他智能体的状态。`teammateViewHelpers.ts` 中的 `release()` 函数在退出查看时清除消息并设置 `evictAfter` 定时器（30 秒），确保闲置智能体的内存最终被回收。这是**隔离与遏制（Isolation & Containment）**原则在状态层面的体现——每个智能体的状态变更不会意外污染其他智能体或主界面。

---

## 关键要点总结

1. **极简 Store**：34 行代码实现的 `createStore()` 是整个状态管理的基石。可变状态 + `Object.is()` 引用比较 + `useSyncExternalStore` 集成，简洁高效。

2. **AppState 的双层结构**：`DeepImmutable<{...}> & {...}` 在类型层面将不可变配置与可变运行时数据显式分离，兼顾类型安全与实用性。

3. **集中式副作用**：`onChangeAppState` 是所有非 UI 副作用的唯一入口——权限同步、设置持久化、缓存清除都在此处理，消除了分散同步的 bug 风险。

4. **消息类型层次**：16+ 种消息类型通过联合类型和判别联合实现类型安全的分发，预处理管道（`normalizeMessagesForAPI`）确保 API 兼容性。

5. **双重缓存策略**：`FileStateCache`（LRU，25MB）管理文件内容，`AttributionState`（Map）追踪 Claude 贡献，两者协同支持 commit 归因。

6. **多智能体隔离**：`tasks` 字典 + `createSubagentContext()` + 30 秒驱逐定时器，实现了智能体间的状态隔离与内存回收。

## 下一篇预览

**Doc 10：多智能体系统** 将深入分析 `AgentTool`（228KB）如何创建和管理子智能体、任务系统的类型层次（LocalShellTask、LocalAgentTask、RemoteAgentTask、InProcessTeammateTask）、Worktree 隔离机制如何在文件系统层面给每个智能体独立的工作副本，以及协调器模式如何编排多个智能体并行工作。本文中提到的 `tasks` 字典和 `agentNameRegistry` 将在那里展现它们的完整用途。
