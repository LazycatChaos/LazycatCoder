# Doc 10: 多智能体系统

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）、Doc 2（构建系统与运行时）、Doc 3（入口点与初始化流程）、Doc 4（终端 UI 系统）、Doc 5（命令系统）、Doc 6（工具系统）、Doc 7（查询引擎与 LLM 交互）、Doc 8（权限系统）、Doc 9（状态管理）

在前九篇文档中，我们分析的一直是"单个 Claude 智能体"的运行机制——一个用户对一个 AI、一个查询引擎、一套工具集。但 Claude Code 的真正野心远不止于此：它构建了一个**完整的多智能体协作系统**，让多个 AI 智能体能够并行工作、相互通信、共享成果，甚至组建团队来协作完成复杂任务。

想象一个场景：用户说"请帮我重构这个模块，同时修复其中的 bug 并编写测试"。在单智能体模式下，Claude 只能串行处理。但在多智能体模式下，主智能体可以创建三个子智能体——一个负责重构、一个修 bug、一个写测试——它们各自在独立的 Git Worktree 中并行工作，完成后将结果汇报给主智能体整合。

本文档是多智能体系统的第一部分，将深入分析四个核心组件：AgentTool（智能体创建与执行）、任务系统（生命周期管理）、团队管理（协作组织）、以及智能体间通信（消息传递）。

---

## 第一章：AgentTool 深度解析 src/tools/AgentTool/

### 1.1 目录结构概览

AgentTool 是 Claude Code 中最大的工具之一，其源码目录包含超过 6,700 行代码：

```
src/tools/AgentTool/
├── AgentTool.tsx          # 主工具定义（228KB）—— 入口、Schema、路由
├── runAgent.ts            # 智能体执行引擎（35KB）—— 查询循环、上下文构建
├── UI.tsx                 # 显示渲染（122KB）—— 进度、结果、分组展示
├── agentToolUtils.ts      # 工具函数（22KB）—— 进度追踪、结果处理
├── forkSubagent.ts        # Fork 模式实现（8.5KB）—— 缓存共享分叉
├── agentMemory.ts         # 智能体记忆（5.7KB）—— 跨迭代记忆持久化
├── agentMemorySnapshot.ts # 记忆快照（5.5KB）—— 状态序列化
├── loadAgentsDir.ts       # 智能体发现与加载（26KB）—— 自定义智能体定义
├── resumeAgent.ts         # 后台恢复（9.1KB）—— 恢复已暂停的智能体
├── constants.ts           # 常量定义
├── prompt.ts              # Prompt 模板生成
├── agentColorManager.ts   # 颜色管理器
└── built-in/              # 内置智能体定义
    ├── generalPurposeAgent.ts   # 通用智能体
    ├── planAgent.ts             # 规划智能体
    ├── exploreAgent.ts          # 探索智能体（只读）
    ├── verificationAgent.ts     # 验证智能体
    ├── claudeCodeGuideAgent.ts  # 帮助文档智能体
    └── statuslineSetup.ts       # 状态栏配置智能体
```

### 1.2 输入 Schema 与参数体系

AgentTool 的输入 Schema 采用了分层设计，基础参数所有模式可用，高级参数按 Feature Flag 门控：

```typescript
// src/tools/AgentTool/AgentTool.tsx:82-88
// 基础输入 Schema —— 所有模式下都可用的核心参数
const baseInputSchema = lazySchema(() => z.object({
  description: z.string()         // 3-5 个词的任务简述（用于 UI 显示）
    .describe('A short (3-5 word) description of the task'),
  prompt: z.string()              // 完整的任务指令
    .describe('The task for the agent to perform'),
  subagent_type: z.string()       // 智能体类型选择（可选）
    .optional()
    .describe('The type of specialized agent to use for this task'),
  model: z.enum(['sonnet', 'opus', 'haiku'])  // 模型覆盖（可选）
    .optional()
    .describe("Optional model override for this agent."),
  run_in_background: z.boolean()  // 后台运行开关（可选）
    .optional()
    .describe('Set to true to run this agent in the background.')
}));
```

当多智能体功能启用时，Schema 会扩展更多参数：

```typescript
// src/tools/AgentTool/AgentTool.tsx:91-102
// 完整 Schema —— 基础参数 + 多智能体参数 + 隔离参数
const fullInputSchema = lazySchema(() => {
  const multiAgentInputSchema = z.object({
    name: z.string().optional()       // 智能体名称（使其可通过 SendMessage 寻址）
      .describe('Name for the spawned agent.'),
    team_name: z.string().optional()  // 所属团队名称
      .describe('Team name for spawning.'),
    mode: permissionModeSchema()      // 权限模式（如 "plan" 需要审批）
      .optional()
      .describe('Permission mode for spawned teammate'),
  });
  return baseInputSchema()
    .merge(multiAgentInputSchema)     // 合并多智能体参数
    .extend({
      isolation: z.enum(['worktree'])  // 隔离模式（Git Worktree）
        .optional(),
      cwd: z.string().optional()       // 工作目录覆盖
    });
});
```

注意第 110-124 行的 Schema 精简逻辑——当某些 Feature Flag 关闭时，对应参数会通过 `.omit()` 从 Schema 中移除，确保模型永远看不到不可用的参数：

```typescript
// src/tools/AgentTool/AgentTool.tsx:110-125
// 根据 Feature Flag 动态精简 Schema
export const inputSchema = lazySchema(() => {
  // KAIROS 关闭时移除 cwd 参数
  const schema = feature('KAIROS')
    ? fullInputSchema()
    : fullInputSchema().omit({ cwd: true });

  // 后台任务被禁用或 Fork 模式启用时，移除 run_in_background 参数
  return isBackgroundTasksDisabled || isForkSubagentEnabled()
    ? schema.omit({ run_in_background: true })
    : schema;
});
```

### 1.3 输出 Schema 与结果类型

AgentTool 的输出根据执行模式分为多种类型：

```typescript
// src/tools/AgentTool/AgentTool.tsx:141-155
// 同步完成结果 —— 智能体在前台运行完毕
const syncOutputSchema = agentToolResultSchema().extend({
  status: z.literal('completed'),  // 标记为已完成
  prompt: z.string()               // 原始 prompt（用于上下文追踪）
});

// 异步启动结果 —— 智能体转入后台
const asyncOutputSchema = z.object({
  status: z.literal('async_launched'),
  agentId: z.string(),             // 异步智能体 ID
  description: z.string(),         // 任务描述
  prompt: z.string(),
  outputFile: z.string(),          // 输出文件路径（用于检查进度）
  canReadOutputFile: z.boolean()   // 调用方是否有读取能力
    .optional()
});
```

此外还有两种仅在内部使用的输出类型（不暴露在导出 Schema 中，以便死代码消除）：

```typescript
// src/tools/AgentTool/AgentTool.tsx:161-190
// 队友生成结果（仅多智能体模式）
type TeammateSpawnedOutput = {
  status: 'teammate_spawned';
  prompt: string;
  teammate_id: string;         // 队友任务 ID
  agent_id: string;            // 智能体标识
  name: string;                // 队友名称
  color?: string;              // UI 显示颜色
  team_name?: string;          // 所属团队
  plan_mode_required?: boolean; // 是否需要 Plan 审批
};

// 远程启动结果（仅内部用户 ant 模式）
type RemoteLaunchedOutput = {
  status: 'remote_launched';
  taskId: string;              // 远程任务 ID
  sessionUrl: string;          // 远程会话 URL
  description: string;
  prompt: string;
  outputFile: string;
};
```

### 1.4 内置智能体类型

`built-in/` 目录定义了六种预配置的智能体类型，每种都有特定的工具集和行为模式：

| 智能体类型 | 文件 | 用途 | 工具限制 |
|-----------|------|------|---------|
| `general-purpose` | `generalPurposeAgent.ts` | 默认类型，全能型 | 完整工具集 |
| `Explore` | `exploreAgent.ts` | 只读研究模式 | 无 Edit/Write/NotebookEdit |
| `Plan` | `planAgent.ts` | 创建实施计划 | 无 Edit/Write/NotebookEdit |
| `verification` | `verificationAgent.ts` | 测试与验证 | 完整工具集 |
| `claude-code-guide` | `claudeCodeGuideAgent.ts` | 帮助文档 | Read-only |
| `statusline-setup` | `statuslineSetup.ts` | 状态栏配置 | Read + Edit |

智能体类型选择的路由逻辑在 `call()` 方法中实现：

```typescript
// src/tools/AgentTool/AgentTool.tsx:318-323
// 智能体类型路由：
// - subagent_type 显式设定 → 使用指定类型（显式优先）
// - subagent_type 为空 + Fork 门控开启 → Fork 路径
// - subagent_type 为空 + Fork 门控关闭 → 默认通用智能体
const effectiveType = subagent_type
  ?? (isForkSubagentEnabled() ? undefined : GENERAL_PURPOSE_AGENT.agentType);
const isForkPath = effectiveType === undefined;
```

### 1.5 createSubagentContext()：隔离上下文构建

`createSubagentContext()` 是多智能体系统最关键的函数之一。它从父上下文创建一个**隔离的子上下文**，确保子智能体不会干扰父智能体的状态：

```typescript
// src/utils/forkedAgent.ts:345-462
export function createSubagentContext(
  parentContext: ToolUseContext,       // 父智能体的完整上下文
  overrides?: SubagentContextOverrides, // 可选的覆盖配置
): ToolUseContext {

  // 1. AbortController 隔离
  //    默认创建子控制器（父中止时子也中止，但子中止不影响父）
  //    可选共享父控制器（用于交互式子智能体）
  const abortController =
    overrides?.abortController ??
    (overrides?.shareAbortController
      ? parentContext.abortController                    // 共享：交互式智能体
      : createChildAbortController(parentContext.abortController)); // 隔离：默认

  // 2. 权限行为调整
  //    非共享控制器的子智能体 → 设置 shouldAvoidPermissionPrompts = true
  //    避免后台智能体弹出权限对话框干扰用户
  const getAppState: ToolUseContext['getAppState'] = overrides?.getAppState
    ? overrides.getAppState
    : overrides?.shareAbortController
      ? parentContext.getAppState                        // 交互式：直接使用父状态
      : () => {
          const state = parentContext.getAppState()
          return {
            ...state,
            toolPermissionContext: {
              ...state.toolPermissionContext,
              shouldAvoidPermissionPrompts: true,         // 后台：避免弹窗
            },
          }
        }

  return {
    // 3. 文件状态缓存 —— 克隆（不共享引用）
    readFileState: cloneFileStateCache(
      overrides?.readFileState ?? parentContext.readFileState,
    ),

    // 4. 记忆触发器 —— 全新（每个子智能体独立追踪）
    nestedMemoryAttachmentTriggers: new Set<string>(),
    loadedNestedMemoryPaths: new Set<string>(),
    dynamicSkillDirTriggers: new Set<string>(),
    discoveredSkillNames: new Set<string>(),
    toolDecisions: undefined,

    // 5. 状态写入 —— 默认 no-op（隔离）
    //    同步智能体可选共享（shareSetAppState: true）
    setAppState: overrides?.shareSetAppState
      ? parentContext.setAppState
      : () => {},                                        // 默认：写入无效

    // 6. 任务注册必须始终到达根 Store
    //    否则后台 Bash 任务会变成僵尸进程
    setAppStateForTasks:
      parentContext.setAppStateForTasks ?? parentContext.setAppState,

    // 7. 拒绝追踪 —— 隔离的子智能体需要本地拒绝计数器
    localDenialTracking: overrides?.shareSetAppState
      ? parentContext.localDenialTracking
      : createDenialTrackingState(),                     // 独立拒绝计数

    // 8. UI 回调 —— 子智能体全部设为 undefined（无法控制父 UI）
    addNotification: undefined,
    setToolJSX: undefined,
    setStreamMode: undefined,

    // 9. 智能体身份 —— 每个子智能体获得唯一 ID
    agentId: overrides?.agentId ?? createAgentId(),
    agentType: overrides?.agentType,

    // 10. 查询深度追踪 —— 深度递增
    queryTracking: {
      chainId: randomUUID(),
      depth: (parentContext.queryTracking?.depth ?? -1) + 1,
    },
  }
}
```

这个函数体现了一个核心设计原则：**默认隔离，显式共享**。子智能体默认情况下不能修改父状态、不能弹出 UI 对话框、不能看到父的工具决策记录。只有通过显式的 `shareSetAppState: true` 等选项，才能打开特定的共享通道。

### 1.6 后台任务管理

AgentTool 支持将子智能体转入后台运行，这对于长时间运行的任务尤为重要：

```typescript
// src/tools/AgentTool/AgentTool.tsx:62-77
// 进度提示阈值 —— 2 秒后显示"按 Esc 转入后台"提示
const PROGRESS_THRESHOLD_MS = 2000;

// 模块加载时检查后台任务是否被环境变量禁用
const isBackgroundTasksDisabled =
  isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_BACKGROUND_TASKS);

// 自动后台阈值（0 = 禁用）
// 环境变量或 GrowthBook 门控控制
function getAutoBackgroundMs(): number {
  // 环境变量或远程配置开启时，2 分钟后自动转入后台
  if (isEnvTruthy(process.env.CLAUDE_AUTO_BACKGROUND_TASKS)
      || getFeatureValue_CACHED_MAY_BE_STALE(
           'tengu_auto_background_agents', false)) {
    return 120_000;  // 120 秒
  }
  return 0;  // 默认禁用自动后台
}
```

后台智能体通过 `emitTaskProgress()` 持续报告进度，确保用户能在 UI 中看到子智能体的工作状态。

### 1.7 多智能体生成路由

`call()` 方法的核心路由逻辑决定了请求走哪条路径：

```typescript
// src/tools/AgentTool/AgentTool.tsx:282-316
// 判断是否为多智能体生成请求
// 条件：team_name 已设置 + name 已提供
if (teamName && name) {
  // 查找匹配的智能体定义（用于获取颜色等元数据）
  const agentDef = subagent_type
    ? toolUseContext.options.agentDefinitions.activeAgents
        .find(a => a.agentType === subagent_type)
    : undefined;

  // 调用 spawnTeammate() 生成队友
  const result = await spawnTeammate({
    name,
    prompt,
    description,
    team_name: teamName,
    use_splitpane: true,
    plan_mode_required: spawnMode === 'plan',  // plan 模式需要审批
    model: model ?? agentDef?.model,
    agent_type: subagent_type,
    invokingRequestId: assistantMessage?.requestId
  }, toolUseContext);

  // 构造 TeammateSpawnedOutput（通过 unknown 断言绕过类型检查）
  const spawnResult: TeammateSpawnedOutput = {
    status: 'teammate_spawned' as const,
    prompt,
    ...result.data
  };
  return { data: spawnResult } as unknown as { data: Output };
}
```

路由决策树如下：

```
AgentTool.call() 入口
│
├─ teamName && name ？
│  └─ YES → spawnTeammate() → TeammateSpawnedOutput
│
├─ isolation === 'remote' ？ (仅 ant 用户)
│  └─ YES → teleportToRemote() → RemoteLaunchedOutput
│
├─ isForkPath ？（Fork 子智能体实验）
│  └─ YES → Fork 路径（缓存共享）
│
└─ 标准路径
   ├─ run_in_background === true → 异步注册 → AsyncLaunchedOutput
   └─ 前台同步运行 → runAgent() → CompletedOutput
```

---

## 第二章：任务系统 src/tasks/

### 2.1 任务类型层次

Claude Code 的任务系统是多智能体架构的骨架。每种智能体执行模式对应一种任务类型，统一管理生命周期：

```typescript
// src/Task.ts:6-13
// 七种任务类型覆盖所有执行模式
export type TaskType =
  | 'local_bash'            // 本地 Shell 命令（BashTool 后台任务）
  | 'local_agent'           // 本地子智能体（前台或后台）
  | 'remote_agent'          // 远程云端执行（CCR）
  | 'in_process_teammate'   // 进程内队友（团队成员）
  | 'local_workflow'        // 工作流执行
  | 'monitor_mcp'           // MCP 监控任务
  | 'dream'                 // Dream 任务（实验性）
```

每种类型通过单字母前缀生成唯一 ID，确保不同类型的任务在命名空间上完全隔离：

```typescript
// src/Task.ts:78-106
// 任务 ID 前缀映射表
const TASK_ID_PREFIXES: Record<string, string> = {
  local_bash: 'b',              // b + 8位随机字符，如 "b3f7a2c1e"
  local_agent: 'a',             // a + 8位随机字符
  remote_agent: 'r',            // r + 8位随机字符
  in_process_teammate: 't',     // t + 8位随机字符
  local_workflow: 'w',
  monitor_mcp: 'm',
  dream: 'd',
}

// 安全的随机 ID 生成器
// 36^8 ≈ 2.8 万亿种组合 —— 足以抵御暴力符号链接攻击
const TASK_ID_ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyz'

export function generateTaskId(type: TaskType): string {
  const prefix = getTaskIdPrefix(type)
  const bytes = randomBytes(8)   // 密码学安全的随机字节
  let id = prefix
  for (let i = 0; i < 8; i++) {
    id += TASK_ID_ALPHABET[bytes[i]! % TASK_ID_ALPHABET.length]
  }
  return id
}
```

### 2.2 任务状态生命周期

所有任务共享统一的五状态生命周期模型：

```typescript
// src/Task.ts:15-29
export type TaskStatus =
  | 'pending'      // 已创建，尚未启动
  | 'running'      // 正在执行
  | 'completed'    // 终态：成功完成
  | 'failed'       // 终态：执行失败
  | 'killed'       // 终态：被手动终止

// 判断是否为终态的辅助函数
// 用于防止向已终止的队友注入消息、驱逐已完成的任务等
export function isTerminalTaskStatus(status: TaskStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'killed'
}
```

状态流转图：

```
            ┌──────────────────────────────────────┐
            │                                      │
            ▼                                      │
  ┌─────────────┐     ┌─────────────┐     ┌───────┴─────┐
  │   pending    │────▶│   running   │────▶│  completed   │
  └─────────────┘     └──────┬──────┘     └─────────────┘
                             │
                             ├────────▶ ┌─────────────┐
                             │          │   failed     │
                             │          └─────────────┘
                             │
                             └────────▶ ┌─────────────┐
                                        │   killed     │
                                        └─────────────┘
```

### 2.3 TaskStateBase：任务基础结构

所有任务类型共享相同的基础字段：

```typescript
// src/Task.ts:44-57
export type TaskStateBase = {
  id: string             // 唯一标识（前缀 + 8位随机字符）
  type: TaskType          // 任务类型
  status: TaskStatus      // 当前状态
  description: string     // 人类可读的任务描述
  toolUseId?: string      // 触发此任务的 tool_use ID
  startTime: number       // 创建时间（毫秒时间戳）
  endTime?: number        // 结束时间（仅终态有值）
  totalPausedMs?: number  // 累计暂停时间
  outputFile: string      // 输出流文件路径
  outputOffset: number    // 流式读取位置偏移
  notified: boolean       // 是否已发送完成通知
}
```

任务创建通过工厂函数完成：

```typescript
// src/Task.ts:108-120
export function createTaskStateBase(
  id: string,
  type: TaskType,
  description: string,
  toolUseId?: string,
): TaskStateBase {
  return {
    id,
    type,
    status: 'pending',        // 初始状态总是 pending
    description,
    toolUseId,
    startTime: Date.now(),
    outputFile: getTaskOutputPath(id),  // 磁盘输出路径
    outputOffset: 0,
    notified: false
  }
}
```

### 2.4 LocalAgentTask：子智能体任务

`LocalAgentTask` 是最常用的子智能体任务类型，管理同步和异步子智能体的执行：

```typescript
// src/tasks/LocalAgentTask/LocalAgentTask.tsx:33-57
// 进度追踪类型
export type AgentProgress = {
  toolUseCount: number;           // 已使用的工具次数
  tokenCount: number;             // 累计 Token 消耗
  lastActivity?: ToolActivity;    // 最近一次工具活动
  recentActivities?: ToolActivity[]; // 最近 5 次活动记录
  summary?: string;               // 进度摘要文本
};

// 进度追踪器 —— 区分累计和增量 Token 计数
export type ProgressTracker = {
  toolUseCount: number;
  latestInputTokens: number;      // 输入 Token（累计值，取最新）
  cumulativeOutputTokens: number; // 输出 Token（各轮求和）
  recentActivities: ToolActivity[];
};

// 工具活动记录
export type ToolActivity = {
  toolName: string;
  input: Record<string, unknown>;
  activityDescription?: string;   // 预计算的描述，如 "Reading src/foo.ts"
  isSearch?: boolean;             // 是否为搜索操作
  isRead?: boolean;               // 是否为读取操作
};
```

`ProgressTracker` 的设计反映了一个重要的 API 特性：Claude API 的 `input_tokens` 是每轮累计值（包含所有历史上下文），而 `output_tokens` 是每轮增量值。因此追踪器对两者采用不同的聚合策略。

### 2.5 InProcessTeammateTask：进程内队友

`InProcessTeammateTask` 是团队系统中最复杂的任务类型，管理在同一进程内运行的队友智能体：

```typescript
// src/tasks/InProcessTeammateTask/types.ts:13-76
// 队友身份信息 —— 存储为纯数据（非引用）
export type TeammateIdentity = {
  agentId: string       // 完整 ID，如 "researcher@my-team"
  agentName: string     // 名称部分，如 "researcher"
  teamName: string      // 所属团队
  color?: string        // UI 显示颜色
  planModeRequired: boolean  // 是否需要 Plan 审批
  parentSessionId: string    // 领导者的会话 ID
}

export type InProcessTeammateTaskState = TaskStateBase & {
  type: 'in_process_teammate'

  identity: TeammateIdentity          // 身份信息

  // 执行相关
  prompt: string                      // 初始任务指令
  model?: string                      // 模型覆盖
  selectedAgent?: AgentDefinition     // 智能体定义（可选）
  abortController?: AbortController   // 终止整个队友
  currentWorkAbortController?: AbortController  // 仅终止当前轮次

  // Plan 模式
  awaitingPlanApproval: boolean       // 是否等待计划审批
  permissionMode: PermissionMode      // 权限模式（可独立切换）

  // 状态
  error?: string
  result?: AgentToolResult
  progress?: AgentProgress
  messages?: Message[]                // UI 对话历史（有上限）

  // 生命周期
  isIdle: boolean                     // 是否空闲（等待新任务）
  shutdownRequested: boolean          // 是否已请求关闭
  onIdleCallbacks?: Array<() => void> // 空闲回调队列

  // 进度追踪
  lastReportedToolCount: number
  lastReportedTokenCount: number
}
```

注意 `messages` 字段有严格的上限控制，这是一个从生产事故中总结的教训：

```typescript
// src/tasks/InProcessTeammateTask/types.ts:89-120
// UI 消息上限 —— 防止内存泄漏
// 生产分析显示：500+ 轮次的会话中每个智能体占用 ~20MB RSS
// 一个极端案例在 2 分钟内启动了 292 个智能体，内存达到 36.8GB
// 根本原因是这个数组持有每条消息的第二份完整副本
export const TEAMMATE_MESSAGES_UI_CAP = 50

// 有上限的消息追加 —— 超过上限时丢弃最旧的消息
export function appendCappedMessage<T>(
  prev: readonly T[] | undefined,
  item: T,
): T[] {
  if (prev === undefined || prev.length === 0) {
    return [item]
  }
  // 达到上限时，保留最新的 49 条 + 新消息
  if (prev.length >= TEAMMATE_MESSAGES_UI_CAP) {
    const next = prev.slice(-(TEAMMATE_MESSAGES_UI_CAP - 1))
    next.push(item)
    return next
  }
  return [...prev, item]
}
```

### 2.6 RemoteAgentTask：远程执行

远程任务类型用于将工作负载发送到云端执行（Claude Code Remote）：

```typescript
// src/tasks/RemoteAgentTask/RemoteAgentTask.tsx:22-59
export type RemoteAgentTaskState = TaskStateBase & {
  type: 'remote_agent'
  remoteTaskType: RemoteTaskType  // 远程任务子类型
  sessionId: string               // 远程会话 ID
  command: string                 // 执行命令
  title: string                   // 任务标题
  todoList: TodoList              // 关联的待办列表
  log: SDKMessage[]               // SDK 消息日志
  isLongRunning?: boolean         // 是否为长时间运行
  pollStartedAt: number           // 轮询开始时间
  isRemoteReview?: boolean        // 是否为远程代码审查
  reviewProgress?: {              // 审查进度跟踪
    stage?: 'finding' | 'verifying' | 'synthesizing'
    bugsFound: number
    bugsVerified: number
  }
}
```

远程任务还支持可插拔的完成检查器，允许不同类型的远程任务定义自己的完成逻辑：

```typescript
// src/tasks/RemoteAgentTask/RemoteAgentTask.tsx:72-86
export type RemoteTaskCompletionChecker =
  (remoteTaskMetadata: RemoteTaskMetadata | undefined)
    => Promise<string | null>

const completionCheckers =
  new Map<RemoteTaskType, RemoteTaskCompletionChecker>()

// 注册完成检查器 —— 策略模式
export function registerCompletionChecker(
  remoteTaskType: RemoteTaskType,
  checker: RemoteTaskCompletionChecker
): void
```

### 2.7 任务调度接口

所有任务类型实现统一的 `Task` 接口用于调度：

```typescript
// src/Task.ts:69-76
// 统一任务接口
// 注意：只有 kill 是多态的
// spawn 和 render 从未被多态调用过（在 #22546 中移除）
export type Task = {
  name: string
  type: TaskType
  kill(taskId: string, setAppState: SetAppState): Promise<void>
}
```

这个精简的接口反映了一个务实的设计决策——只抽象真正需要多态调度的操作。`spawn` 和 `render` 在每种任务类型中的逻辑差异太大，强行抽象反而增加复杂度。

---

## 第三章：团队管理

### 3.1 团队概念

Claude Code 的"团队"（Team）是一组协作智能体的组织单元。每个团队有一个**领导者**（Team Lead）和多个**队友**（Teammates）：

- **领导者**：用户直接交互的主会话，负责任务分配和结果整合
- **队友**：领导者生成的子智能体，各自独立工作，通过邮箱通信

团队的持久化数据存储在 TeamFile 中：

```typescript
// src/utils/swarm/teamHelpers.ts:64-90
export type TeamFile = {
  name: string                   // 团队名称
  description?: string           // 团队描述
  createdAt: number              // 创建时间
  leadAgentId: string            // 领导者 ID（如 "team-lead@my-team"）
  leadSessionId?: string         // 领导者会话 ID（用于发现）
  hiddenPaneIds?: string[]       // 隐藏的终端面板
  teamAllowedPaths?: TeamAllowedPath[] // 团队允许的路径
  members: Array<{
    agentId: string              // 成员 ID（如 "researcher@my-team"）
    name: string                 // 成员名称
    agentType?: string           // 智能体类型
    model?: string               // 使用的模型
    prompt?: string              // 初始指令
    color?: string               // UI 颜色
    planModeRequired?: boolean   // 是否需要计划审批
    joinedAt: number             // 加入时间
    tmuxPaneId: string           // Tmux 面板 ID
    cwd: string                  // 工作目录
    worktreePath?: string        // Worktree 路径
    sessionId?: string           // 会话 ID
    subscriptions: string[]      // 消息订阅
    backendType?: BackendType    // 后端类型：'tmux' | 'iterm2' | 'in-process'
    isActive?: boolean           // 活跃状态
    mode?: PermissionMode        // 权限模式
  }>
}
```

### 3.2 TeamCreateTool：团队创建

TeamCreateTool 负责创建新团队并初始化所有必要的基础设施：

```typescript
// src/tools/TeamCreateTool/TeamCreateTool.ts:128-237
async call(input, context) {
  const { setAppState, getAppState } = context
  const { team_name, description, agent_type } = input

  // 1. 检查是否已在团队中 —— 每个领导者只能管理一个团队
  const existingTeam = getAppState().teamContext?.teamName
  if (existingTeam) {
    throw new Error(
      `Already leading team "${existingTeam}". ` +
      `Use TeamDelete to end the current team before creating a new one.`
    )
  }

  // 2. 名称冲突处理 —— 自动生成唯一名称
  const finalTeamName = generateUniqueTeamName(team_name)

  // 3. 创建确定性的领导者 ID
  //    格式：team-lead@finalTeamName
  const leadAgentId = formatAgentId(TEAM_LEAD_NAME, finalTeamName)
  const leadAgentType = agent_type || TEAM_LEAD_NAME

  // 4. 构造 TeamFile 并写入磁盘
  const teamFile: TeamFile = {
    name: finalTeamName,
    description,
    createdAt: Date.now(),
    leadAgentId,
    leadSessionId: getSessionId(),
    members: [{
      agentId: leadAgentId,
      name: TEAM_LEAD_NAME,
      agentType: leadAgentType,
      model: leadModel,
      joinedAt: Date.now(),
      tmuxPaneId: '',
      cwd: getCwd(),
      subscriptions: [],
    }],
  }
  await writeTeamFileAsync(finalTeamName, teamFile)
  // 注册会话清理（防止团队文件永久留在磁盘上）
  registerTeamForSessionCleanup(finalTeamName)

  // 5. 初始化任务列表目录
  //    团队 = 项目 = 任务列表
  const taskListId = sanitizeName(finalTeamName)
  await resetTaskList(taskListId)
  await ensureTasksDir(taskListId)
  setLeaderTeamName(sanitizeName(finalTeamName))

  // 6. 更新 AppState —— 注册团队上下文
  setAppState(prev => ({
    ...prev,
    teamContext: {
      teamName: finalTeamName,
      teamFilePath,
      leadAgentId,
      teammates: {
        [leadAgentId]: {
          name: TEAM_LEAD_NAME,
          agentType: leadAgentType,
          color: assignTeammateColor(leadAgentId),  // 分配 UI 颜色
          tmuxSessionName: '',
          tmuxPaneId: '',
          cwd: getCwd(),
          spawnedAt: Date.now(),
        },
      },
    },
  }))

  return {
    data: {
      team_name: finalTeamName,
      team_file_path: teamFilePath,
      lead_agent_id: leadAgentId,
    },
  }
}
```

注意代码中的关键设计决策（第 224-228 行的注释）：领导者故意**不设置** `CLAUDE_CODE_AGENT_ID` 环境变量，因为领导者不是"队友"——`isTeammate()` 对领导者应返回 `false`，否则会错误触发邮箱轮询。

### 3.3 团队层级限制

多智能体并发数量受订阅层级控制，这是渐进信任模型在多智能体领域的直接体现：

```typescript
// src/utils/planModeV2.ts:5-29
export function getPlanModeV2AgentCount(): number {
  // 环境变量覆盖（开发/调试用，上限 10）
  if (process.env.CLAUDE_CODE_PLAN_V2_AGENT_COUNT) {
    const count = parseInt(process.env.CLAUDE_CODE_PLAN_V2_AGENT_COUNT, 10)
    if (!isNaN(count) && count > 0 && count <= 10) {
      return count
    }
  }

  const subscriptionType = getSubscriptionType()
  const rateLimitTier = getRateLimitTier()

  // Tier 1：Claude Max 20x 用户 → 3 个并发智能体
  if (subscriptionType === 'max'
      && rateLimitTier === 'default_claude_max_20x') {
    return 3
  }
  // Tier 2：企业版或团队版 → 3 个并发智能体
  if (subscriptionType === 'enterprise'
      || subscriptionType === 'team') {
    return 3
  }
  // 默认：免费/Pro 用户 → 1 个智能体
  return 1
}
```

| 订阅类型 | 速率限制层级 | 最大并发智能体数 |
|---------|------------|---------------|
| Max | 20x | 3 |
| Enterprise | 任意 | 3 |
| Team | 任意 | 3 |
| Free / Pro | 任意 | 1 |

探索智能体（Explore）有独立的限制，固定为 3 个：

```typescript
// src/utils/planModeV2.ts:31-43
export function getPlanModeV2ExploreAgentCount(): number {
  if (process.env.CLAUDE_CODE_PLAN_V2_EXPLORE_AGENT_COUNT) {
    const count = parseInt(...)
    if (!isNaN(count) && count > 0 && count <= 10) {
      return count
    }
  }
  return 3  // 探索智能体始终允许 3 个（只读，风险低）
}
```

### 3.4 队友生成流程

当 AgentTool 接收到带 `team_name` 和 `name` 的请求时，会通过 `spawnTeammate()` 生成新的队友。队友有三种运行后端：

| 后端类型 | 特点 | 适用场景 |
|---------|------|---------|
| `in-process` | 同一进程内运行 | 默认模式，低开销 |
| `tmux` | 独立 Tmux 面板 | 需要独立终端 |
| `iterm2` | iTerm2 面板 | macOS iTerm2 用户 |

进程内队友的执行流程：

```
用户请求 "帮我研究这个问题"
        │
        ▼
AgentTool.call()
  │ teamName && name → spawnTeammate()
  │
  ▼
spawnTeammate()
  │ 1. 确定后端类型（in-process / tmux / iterm2）
  │ 2. 创建 InProcessTeammateTaskState
  │ 3. 注册到 AppState.tasks
  │ 4. 更新 TeamFile（添加成员）
  │ 5. 分配 UI 颜色
  │
  ▼
startInProcessTeammate()
  │ 6. 创建隔离的 ToolUseContext
  │ 7. 启动查询循环（runAgent）
  │ 8. 轮询邮箱等待新消息
  │
  ▼
队友独立运行 ←→ 通过邮箱与领导者/其他队友通信
```

---

## 第四章：智能体间通信 src/tools/SendMessageTool/

### 4.1 通信机制概览

Claude Code 的多智能体通信建立在**基于文件的邮箱系统**之上。每个队友有一个专属邮箱目录，消息以 JSONL 格式追加写入：

```
~/.claude/teams/{team_name}/mailbox/{agent_name}/messages.jsonl
```

这个设计选择看似原始，但极为可靠——文件系统是最简单的进程间通信（IPC）机制，无需额外的消息队列或 Socket 服务器。

### 4.2 SendMessageTool 输入 Schema

SendMessageTool 支持三种消息类型：纯文本、结构化控制消息、和广播：

```typescript
// src/tools/SendMessageTool/SendMessageTool.ts:46-87
// 结构化消息类型（协议消息）
const StructuredMessage = lazySchema(() =>
  z.discriminatedUnion('type', [
    // 关闭请求 —— 领导者请求队友关闭
    z.object({
      type: z.literal('shutdown_request'),
      reason: z.string().optional(),
    }),
    // 关闭响应 —— 队友同意或拒绝关闭
    z.object({
      type: z.literal('shutdown_response'),
      request_id: z.string(),
      approve: semanticBoolean(),      // 模糊布尔解析（支持 "yes"/"true"/1）
      reason: z.string().optional(),
    }),
    // 计划审批响应 —— 领导者审批队友的计划
    z.object({
      type: z.literal('plan_approval_response'),
      request_id: z.string(),
      approve: semanticBoolean(),
      feedback: z.string().optional(),  // 审批反馈
    }),
  ]),
)

// 完整输入 Schema
const inputSchema = lazySchema(() =>
  z.object({
    to: z.string()                // 收件人：队友名称、"*" 广播、
      .describe(                  //   "uds:<path>" 本地对等、
        'Recipient: teammate name, ' + //   "bridge:<id>" 远程对等
        '"*" for broadcast, ...'
      ),
    summary: z.string().optional()  // 5-10 字摘要（UI 预览用）
      .describe('A 5-10 word summary shown as a preview in the UI'),
    message: z.union([
      z.string()                    // 纯文本消息
        .describe('Plain text message content'),
      StructuredMessage(),          // 结构化协议消息
    ]),
  }),
)
```

### 4.3 消息路由机制

SendMessageTool 的路由逻辑根据收件人地址的格式决定投递路径：

```typescript
// 路由决策树（简化的伪代码）
// src/tools/SendMessageTool/SendMessageTool.ts

if (to === '*') {
  // 广播 —— 发送给团队中除自己外的所有成员
  return handleBroadcast(content, summary, context)
}

if (to.startsWith('uds:')) {
  // Unix Domain Socket —— 本地对等进程通信
  return sendToUdsSocket(socketPath, message)
}

if (to.startsWith('bridge:')) {
  // 远程控制桥接 —— 跨机器通信
  return postInterClaudeMessage(sessionId, message)
}

// 默认：按名称路由到队友
// 1. 先检查进程内子智能体（agentNameRegistry）
// 2. 再检查队友邮箱
```

### 4.4 纯文本消息投递

最常见的通信形式是纯文本消息，通过文件邮箱系统投递：

```typescript
// src/tools/SendMessageTool/SendMessageTool.ts:149-189
async function handleMessage(
  recipientName: string,       // 收件人名称
  content: string,             // 消息内容
  summary: string | undefined, // UI 摘要
  context: ToolUseContext,
): Promise<{ data: MessageOutput }> {
  const appState = context.getAppState()
  const teamName = getTeamName(appState.teamContext)
  const senderName =
    getAgentName() || (isTeammate() ? 'teammate' : TEAM_LEAD_NAME)
  const senderColor = getTeammateColor()

  // 写入收件人的邮箱文件（JSONL 追加写入）
  await writeToMailbox(
    recipientName,
    {
      from: senderName,           // 发件人名称
      text: content,              // 消息文本
      summary,                    // UI 预览摘要
      timestamp: new Date().toISOString(), // ISO 8601 时间戳
      color: senderColor,         // 发件人颜色（UI 用）
    },
    teamName,
  )

  return {
    data: {
      success: true,
      message: `Message sent to ${recipientName}'s inbox`,
      routing: {
        sender: senderName,
        senderColor,
        target: `@${recipientName}`,
        targetColor: findTeammateColor(appState, recipientName),
        summary,
        content,
      },
    },
  }
}
```

### 4.5 广播机制

广播消息发送给团队中除自己外的所有成员：

```typescript
// src/tools/SendMessageTool/SendMessageTool.ts:191-259
async function handleBroadcast(
  content: string,
  summary: string | undefined,
  context: ToolUseContext,
): Promise<{ data: BroadcastOutput }> {
  // 读取团队文件获取所有成员列表
  const teamFile = await readTeamFileAsync(teamName)

  const senderName = getAgentName()
    || (isTeammate() ? 'teammate' : TEAM_LEAD_NAME)

  // 过滤掉自己
  const recipients: string[] = []
  for (const member of teamFile.members) {
    if (member.name.toLowerCase() === senderName.toLowerCase()) {
      continue  // 跳过自己
    }
    recipients.push(member.name)
  }

  // 逐一写入每个收件人的邮箱
  for (const recipientName of recipients) {
    await writeToMailbox(recipientName, {
      from: senderName,
      text: content,
      summary,
      timestamp: new Date().toISOString(),
      color: senderColor,
    }, teamName)
  }

  return {
    data: {
      success: true,
      message: `Message broadcast to ${recipients.length} teammate(s)`,
      recipients,
    },
  }
}
```

### 4.6 结构化控制消息

除了纯文本，SendMessageTool 还支持三种结构化控制消息，实现智能体间的协议级通信：

**关闭协议**：领导者请求队友优雅关闭

```
领导者                              队友
  │                                  │
  │── shutdown_request ──────────▶  │
  │   { reason: "任务完成" }         │
  │                                  │（完成当前工作）
  │  ◀── shutdown_response ─────────│
  │   { approve: true }              │
  │                                  │（队友退出）
```

**计划审批协议**：队友提交计划等待领导者审批

```
队友                                领导者
  │                                  │
  │── plan (等待审批) ──────────────▶│
  │                                  │（查看计划）
  │  ◀── plan_approval_response ────│
  │   { approve: true,               │
  │     feedback: "方向正确" }        │
  │                                  │
  │（按计划执行）                     │
```

### 4.7 同步 vs 异步通信

多智能体通信有两种模式，取决于智能体的运行方式：

**同步通信（进程内子智能体）**：
- 消息通过 `queuePendingMessage()` 存入 `pendingMessages: string[]` 队列
- 在工具轮次边界由 `drainPendingMessages()` 消费
- 消息作为下一轮用户输入注入子智能体的查询循环
- 延迟极低（同进程内函数调用）

**异步通信（队友 / 跨机器）**：
- 消息写入文件邮箱（JSONL 格式追加）
- 接收方在工具轮次边界轮询邮箱
- 通过 JSON 消息解析和分发
- 延迟取决于轮询间隔

### 4.8 输出类型体系

SendMessageTool 根据消息类型返回不同的输出结构：

```typescript
// src/tools/SendMessageTool/SendMessageTool.ts:101-131
// 普通消息输出
export type MessageOutput = {
  success: boolean
  message: string
  routing?: MessageRouting  // 路由信息（用于 UI 渲染箭头）
}

// 广播输出
export type BroadcastOutput = {
  success: boolean
  message: string
  recipients: string[]       // 实际收到消息的成员列表
  routing?: MessageRouting
}

// 请求输出（shutdown_request / plan 提交）
export type RequestOutput = {
  success: boolean
  message: string
  request_id: string         // 请求 ID（用于匹配响应）
  target: string             // 目标成员
}

// 响应输出（shutdown_response / plan_approval_response）
export type ResponseOutput = {
  success: boolean
  message: string
  request_id?: string        // 关联的请求 ID
}

// 联合类型
export type SendMessageToolOutput =
  | MessageOutput
  | BroadcastOutput
  | RequestOutput
  | ResponseOutput
```

`MessageRouting` 类型特别值得注意——它包含发件人和收件人的颜色信息，使 UI 能够渲染彩色的消息路由箭头，让用户直观地看到智能体间的通信流动：

```typescript
// src/tools/SendMessageTool/SendMessageTool.ts:92-99
export type MessageRouting = {
  sender: string           // 发件人名称
  senderColor?: string     // 发件人颜色
  target: string           // 收件人名称（带 @ 前缀）
  targetColor?: string     // 收件人颜色
  summary?: string         // 消息摘要
  content?: string         // 完整内容
}
```

---

## 第五章：Worktree 隔离

### 5.1 为什么需要文件系统隔离

在多智能体并行工作时，一个根本性问题浮出水面：**多个智能体同时修改同一个文件怎么办？** 如果三个智能体同时编辑 `src/auth/validate.ts`，它们的修改会相互覆盖，产生不可预测的结果。

考虑一个典型场景：用户说"请重构 auth 模块、修复 session 过期 bug、并为所有改动写测试"。协调器将其分解为三个并行任务。重构智能体可能删除 `validate()` 函数并替换为 `validateSession()` 和 `validateToken()` 两个函数；与此同时，修 bug 的智能体正在 `validate()` 函数里添加空值检查——它们修改的是同一个文件的同一个函数。如果没有文件系统隔离，两个智能体的修改会直接冲突，后写入的会覆盖先写入的。

Git Worktree 是 Git 提供的一个功能，允许在同一个仓库中同时检出多个工作目录。每个 Worktree 有自己独立的文件系统副本和独立的分支，但共享同一个 `.git` 对象数据库。这意味着：

- **独立修改**：每个智能体在自己的目录中工作，互不干扰
- **共享历史**：所有 Worktree 共享同一个 Git 历史，提交可以互相访问
- **轻量创建**：不需要完整克隆，只需创建文件系统快照
- **自然合并**：当所有智能体完成工作后，可以通过 Git 合并机制整合各分支的修改

### 5.2 Worktree 会话数据结构

```typescript
// src/utils/worktree.ts:140-154
// Worktree 会话的完整状态定义
export type WorktreeSession = {
  originalCwd: string           // 进入 Worktree 前的原始工作目录
  worktreePath: string          // Worktree 的文件系统路径
  worktreeName: string          // Worktree 的名称（slug）
  worktreeBranch?: string       // Worktree 对应的 Git 分支名
  originalBranch?: string       // 进入前的原始分支
  originalHeadCommit?: string   // 进入前的 HEAD commit SHA
  sessionId: string             // 所属会话 ID
  tmuxSessionName?: string      // 关联的 tmux 会话（如有）
  hookBased?: boolean           // 是否由 Hook 创建（非 Git 原生）
  creationDurationMs?: number   // 创建耗时（恢复已有 Worktree 时为空）
  usedSparsePaths?: boolean     // 是否使用了 sparse-checkout
}
```

这个数据结构精心记录了 Worktree 的所有状态信息。特别注意 `originalHeadCommit` 字段——它是退出时安全检查的基准线：通过比较当前 HEAD 与创建时的 HEAD，系统能精确计算出智能体在 Worktree 中做了多少提交。

### 5.3 EnterWorktreeTool：创建隔离环境

```typescript
// src/tools/EnterWorktreeTool/EnterWorktreeTool.ts:77-118
async call(input) {
  // 第一步：防止嵌套 —— 不允许在 Worktree 内再创建 Worktree
  if (getCurrentWorktreeSession()) {
    throw new Error('Already in a worktree session')
  }

  // 第二步：回到主仓库根目录，确保 Worktree 创建从正确位置开始
  const mainRepoRoot = findCanonicalGitRoot(getCwd())
  if (mainRepoRoot && mainRepoRoot !== getCwd()) {
    process.chdir(mainRepoRoot)        // 物理切换进程工作目录
    setCwd(mainRepoRoot)               // 同步 Claude Code 内部 CWD 状态
  }

  // 第三步：生成或使用指定的 Worktree 名称
  const slug = input.name ?? getPlanSlug()

  // 第四步：创建 Worktree（核心操作）
  const worktreeSession = await createWorktreeForSession(getSessionId(), slug)

  // 第五步：切换到新的 Worktree 目录
  process.chdir(worktreeSession.worktreePath)    // 物理目录切换
  setCwd(worktreeSession.worktreePath)           // 状态同步
  setOriginalCwd(getCwd())                       // 更新原始 CWD 引用

  // 第六步：持久化 Worktree 状态（用于崩溃恢复）
  saveWorktreeState(worktreeSession)

  // 第七步：清除所有依赖 CWD 的缓存（关键！）
  clearSystemPromptSections()    // 系统提示需要重新计算（包含目录信息）
  clearMemoryFileCaches()        // CLAUDE.md 文件缓存依赖于当前目录
  getPlansDirectory.cache.clear?.()  // 计划目录也需要重新发现
  // ...
}
```

注意第七步的缓存清除——这是一个容易被忽略但极其重要的操作。系统提示、记忆文件、计划目录都依赖于当前工作目录。如果不清除这些缓存，智能体会继续使用旧目录的上下文信息，导致文件引用错误。

### 5.4 Worktree 创建的核心流程

`createWorktreeForSession` 函数实现了两条创建路径：

```typescript
// src/utils/worktree.ts:702-778
export async function createWorktreeForSession(
  sessionId: string,
  slug: string,
  tmuxSessionName?: string,
  options?: { prNumber?: number },
): Promise<WorktreeSession> {
  // 安全验证：防止路径遍历攻击
  validateWorktreeSlug(slug)

  const originalCwd = getCwd()

  // 路径一：Hook 模式 —— 允许用户自定义 VCS 集成
  if (hasWorktreeCreateHook()) {
    const hookResult = await executeWorktreeCreateHook(slug)
    currentWorktreeSession = {
      originalCwd,
      worktreePath: hookResult.worktreePath,
      worktreeName: slug,
      sessionId,
      tmuxSessionName,
      hookBased: true,               // 标记为 Hook 创建
    }
  } else {
    // 路径二：Git 原生模式 —— 默认路径
    const gitRoot = findGitRoot(getCwd())
    if (!gitRoot) {
      throw new Error(
        'Cannot create a worktree: not in a git repository and no ' +
        'WorktreeCreate hooks are configured.'
      )
    }

    const originalBranch = await getBranch()
    const { worktreePath, worktreeBranch, headCommit, existed } =
      await getOrCreateWorktree(gitRoot, slug, options)  // 实际创建或恢复

    if (!existed) {
      await performPostCreationSetup(gitRoot, worktreePath)  // 初始化设置
    }

    currentWorktreeSession = {
      originalCwd, worktreePath, worktreeName: slug,
      worktreeBranch, originalBranch, originalHeadCommit: headCommit,
      sessionId, tmuxSessionName,
    }
  }

  // 持久化到项目配置（用于崩溃恢复）
  saveCurrentProjectConfig(current => ({
    ...current,
    activeWorktreeSession: currentWorktreeSession ?? undefined,
  }))

  return currentWorktreeSession
}
```

两条路径的设计体现了"无需修改的可扩展性"——Git 是默认支持，但通过 Hook 机制，用户可以集成 Mercurial、Perforce 或任何其他版本控制系统，而无需修改 Claude Code 的核心代码。Hook 模式下创建的 Worktree 会被标记为 `hookBased: true`，退出时会调用 `executeWorktreeRemoveHook` 而非 `git worktree remove`。

注意 `getOrCreateWorktree` 的快速恢复路径——如果 Worktree 已经存在（比如上次会话创建但未清理），系统直接读取 `.git` 指针文件获取 HEAD SHA，跳过 fetch 和创建步骤，避免了约 15ms 的进程启动开销。

### 5.5 创建后的初始化设置

创建 Worktree 后，`performPostCreationSetup` 执行一系列关键配置：

```typescript
// src/utils/worktree.ts:510-589（简化）
async function performPostCreationSetup(
  repoRoot: string,
  worktreePath: string,
): Promise<void> {
  // 1. 复制 settings.local.json（可能包含密钥）
  const sourceSettingsLocal = join(repoRoot, localSettingsRelativePath)
  await copyFile(sourceSettingsLocal, destSettingsLocal)

  // 2. 配置 Git Hooks 路径（解决 .husky 相对路径问题）
  // Worktree 中的 .husky 脚本使用相对路径，需要指回主仓库
  if (hooksPath) {
    await execFileNoThrowWithCwd(
      gitExe(),
      ['config', 'core.hooksPath', hooksPath],
      { cwd: worktreePath },
    )
  }

  // 3. 符号链接大目录（如 node_modules）避免磁盘膨胀
  const dirsToSymlink = settings.worktree?.symlinkDirectories ?? []
  if (dirsToSymlink.length > 0) {
    await symlinkDirectories(repoRoot, worktreePath, dirsToSymlink)
  }

  // 4. 复制 .worktreeinclude 中指定的 gitignore 文件
  await copyWorktreeIncludeFiles(repoRoot, worktreePath)
}
```

第三步的符号链接特别重要——`node_modules` 目录可能有数百 MB，如果每个 Worktree 都完整复制一份，磁盘很快就会爆满。通过符号链接共享，多个 Worktree 共用同一份依赖。

### 5.6 ExitWorktreeTool：安全退出

退出 Worktree 时，系统提供两种模式——**保留**（keep）和**删除**（remove）：

```typescript
// src/tools/ExitWorktreeTool/ExitWorktreeTool.ts:174-223（validateInput 简化）
async validateInput(input) {
  // 门控检查：只能退出本会话创建的 Worktree
  const session = getCurrentWorktreeSession()
  if (!session) {
    return { result: false, message: 'No active EnterWorktree session' }
  }

  // 删除模式的安全检查：如果有未提交的更改或未推送的提交，
  // 必须显式确认 discard_changes: true
  if (input.action === 'remove' && !input.discard_changes) {
    const summary = await countWorktreeChanges(
      session.worktreePath, session.originalHeadCommit
    )

    // Fail-closed：如果无法确定状态，拒绝删除
    if (summary === null) {
      return { result: false,
        message: 'Could not verify worktree state. Refusing to remove.' }
    }

    // 有未保存工作时，要求显式确认
    if (summary.changedFiles > 0 || summary.commits > 0) {
      return { result: false,
        message: `Worktree has ${summary.changedFiles} uncommitted files ` +
                 `and ${summary.commits} commits. Confirm discard_changes: true.` }
    }
  }
  return { result: true }
}
```

`countWorktreeChanges` 函数的 "fail-closed" 设计值得注意——当无法确定 Worktree 状态时（比如 Git 锁文件存在），它返回 `null`，而调用方将 `null` 视为"不安全，拒绝操作"。这比假设"状态为空，可以安全删除"要安全得多。

退出后的清理操作通过 `restoreSessionToOriginalCwd` 恢复所有会话状态：

```typescript
// src/tools/ExitWorktreeTool/ExitWorktreeTool.ts:122-146
function restoreSessionToOriginalCwd(
  originalCwd: string,
  projectRootIsWorktree: boolean,
): void {
  setCwd(originalCwd)               // 恢复内部 CWD
  setOriginalCwd(originalCwd)       // 恢复原始 CWD 引用
  if (projectRootIsWorktree) {
    setProjectRoot(originalCwd)     // 恢复项目根目录
    updateHooksConfigSnapshot()     // 重新加载 Hooks 配置
  }
  saveWorktreeState(null)           // 清除持久化的 Worktree 状态
  clearSystemPromptSections()       // 清除系统提示缓存
  clearMemoryFileCaches()           // 清除记忆文件缓存
  getPlansDirectory.cache.clear?.() // 清除计划目录缓存
}
```

### 5.7 Slug 安全验证

Worktree 名称通过 `validateWorktreeSlug` 进行严格验证，防止路径遍历攻击：

```typescript
// src/utils/worktree.ts:48-49, 66-97（简化）
const VALID_WORKTREE_SLUG_SEGMENT = /^[a-zA-Z0-9._-]+$/
const MAX_WORKTREE_SLUG_LENGTH = 64

export function validateWorktreeSlug(slug: string): void {
  if (slug.length > MAX_WORKTREE_SLUG_LENGTH) {
    throw new Error(`Worktree slug exceeds ${MAX_WORKTREE_SLUG_LENGTH} chars`)
  }
  // 逐段验证（允许 / 作为分隔符）
  for (const segment of slug.split('/')) {
    if (!VALID_WORKTREE_SLUG_SEGMENT.test(segment)) {
      throw new Error(`Invalid segment "${segment}"`)
    }
  }
}
```

由于 Worktree 路径通过 `path.join('.claude/worktrees/', slug)` 构建，如果不验证 slug，攻击者可以通过 `../../../target` 这样的名称逃逸到任意目录。

---

## 第六章：协调器模式

### 6.1 什么是协调器模式

在分析了底层的 Worktree 隔离机制后，让我们上升一个抽象层，看看 Claude Code 如何在更高层面编排多个智能体的协作。

协调器模式（Coordinator Mode）是 Claude Code 的一种高级多智能体编排模式。在普通模式下，用户直接与一个 Claude 智能体对话，该智能体可以选择性地创建子智能体。而在协调器模式下，主智能体的角色发生根本转变——它不再直接执行任务，而是**专注于理解需求、分解任务、指挥工人智能体、综合结果**。这类似于软件工程中的"技术负责人"角色：自己不写代码，但负责理解需求、设计方案、分配任务、审查结果。

```typescript
// src/coordinator/coordinatorMode.ts:36-41
// 协调器模式的开关检测
export function isCoordinatorMode(): boolean {
  if (feature('COORDINATOR_MODE')) {        // Feature Flag 门控
    return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)  // 环境变量控制
  }
  return false
}
```

这里展示了典型的双层门控：首先通过 Feature Flag `COORDINATOR_MODE` 确保功能已上线，然后通过环境变量 `CLAUDE_CODE_COORDINATOR_MODE` 让用户自主启用。

### 6.2 协调器的专用系统提示

协调器模式的核心在于一套精心设计的系统提示，定义了协调器的行为规范：

```typescript
// src/coordinator/coordinatorMode.ts:111-369（简化关键部分）
export function getCoordinatorSystemPrompt(): string {
  return `You are Claude Code, an AI assistant that orchestrates
software engineering tasks across multiple workers.

## 1. Your Role
You are a **coordinator**. Your job is to:
- Help the user achieve their goal
- Direct workers to research, implement and verify code changes
- Synthesize results and communicate with the user
- Answer questions directly when possible

## 2. Your Tools
- **Agent** - Spawn a new worker
- **SendMessage** - Continue an existing worker
- **TaskStop** - Stop a running worker

## 4. Task Workflow
| Phase          | Who      | Purpose                        |
|----------------|----------|--------------------------------|
| Research       | Workers  | Investigate codebase           |
| Synthesis      | **You**  | Read findings, craft specs     |
| Implementation | Workers  | Make targeted changes, commit  |
| Verification   | Workers  | Test changes work              |

**Parallelism is your superpower.**`
}
```

协调器系统提示的设计有几个关键原则：

1. **明确角色分离**：协调器负责思考和综合，工人负责执行
2. **强调并行性**：鼓励同时启动多个独立的研究或实现任务
3. **禁止"懒惰委托"**：协调器必须自己理解工人的研究结果，不能说"根据你的发现去修复"
4. **Fail-fast 策略**：工人失败时，应继续同一个工人（它有错误上下文），而不是启动新的

### 6.3 工人工具上下文

协调器需要告诉 LLM 工人有哪些工具可用：

```typescript
// src/coordinator/coordinatorMode.ts:80-109
export function getCoordinatorUserContext(
  mcpClients: ReadonlyArray<{ name: string }>,
  scratchpadDir?: string,
): { [k: string]: string } {
  if (!isCoordinatorMode()) {
    return {}                            // 非协调器模式返回空
  }

  // 简化模式下只暴露基础工具
  const workerTools = isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)
    ? [BASH_TOOL_NAME, FILE_READ_TOOL_NAME, FILE_EDIT_TOOL_NAME]
        .sort().join(', ')
    : Array.from(ASYNC_AGENT_ALLOWED_TOOLS)     // 完整工具集
        .filter(name => !INTERNAL_WORKER_TOOLS.has(name))  // 排除内部工具
        .sort().join(', ')

  let content = `Workers have access to these tools: ${workerTools}`

  // MCP 工具也暴露给工人
  if (mcpClients.length > 0) {
    const serverNames = mcpClients.map(c => c.name).join(', ')
    content += `\nWorkers also have access to MCP tools from: ${serverNames}`
  }

  // Scratchpad 目录：工人之间共享的临时工作区
  if (scratchpadDir && isScratchpadGateEnabled()) {
    content += `\nScratchpad directory: ${scratchpadDir}
Workers can read and write here without permission prompts.`
  }

  return { workerToolsContext: content }
}
```

注意 `INTERNAL_WORKER_TOOLS` 的过滤——`TeamCreate`、`TeamDelete`、`SendMessage`、`SyntheticOutput` 这些工具只有协调器能用，不暴露给工人，防止工人自行创建团队或发送消息造成混乱。

### 6.4 会话恢复时的模式匹配

恢复已保存的会话时，协调器模式需要保持一致：

```typescript
// src/coordinator/coordinatorMode.ts:49-78
export function matchSessionMode(
  sessionMode: 'coordinator' | 'normal' | undefined,
): string | undefined {
  if (!sessionMode) return undefined         // 旧会话无模式记录

  const currentIsCoordinator = isCoordinatorMode()
  const sessionIsCoordinator = sessionMode === 'coordinator'

  if (currentIsCoordinator === sessionIsCoordinator) {
    return undefined                         // 模式匹配，无需切换
  }

  // 动态翻转环境变量以匹配会话模式
  if (sessionIsCoordinator) {
    process.env.CLAUDE_CODE_COORDINATOR_MODE = '1'
  } else {
    delete process.env.CLAUDE_CODE_COORDINATOR_MODE
  }

  return sessionIsCoordinator
    ? 'Entered coordinator mode to match resumed session.'
    : 'Exited coordinator mode to match resumed session.'
}
```

这确保了恢复一个协调器会话时，即使当前环境没有设置协调器模式，系统也会自动切入正确模式。`isCoordinatorMode()` 每次调用都直接读取环境变量（无缓存），因此通过修改 `process.env` 就能立即生效。

### 6.5 协调器模式的价值与局限

协调器模式最大的价值在于**并行化**——当任务可以分解为多个独立子任务时，协调器能同时启动多个工人并行处理，极大缩短总耗时。比如重构一个模块涉及修改 10 个文件，协调器可以让 3 个工人各负责一组文件，同时进行。

但协调器模式也有局限：对于简单的单步任务（比如"修复这个 typo"），协调器的编排开销反而拖慢速度。系统提示中明确指出"Answer questions directly when possible — don't delegate work that you can handle without tools"，避免过度编排。此外，协调器的综合能力高度依赖 LLM 的理解力——如果协调器误解了工人的研究结果，后续的实现指令也会出错。

---

## 第七章：远程执行

### 7.1 远程执行架构概览

前面两章分析的 Worktree 隔离和协调器模式都运行在用户的本地机器上。但 Claude Code 的多智能体野心远不止于此——**远程执行系统**允许智能体在 Anthropic 托管的云端沙箱中运行，突破本地机器的算力和安全边界。

本地客户端通过 WebSocket 和 HTTP API 与远程会话通信。这个系统由三个核心层组成：

```
┌─────────────────────────────────────────────┐
│  本地 CLI (Claude Code)                      │
│  ┌──────────────────┐  ┌──────────────────┐ │
│  │RemoteSessionManager│  │PermissionBridge │ │
│  │  (会话管理)        │  │  (权限桥接)     │ │
│  └────────┬─────────┘  └────────┬─────────┘ │
│           │                     │            │
│  ┌────────┴─────────────────────┴─────────┐ │
│  │      SessionsWebSocket                  │ │
│  │  (WebSocket 通信层)                     │ │
│  └────────────────┬────────────────────────┘ │
└───────────────────┼──────────────────────────┘
                    │ wss://api.anthropic.com
┌───────────────────┼──────────────────────────┐
│  Anthropic 云端 (CCR - Cloud Code Runner)   │
│  ┌────────────────┴────────────────────────┐ │
│  │     远程 Claude 智能体                   │ │
│  │     (在沙箱容器中运行)                   │ │
│  └─────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

### 7.2 RemoteSessionManager：会话管理中枢

```typescript
// src/remote/RemoteSessionManager.ts:50-62
// 远程会话配置
export type RemoteSessionConfig = {
  sessionId: string                  // 会话唯一标识
  getAccessToken: () => string       // OAuth 令牌获取函数
  orgUuid: string                    // 组织 UUID
  hasInitialPrompt?: boolean         // 是否有初始提示正在处理
  viewerOnly?: boolean               // 纯观察模式（不发送中断）
}
```

```typescript
// src/remote/RemoteSessionManager.ts:95-141（简化）
export class RemoteSessionManager {
  private websocket: SessionsWebSocket | null = null
  // 待处理的权限请求映射
  private pendingPermissionRequests:
    Map<string, SDKControlPermissionRequest> = new Map()

  connect(): void {
    // 创建 WebSocket 连接，注册消息处理器
    this.websocket = new SessionsWebSocket(
      this.config.sessionId,
      this.config.orgUuid,
      this.config.getAccessToken,
      {
        onMessage: message => this.handleMessage(message),
        onConnected: () => this.callbacks.onConnected?.(),
        onClose: () => this.callbacks.onDisconnected?.(),
        onReconnecting: () => this.callbacks.onReconnecting?.(),
      },
    )
    void this.websocket.connect()
  }
}
```

RemoteSessionManager 的核心职责是**消息路由**——它接收来自远程会话的消息，根据消息类型（SDK 消息、控制请求、权限请求）分发给不同的处理器。`pendingPermissionRequests` 映射表跟踪所有等待用户响应的权限请求，确保每个请求最终都能得到回复或被取消，不会无限期悬挂。

### 7.3 WebSocket 通信协议

```typescript
// src/remote/SessionsWebSocket.ts:17-36
const RECONNECT_DELAY_MS = 2000             // 重连延迟 2 秒
const MAX_RECONNECT_ATTEMPTS = 5            // 最多重连 5 次
const PING_INTERVAL_MS = 30000              // 心跳间隔 30 秒
const MAX_SESSION_NOT_FOUND_RETRIES = 3     // 会话未找到最多重试 3 次

// 永久性关闭码（不再重连）
const PERMANENT_CLOSE_CODES = new Set([
  4003, // unauthorized —— 认证失败，无需重试
])
```

WebSocket 连接遵循以下协议：

1. **连接**：`wss://api.anthropic.com/v1/sessions/ws/{sessionId}/subscribe`
2. **认证**：发送 `{ type: 'auth', credential: { type: 'oauth', token: '...' } }`
3. **接收消息流**：SDK 消息、控制请求（权限提示）、取消请求

重连策略区分**暂时性**和**永久性**断开——网络波动时自动重连最多 5 次，而认证失败（4003）或会话过期则立即放弃。特别地，会话未找到（4001）有 3 次额外重试机会，因为上下文压缩期间服务器可能暂时认为会话已失效。

### 7.4 权限桥接

远程执行中最复杂的部分是权限处理——远程智能体需要执行工具，但权限决策必须由本地用户做出：

```typescript
// src/remote/remotePermissionBridge.ts:12-46（简化）
// 为远程权限请求创建合成的 AssistantMessage
export function createSyntheticAssistantMessage(
  request: SDKControlPermissionRequest,
  requestId: string,
): AssistantMessage {
  return {
    type: 'assistant',
    uuid: randomUUID(),
    message: {
      id: `remote-${requestId}`,     // remote- 前缀标识来源
      type: 'message',
      role: 'assistant',
      content: [{
        type: 'tool_use',
        id: request.tool_use_id,      // 远程工具调用 ID
        name: request.tool_name,      // 工具名称
        input: request.input,         // 工具参数
      }],
      // ...
    },
  }
}
```

```typescript
// src/remote/remotePermissionBridge.ts:53-60
// 为未知工具创建最小化存根
export function createToolStub(toolName: string): Tool {
  return {
    name: toolName,
    inputSchema: {} as Tool['inputSchema'],
    isEnabled: () => true,
    userFacingName: () => toolName,
    // 渲染器：将工具输入显示为键值对
    renderToolUseMessage: (input: Record<string, unknown>) => { /* ... */ }
  }
}
```

`createToolStub` 解决了一个有趣的问题：远程 CCR 可能安装了本地没有的 MCP 工具。当这些工具请求权限时，本地需要一个"占位符"工具定义来渲染权限提示 UI。

### 7.5 Teleport：跨机器工作流

Teleport 系统实现了将本地代码库"传送"到远程 CCR 环境的能力：

```typescript
// src/utils/teleport/gitBundle.ts:1-9（流程说明）
// Git Bundle 创建 + 上传流程：
//   1. git stash create → update-ref refs/seed/stash（使未提交更改可达）
//   2. git bundle create --all（打包所有引用 + 对象）
//   3. 上传到 /v1/files API
//   4. 清理 refs/seed/stash（不污染用户仓库）
//   5. 调用方在 SessionContext 中设置 seed_bundle_file_id
```

```typescript
// src/utils/teleport/api.ts:84-125（简化关键类型）
// 会话上下文：定义远程环境需要的一切
export type SessionContext = {
  sources: SessionContextSource[]       // 代码来源（Git 仓库或知识库）
  cwd: string                           // 远程工作目录
  outcomes: Outcome[] | null            // 期望的输出（如 Git 分支推送）
  custom_system_prompt: string | null   // 自定义系统提示
  model: string | null                  // 模型选择
  seed_bundle_file_id?: string          // Git Bundle 文件 ID
  github_pr?: { owner: string; repo: string; number: number }  // PR 关联
}
```

Teleport 的三层降级策略体现了优雅降级思想：
1. **`--all` 完整打包**：包含所有分支、标签、完整历史
2. **`HEAD` 降级**：只打包当前分支历史（丢弃旁支）
3. **Squashed-root 降级**：单个无父提交的快照（无历史，只有文件树）

这种递进降级确保即使仓库很大（超过 100MB Bundle 限制），也能传送一个可用的代码快照。

### 7.6 环境选择

```typescript
// src/utils/teleport/environments.ts:9-18
// 远程环境类型
export type EnvironmentKind = 'anthropic_cloud' | 'byoc' | 'bridge'

export type EnvironmentResource = {
  kind: EnvironmentKind       // 环境类型
  environment_id: string      // 环境唯一 ID
  name: string                // 环境名称
  created_at: string          // 创建时间
  state: EnvironmentState     // 环境状态
}
```

三种环境类型覆盖了不同的部署场景：
- **anthropic_cloud**：Anthropic 托管的云端沙箱，提供标准化的执行环境
- **byoc** (Bring Your Own Cloud)：用户自己的云基础设施，满足数据合规和定制化需求
- **bridge**：本地网络直连模式，用于同一网络内的机器间协作

这种多环境设计反映了企业用户的多样化需求——有些团队希望使用 Anthropic 提供的便捷云环境，有些有严格的数据主权要求必须在自己的基础设施上运行，还有些需要在内网中直接连接同事的机器协作。通过统一的 API 接口（`/v1/environment_providers`），这三种截然不同的部署模式对上层应用代码完全透明。

---

## 第八章：多智能体架构图

在前面三章中，我们分别深入分析了 Worktree 文件系统隔离、协调器编排模式和远程执行系统。现在让我们通过三张架构图将这些概念整合在一起，形成一个完整的多智能体系统全景视图。

### 8.1 智能体层次结构

```
                    ┌─────────────────────┐
                    │     用户 (User)      │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │    主智能体 / 协调器   │
                    │   (Main / Coordinator)│
                    │                      │
                    │  • QueryEngine       │
                    │  • 完整工具集         │
                    │  • 权限交互           │
                    └───┬──────┬──────┬────┘
                        │      │      │
           ┌────────────┘      │      └────────────┐
           │                   │                    │
┌──────────▼─────────┐ ┌──────▼──────────┐ ┌───────▼─────────┐
│  子智能体 (Agent)   │ │ 团队队友 (Teammate)│ │ 远程智能体 (Remote)│
│                    │ │                  │ │                  │
│ • 隔离上下文       │ │ • 团队共享上下文  │ │ • CCR 沙箱容器   │
│ • 受限工具集       │ │ • 文件邮箱通信    │ │ • WebSocket 通信  │
│ • 可选 Worktree    │ │ • 持久化到磁盘    │ │ • 权限桥接        │
│ • 前台/后台模式    │ │ • 彩色 UI 标识    │ │ • Git Bundle 传送 │
└────────┬───────────┘ └────────┬─────────┘ └──────────────────┘
         │                      │
  ┌──────▼──────┐        ┌──────▼──────┐
  │ 探索智能体   │        │ 工人智能体   │
  │ (Explore)    │        │ (Worker)     │
  │ • 只读工具   │        │ • 完整工具   │
  │ • 快速搜索   │        │ • 可写文件   │
  └─────────────┘        └─────────────┘
```

### 8.2 通信流程图

```
┌──────────┐                                      ┌──────────┐
│ 协调器    │                                      │  工人 A   │
└────┬─────┘                                      └────┬─────┘
     │                                                  │
     │  AgentTool({ prompt: "研究 auth 模块" })          │
     │ ─────────────────────────────────────────────────>│
     │                                                  │
     │  <task-notification> completed </task-notification>│
     │ <─────────────────────────────────────────────────│
     │                                                  │
     │  [协调器综合研究结果，制定修复方案]                  │
     │                                                  │
     │  SendMessage({ to: "A", message: "修复 validate.ts:42" })
     │ ─────────────────────────────────────────────────>│
     │                                                  │
     │                                                  │ ┌──────────┐
     │  AgentTool({ prompt: "验证修复" })                 │ │  工人 B   │
     │ ──────────────────────────────────────────────────┼>│          │
     │                                                  │ └────┬─────┘
     │  <task-notification> completed (hash: abc123)     │      │
     │ <─────────────────────────────────────────────────│      │
     │                                                  │      │
     │  <task-notification> tests pass ✓                 │      │
     │ <───────────────────────────────────────────────────────│
     │                                                  │      │
     ▼                                                  ▼      ▼
```

### 8.3 Worktree 隔离示意图

```
┌─────────────────────────────────────────────────────────────┐
│  主仓库 (.git)                                              │
│  ┌─────────────┐   共享 Git 对象数据库                       │
│  │ objects/     │◄──────────────────────────┐                │
│  │ refs/        │                           │                │
│  │ config       │                           │                │
│  └─────────────┘                           │                │
│                                             │                │
│  ┌──────────────────┐  ┌──────────────────┐ │                │
│  │ 主工作目录        │  │ .claude/worktrees/│ │                │
│  │ (originalCwd)    │  │                  │ │                │
│  │                  │  │  ┌─────────────┐ │ │                │
│  │  src/            │  │  │ agent-abc/  │─┘ │                │
│  │  package.json    │  │  │  src/       │   │                │
│  │  node_modules/ ──┼──┼──│  node_modules/→ (symlink)       │
│  │                  │  │  │  .git → ../..│   │                │
│  └──────────────────┘  │  └─────────────┘   │                │
│                        │                    │                │
│                        │  ┌─────────────┐   │                │
│                        │  │ agent-xyz/  │───┘                │
│                        │  │  src/       │                    │
│                        │  │  node_modules/→ (symlink)        │
│                        │  └─────────────┘                    │
│                        └──────────────────┘                  │
└─────────────────────────────────────────────────────────────┘

每个 Worktree：
 ✓ 独立文件系统副本（可自由修改）
 ✓ 独立 Git 分支（互不冲突）
 ✓ 共享 .git 对象库（节省空间）
 ✓ 符号链接大目录（避免磁盘膨胀）
 ✗ 不能嵌套创建（防止复杂度爆炸）
```

---

## 设计哲学分析

本文档是"**隔离与遏制**"（Isolation & Containment）这一核心设计哲学的集中展示。在多智能体系统中，"隔离"不是一个单一的技术决策，而是在多个层面、多个维度上的系统性实践。以下分析将从七个维度审视这一哲学如何贯穿整个多智能体架构。

### 文件系统隔离：Worktree 的核心贡献

Git Worktree 机制赋予了每个智能体一份**物理独立的文件系统副本**。这不是简单的"不同目录"——每个 Worktree 有独立的工作树、独立的 Git 索引、独立的分支，但共享同一个对象数据库。这种设计在隔离和效率之间找到了精确的平衡点：

- 智能体 A 修改 `src/auth.ts` 不会影响智能体 B 正在编辑的同一文件
- 两个智能体各自提交后，Git 的合并机制自然处理整合
- 共享对象库意味着创建 Worktree 只需秒级时间，而非克隆整个仓库的分钟级

`performPostCreationSetup` 中的符号链接策略（`symlinkDirectories`）进一步优化了这个平衡——`node_modules` 这样的只读依赖通过符号链接共享，避免了每个 Worktree 数百 MB 的磁盘占用。

### 上下文隔离：createSubagentContext 的边界

在第一部分中分析的 `createSubagentContext()` 实现了**消息级别和权限级别的隔离**。子智能体看不到父智能体的私有消息或工具拒绝记录。这种隔离防止了一个智能体的失败经验"污染"另一个智能体的决策——如果智能体 A 的某个工具调用被拒绝，智能体 B 不应该因此回避使用同一工具。

### 渐进信任：团队规模限制

团队系统中的层级限制（Free: 1 个队友, Max/Enterprise: 3 个）是**渐进信任模型**在多智能体领域的直接体现。更多的智能体意味着更大的自主操作范围、更多的并行文件修改、更高的资源消耗。系统通过订阅层级来门控这种能力扩展：

- **免费用户**：1 个队友，适合简单的"我研究、你实现"模式
- **付费用户**：3 个队友，支持完整的"研究-实现-验证"三阶段并行流水线

这种设计确保了用户的信任级别（通过付费行为表达）与系统赋予的自主权之间的对称性。

### 消息传递 vs 共享状态：通信设计选择

SendMessageTool 采用**消息传递**（message passing）而非**共享状态**（shared state）作为智能体间通信机制。这个选择本身就是对"隔离"原则的深层体现：

- 消息传递天然支持异步：发送方不需要等待接收方处理
- 消息是不可变的：一旦发送，不会被另一个智能体修改
- 通信通道可审计：所有消息都可以被记录和回放
- 命名空间隔离：每个智能体有自己的消息队列，不会被其他智能体的消息淹没

文件邮箱（UDS 消息通道）看似原始，但它的简单性恰恰是其可靠性的来源——没有复杂的锁机制、没有竞态条件、没有分布式一致性问题。

### 远程隔离：机器边界作为信任边界

远程执行系统将隔离推延到了**机器边界**。CCR（Cloud Code Runner）在独立的沙箱容器中运行智能体，物理隔离了：

- **文件系统**：远程容器有自己的文件系统，通过 Git Bundle 初始化
- **网络**：容器有受限的网络访问
- **权限**：远程工具调用通过 WebSocket 路由回本地，由用户在本地做出权限决策

权限桥接机制（`remotePermissionBridge.ts`）是这种跨边界隔离中最精妙的部分。远程智能体执行到需要权限的工具调用时，控制流跨越网络回到本地用户终端，用户看到权限提示，做出决策，决策再跨网络传回远程容器。整个过程中，权限决策始终在用户控制之下——这是"人在回路"原则在分布式环境中的延伸。

### 协调器的分离关注点

协调器模式通过角色分离实现了一种更高层面的隔离——**关注点隔离**。协调器不执行具体任务，工人不做高层决策。这种分离有几个重要效果：

- **知识隔离**：工人看不到用户对话，只看到协调器精心构建的任务描述，避免了上下文泄露
- **失败隔离**：一个工人的失败不影响其他工人，协调器可以选择重试、换方案或向用户汇报
- **资源隔离**：每个工人有独立的上下文窗口，不会因为一个长任务占满所有可用 token

`INTERNAL_WORKER_TOOLS` 集合的过滤确保了工人不能越权——它们不能创建团队、发送消息或生成合成输出，这些能力只属于协调器层面。

### 安全第一的多智能体设计

整个多智能体系统是 Claude Code "安全第一"设计哲学最雄心勃勃的表达。让多个 AI 智能体自主并行工作，同时保持安全性，需要在每个层面都有防护：

- **Worktree slug 验证**防止路径遍历（`validateWorktreeSlug`）
- **Fail-closed 语义**确保无法确定状态时拒绝危险操作（`countWorktreeChanges` 返回 null 时拒绝删除）
- **权限不继承**——子智能体必须独立获取权限，不能继承父智能体的权限授予
- **显式确认删除**——有未保存工作的 Worktree 删除需要 `discard_changes: true` 双重确认
- **嵌套防护**——不允许在 Worktree 内创建 Worktree，防止复杂度爆炸

这些机制共同构建了一个多层防御体系：即使某一层的隔离被突破，其他层仍然能限制影响范围。这正是"遏制"（Containment）一词的核心含义——不仅隔离，更要在隔离失败时限制爆炸半径。

---

## 关键要点总结

### Part 1 要点

1. **AgentTool 是多智能体系统的入口**：通过统一的工具接口，支持同步子智能体、异步后台智能体、团队队友和远程执行四种模式
2. **createSubagentContext() 实现"默认隔离，显式共享"**：子智能体默认不能修改父状态、不能弹出对话框，只有通过显式选项才能打开共享通道
3. **任务系统提供统一的生命周期管理**：七种任务类型共享五状态模型（pending → running → completed/failed/killed），通过单字母前缀的 ID 隔离命名空间
4. **团队管理基于 TeamFile 持久化**：领导者创建团队、生成队友、分配颜色，队友数量受订阅层级限制
5. **通信系统采用文件邮箱**：看似原始但极为可靠，支持纯文本、广播和结构化协议消息

### Part 2 要点

6. **Worktree 提供文件系统级隔离**：每个智能体获得独立的文件副本和 Git 分支，共享对象库节省空间，符号链接避免磁盘膨胀
7. **Worktree 退出采用 fail-closed 安全策略**：无法确认状态时拒绝删除，有未保存工作时要求显式确认
8. **协调器模式将主智能体变为纯编排者**：不执行任务，专注于需求理解、任务分解、结果综合，工人通过 `<task-notification>` 汇报
9. **远程执行通过 WebSocket + HTTP 实现跨机器智能体**：权限决策通过权限桥接回传到本地用户，Git Bundle 实现代码"传送"
10. **隔离在五个层面实施**：文件系统（Worktree）、消息/权限（subagentContext）、通信（消息传递 vs 共享状态）、机器（远程沙箱）、角色（协调器/工人分离）

---

## 下一篇预览

**Doc 11：MCP 与外部协议**将深入分析 Claude Code 如何通过标准协议连接外部世界。Model Context Protocol (MCP) 是实现"无需修改的可扩展性"设计哲学的极致表达——通过统一的协议接口，Claude Code 可以连接无限多的外部工具、数据源和服务，而无需修改一行核心代码。我们将分析 MCP 客户端的连接管理、工具发现与注册机制、LSP 集成用于代码理解、IDE 桥接实现编辑器内嵌使用，以及这些协议抽象如何在保持安全性的前提下实现真正的开放式可扩展。
