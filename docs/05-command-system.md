# Doc 5: 命令系统

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）、Doc 2（构建系统与运行时）、Doc 3（入口点与初始化流程）、Doc 4（终端 UI 系统）

在前四篇文档中，我们从语言基础到启动流程再到 UI 渲染，建立了对 Claude Code 运行时行为的完整理解。本文档将深入**命令系统**——用户通过 `/command` 斜杠命令与 Claude Code 交互的核心机制。命令系统是连接用户意图与系统行为的桥梁：当用户输入 `/commit` 创建提交、输入 `/compact` 压缩上下文、输入 `/config` 打开设置面板时，背后是一套统一的注册、发现、分发和执行管道在运作。

---

## 第一章：命令注册中心 src/commands.ts

### 1.1 Command 类型定义

命令系统的类型基础定义在 `src/types/command.ts`。理解命令系统的第一步是理解 `Command` 类型：

```typescript
// src/types/command.ts:205-206
// Command 是 CommandBase 与三种命令变体的联合类型
export type Command = CommandBase &
  (PromptCommand | LocalCommand | LocalJSXCommand)
```

这个定义告诉我们：**每个命令都由通用元数据（CommandBase）加上特定执行模式（三选一）组成**。三种命令类型分别是：

| 类型 | 描述 | 执行方式 | 典型示例 |
|------|------|---------|---------|
| `prompt` | 技能型命令 | 展开为文本发送给 Claude 模型 | `/commit`、`/review`、`/init` |
| `local` | 本地文本命令 | 在 REPL 进程内同步执行，返回文本 | `/compact`、`/vim`、`/cost` |
| `local-jsx` | 本地 UI 命令 | 在 REPL 中渲染 React/Ink 交互界面 | `/config`、`/doctor`、`/memory` |

### 1.2 CommandBase：通用元数据

每个命令都携带的通用字段定义了命令的身份和行为约束：

```typescript
// src/types/command.ts:175-203
export type CommandBase = {
  availability?: CommandAvailability[]  // 认证/提供商可用性限制
  description: string                   // 用户可见的命令描述
  hasUserSpecifiedDescription?: boolean // 描述是否来自用户自定义
  isEnabled?: () => boolean            // 条件启用检查（Feature Flag、环境变量等）
  isHidden?: boolean                   // 是否隐藏在自动补全和帮助中
  name: string                         // 命令主标识符
  aliases?: string[]                   // 别名列表（如 /settings → /config）
  isMcp?: boolean                      // 是否是 MCP 提供的命令
  argumentHint?: string                // 参数提示文本（如 "[model]"）
  whenToUse?: string                   // 详细的使用场景描述（来自 Skill 规范）
  version?: string                     // 命令/技能版本号
  disableModelInvocation?: boolean     // 是否禁止模型通过 SkillTool 调用
  userInvocable?: boolean              // 用户是否可以通过 /skill-name 调用
  loadedFrom?:                         // 命令来源标记
    | 'commands_DEPRECATED'
    | 'skills' | 'plugin' | 'managed' | 'bundled' | 'mcp'
  kind?: 'workflow'                    // 工作流命令标记（影响自动补全徽章）
  immediate?: boolean                  // 立即执行，不等待队列（如 /mcp、/exit）
  isSensitive?: boolean                // 参数是否需要从会话历史中脱敏
  userFacingName?: () => string        // 自定义显示名（如插件去前缀）
}
```

两个解析辅助函数提供了安全的默认值访问：

```typescript
// src/types/command.ts:208-216
// 获取命令的用户可见名称，优先使用自定义名称，回退到 name 字段
export function getCommandName(cmd: CommandBase): string {
  return cmd.userFacingName?.() ?? cmd.name
}

// 检查命令是否启用，默认为 true（没有 isEnabled 的命令始终可用）
export function isCommandEnabled(cmd: CommandBase): boolean {
  return cmd.isEnabled?.() ?? true
}
```

### 1.3 三种命令变体的类型结构

**PromptCommand** 是最复杂的变体——它定义了一个可以被 Claude 模型执行的技能：

```typescript
// src/types/command.ts:25-57
export type PromptCommand = {
  type: 'prompt'
  progressMessage: string       // 执行时的加载提示文本
  contentLength: number         // 内容长度（用于 token 估算）
  argNames?: string[]           // 参数名称列表
  allowedTools?: string[]       // 限制模型可用的工具列表
  model?: string                // 覆盖默认模型
  source: SettingSource | 'builtin' | 'mcp' | 'plugin' | 'bundled'
  context?: 'inline' | 'fork'  // 执行上下文：内联（默认）或 fork（子智能体）
  agent?: string                // fork 时使用的智能体类型
  effort?: EffortValue          // 努力程度
  hooks?: HooksSettings         // 调用时注册的 Hook
  skillRoot?: string            // 技能资源根目录（用于设置环境变量）
  paths?: string[]              // Glob 模式：仅在模型触及匹配文件后可见
  // 核心方法：生成发送给模型的 prompt 内容
  getPromptForCommand(
    args: string,
    context: ToolUseContext,
  ): Promise<ContentBlockParam[]>
}
```

**LocalCommand** 是最简单的变体——同步文本输出：

```typescript
// src/types/command.ts:74-78
type LocalCommand = {
  type: 'local'
  supportsNonInteractive: boolean  // 是否支持非交互模式（-p 标志）
  load: () => Promise<LocalCommandModule>  // 延迟加载实现
}
```

**LocalJSXCommand** 是 React UI 命令——渲染 Ink 组件：

```typescript
// src/types/command.ts:144-152
type LocalJSXCommand = {
  type: 'local-jsx'
  // 延迟加载命令实现
  // 返回一个带有 call() 函数的模块
  // 这将加载繁重依赖推迟到命令被实际调用时
  load: () => Promise<LocalJSXCommandModule>
}
```

注意 `local` 和 `local-jsx` 都使用 `load()` 延迟加载——这是 Doc 3 中讲到的启动性能优化策略的具体体现。

### 1.4 命令注册与加载

所有内置命令在 `src/commands.ts` 中通过 `COMMANDS()` 函数注册。这个函数使用 `lodash-es/memoize` 包装，确保命令列表只构建一次：

```typescript
// src/commands.ts:258-346（简化展示关键结构）
const COMMANDS = memoize((): Command[] => [
  addDir,      // 添加目录到上下文
  advisor,     // AI 顾问
  agents,      // 智能体管理
  branch,      // Git 分支管理
  clear,       // 清除会话历史
  compact,     // 压缩上下文
  config,      // 设置面板
  commit,      // 创建 Git 提交（INTERNAL_ONLY）
  cost,        // 显示会话费用
  diff,        // 查看未提交更改
  doctor,      // 诊断安装
  help,        // 帮助信息
  init,        // 初始化 CLAUDE.md
  memory,      // 编辑记忆文件
  mcp,         // 管理 MCP 服务器
  permissions, // 管理权限规则
  plan,        // 计划模式
  plugin,      // 插件管理
  resume,      // 恢复之前的会话
  review,      // 代码审查
  tasks,       // 后台任务列表
  vim,         // 切换 Vim 模式
  // ... 共 80+ 个命令
  // Feature-Gated 命令通过展开运算符条件性加入
  ...(proactive ? [proactive] : []),
  ...(bridge ? [bridge] : []),
  ...(voiceCommand ? [voiceCommand] : []),
  // 内部专用命令仅在 ant 模式下加入
  ...(process.env.USER_TYPE === 'ant' && !process.env.IS_DEMO
    ? INTERNAL_ONLY_COMMANDS : []),
])
```

`getCommands()` 是面向消费者的公开 API，它整合了所有命令来源：

```typescript
// src/commands.ts:476-517
export async function getCommands(cwd: string): Promise<Command[]> {
  // loadAllCommands 是 memoized 的，只执行一次磁盘 I/O
  const allCommands = await loadAllCommands(cwd)
  // 获取文件操作过程中发现的动态技能
  const dynamicSkills = getDynamicSkills()
  // 过滤：可用性检查 + 启用状态检查（每次调用都重新执行，因为认证状态可能改变）
  const baseCommands = allCommands.filter(
    _ => meetsAvailabilityRequirement(_) && isCommandEnabled(_),
  )
  // 去重并插入动态技能
  // ...
  return baseCommands
}
```

命令来源的优先级顺序：

```
bundledSkills → builtinPluginSkills → skillDirCommands → workflowCommands → pluginCommands → pluginSkills → COMMANDS()
```

前面的来源会覆盖后面同名的命令，确保用户自定义的技能优先于内置命令。

### 1.5 可用性检查：meetsAvailabilityRequirement

命令的 `availability` 字段控制了谁能看到这个命令。这个检查独立于 `isEnabled()` 运行——前者是认证/提供商级别的静态限制，后者是功能标志级别的动态开关：

```typescript
// src/commands.ts:417-443
export function meetsAvailabilityRequirement(cmd: Command): boolean {
  if (!cmd.availability) return true   // 无限制 = 所有人可用
  for (const a of cmd.availability) {
    switch (a) {
      case 'claude-ai':
        if (isClaudeAISubscriber()) return true  // claude.ai 订阅用户
        break
      case 'console':
        // Console API key 用户 = 直接 1P API 客户
        // 排除 3P（Bedrock/Vertex/Foundry）和自定义 base URL 用户
        if (!isClaudeAISubscriber() && !isUsing3PServices()
            && isFirstPartyAnthropicBaseUrl())
          return true
        break
      default: {
        const _exhaustive: never = a  // 穷举检查：编译时捕获漏掉的分支
        void _exhaustive
        break
      }
    }
  }
  return false
}
```

---

## 第二章：命令生命周期

### 2.1 从用户输入到命令执行

当用户在 REPL 中输入 `/compact summary please` 并按下回车键，一条完整的执行管道启动。下面用 ASCII 流程图展示这个过程：

```
┌──────────────────────────────────────────────────────────────────┐
│  用户在 PromptInput 中输入 "/compact summary please" 并按 Enter  │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  REPL.tsx handlePromptSubmit()                                   │
│  检查是否有 immediate 标记 → 如果有，跳过队列直接执行              │
│  否则加入 inputQueue，等待当前查询完成                             │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  executeUserInput()                                              │
│  遍历队列中的输入，对每个调用 processUserInput()                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  processUserInput()  (src/utils/processUserInput/processUserInput.ts)
│  检测输入是否以 "/" 开头 → 是 → processSlashCommand()             │
│                           → 否 → 普通文本处理                     │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  processSlashCommand()  (processSlashCommand.tsx:309)             │
│  1. parseSlashCommand() 解析输入                                  │
│  2. hasCommand() 查找命令                                         │
│  3. getMessagesForSlashCommand() 执行命令                         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         ┌─────────┐  ┌──────────┐  ┌─────────────┐
         │ prompt   │  │  local   │  │  local-jsx  │
         │ 展开为   │  │ 调用     │  │ 渲染 React  │
         │ prompt   │  │ call()   │  │ 组件，等待  │
         │ 内容块   │  │ 返回文本 │  │ onDone 回调 │
         └────┬────┘  └────┬─────┘  └──────┬──────┘
              │            │               │
              └────────────┼───────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  返回 { messages, shouldQuery, allowedTools, model }             │
│  messages: SystemLocalCommandMessage 或 UserMessage              │
│  shouldQuery: 是否需要将消息发送给模型                             │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                  ┌──────────┴──────────┐
                  ▼                     ▼
           shouldQuery=true      shouldQuery=false
           调用 onQuery()         直接显示结果
           进入查询循环            命令完成
```

### 2.2 斜杠命令解析

输入解析由 `parseSlashCommand()` 完成，它是一个纯函数：

```typescript
// src/utils/slashCommandParsing.ts:25-60
export function parseSlashCommand(input: string): ParsedSlashCommand | null {
  const trimmedInput = input.trim()
  // 必须以 "/" 开头
  if (!trimmedInput.startsWith('/')) { return null }
  // 去掉 "/" 前缀，按空格分割
  const withoutSlash = trimmedInput.slice(1)
  const words = withoutSlash.split(' ')
  if (!words[0]) { return null }

  let commandName = words[0]   // 第一个词是命令名
  let isMcp = false
  let argsStartIndex = 1

  // 特殊处理 MCP 命令（第二个词是 "(MCP)"）
  if (words.length > 1 && words[1] === '(MCP)') {
    commandName = commandName + ' (MCP)'
    isMcp = true
    argsStartIndex = 2
  }
  // 剩余部分是参数
  const args = words.slice(argsStartIndex).join(' ')
  return { commandName, args, isMcp }
}
```

解析结果 `ParsedSlashCommand` 包含三个字段：`commandName`（命令名称）、`args`（参数字符串）、`isMcp`（是否是 MCP 命令）。

### 2.3 命令查找

解析后，通过 `findCommand()` 在已注册命令列表中查找：

```typescript
// src/commands.ts:688-698
export function findCommand(
  commandName: string,
  commands: Command[],
): Command | undefined {
  return commands.find(
    _ =>
      _.name === commandName ||           // 精确名称匹配
      getCommandName(_) === commandName || // 自定义显示名匹配
      _.aliases?.includes(commandName),    // 别名匹配
  )
}
```

如果命令不存在，系统会进一步区分"看起来像命令名的输入"和"可能是文件路径的输入"——后者会作为普通文本发送给模型，前者则显示 `Unknown skill: xxx` 错误。

### 2.4 三种类型的执行分支

**local-jsx 命令执行**（`processSlashCommand.tsx:550-620`）：

```typescript
// 简化的执行流程
case 'local-jsx': {
  return new Promise<SlashCommandResult>(resolve => {
    // onDone 回调——命令完成时调用
    const onDone = (result?: string, options?) => {
      // display='skip' → 不添加任何消息
      // display='system' → 添加 SystemLocalCommandMessage
      // 默认 → 添加 UserMessage（模型可见）
      resolve({ messages, shouldQuery: options?.shouldQuery ?? false })
    }
    // 延迟加载并执行命令
    void command.load()
      .then(mod => mod.call(onDone, { ...context, canUseTool }, args))
      .then(jsx => {
        // jsx 是 React 元素，通过 setToolJSX 渲染到 REPL
        setToolJSX(() => jsx)
      })
  })
}
```

关键点：`local-jsx` 命令是**异步**的——它通过 `onDone` 回调通知完成，而不是通过返回值。这允许 UI 组件在用户交互完成后才报告结果。

**local 命令执行**（`processSlashCommand.tsx:669-688`）：

```typescript
case 'local': {
  const mod = await command.load()  // 延迟加载
  const result = await mod.call(args, context)  // 同步调用
  // result 是 LocalCommandResult 联合类型
  // type: 'text' → 文本输出
  // type: 'compact' → 上下文压缩结果
  // type: 'skip' → 不显示任何输出
}
```

**prompt 命令执行**（`processSlashCommand.tsx:869-920`）：

```typescript
// prompt 命令有两种执行上下文
if (command.context === 'fork') {
  // fork 模式：在子智能体中执行，隔离 token 预算
  return executeForkedSlashCommand(command, args, context)
} else {
  // inline 模式（默认）：展开到当前对话
  const content = await command.getPromptForCommand(args, context)
  // 提取 allowedTools 并传递给查询引擎
  const additionalAllowedTools = parseToolListFromCLI(command.allowedTools ?? [])
  return {
    messages: [...promptMessages],
    shouldQuery: true,  // prompt 命令总是需要查询模型
    allowedTools: additionalAllowedTools,
    model: command.model,
  }
}
```

### 2.5 Immediate 命令快速路径

标记 `immediate: true` 的命令（如 `/mcp`、`/config`、`/exit`、`/status`）可以在当前查询进行中执行，不需要等待队列。这在 `handlePromptSubmit.ts` 中特殊处理：

```typescript
// src/utils/handlePromptSubmit.ts:250-300（简化）
if (command.immediate === true && (isLoading || externallyLoading)) {
  // 绕过正常队列，立即执行
  const context = getToolUseContext(messages, [], createAbortController(), mainLoopModel)
  const impl = await immediateCommand.load()
  const jsx = await impl.call(onDone, context, commandArgs)
  // 直接渲染到 REPL
}
```

### 2.6 命令输出消息

命令执行结果通过 `SystemLocalCommandMessage` 类型包装：

```typescript
// src/utils/messages.ts:4516-4528
function createCommandInputMessage(content: string): SystemLocalCommandMessage {
  return {
    type: 'system',
    subtype: 'local_command',
    content,          // 输出文本
    level: 'info',
    timestamp: Date.now(),
    uuid: randomUUID(),
    isMeta: false     // 用户可见，但在 API 调用前会被过滤掉
  }
}
```

`local` 和 `local-jsx` 命令的输出被包裹在 `<local-command-stdout>` 标签中，而 `prompt` 命令的输出则作为 `UserMessage` 发送给模型。

---

## 第三章：核心命令详解

Claude Code 包含 80+ 个内置命令。以下详细介绍最重要的 20+ 个命令，按功能类别组织。每个命令包含其文件路径、核心逻辑说明和设计考量。

### 3.1 上下文与会话管理

**`/compact`** — 压缩上下文（`src/commands/compact/`，`local` 类型）

压缩对话历史但保留摘要。这是**上下文窗口经济学**的核心工具——当对话接近 token 上限时（80% 阈值），系统会自动触发压缩，用户也可以手动调用。`/compact` 接受可选参数指定摘要重点（如 `/compact focus on the API changes`），让用户控制压缩后保留的上下文方向。底层实现调用 `src/services/compact/compact.ts`，通过 `groupMessagesByApiRound()` 将消息按轮次分组，生成摘要，并用引用替换原始内容。返回 `LocalCommandResult` 的 `compact` 变体，触发 REPL 状态重置。可通过环境变量 `DISABLE_COMPACT` 禁用。

```typescript
// src/commands/compact/index.ts:4-15
const compact = {
  type: 'local',
  name: 'compact',
  description: 'Clear conversation history but keep a summary in context. ' +
    'Optional: /compact [instructions for summarization]',
  isEnabled: () => !isEnvTruthy(process.env.DISABLE_COMPACT),
  supportsNonInteractive: true,
  argumentHint: '<optional custom summarization instructions>',
  load: () => import('./compact.js'),  // 延迟加载实现模块
} satisfies Command
```

**`/resume`** — 恢复会话（`src/commands/resume/`，`local-jsx` 类型）

恢复之前的对话。这个命令展示了 `local-jsx` 类型的典型用法——它需要渲染一个交互式会话选择器（基于 `src/components/LogSelector.tsx`），让用户从会话列表中选择。支持两种调用方式：直接传入会话 ID（`/resume abc123`）跳过选择器直接恢复，或传入搜索词（`/resume yesterday's refactor`）过滤会话列表。`resume` 入口类型通过 `ResumeEntrypoint` 联合类型追踪调用来源（CLI 参数、选择器、会话 ID、标题搜索、fork），用于分析用户行为。别名 `/continue` 保持向后兼容。

```typescript
// src/commands/resume/index.ts:3-12
const resume: Command = {
  type: 'local-jsx',
  name: 'resume',
  description: 'Resume a previous conversation',
  aliases: ['continue'],         // /continue 也可以触发
  argumentHint: '[conversation id or search term]',
  load: () => import('./resume.js'),
}
```

**`/clear`** — 清除会话（`src/commands/clear/`，`local` 类型）

清除当前对话历史并开始新对话。不同于 `/compact`（保留摘要），`/clear` 完全重置上下文。有三个别名：`/reset` 和 `/new`，反映了用户可能用不同心智模型理解同一操作（"清除"、"重置"、"新建"）。在 Bridge-Safe 命令列表中——可以从手机端远程清除对话。

**`/context`** — 上下文管理（`src/commands/context/`，`prompt` 类型）

管理上下文附件——添加或移除文件、URL、目录等上下文信息。提供两个变体：交互模式（默认，渲染选择界面）和非交互模式（`contextNonInteractive`，支持 `-p` 标志，用于脚本化调用）。与 `/add-dir` 命令互补——后者专门用于添加额外工作目录到上下文。

### 3.2 Git 与代码操作

**`/commit`** — 创建 Git 提交（`src/commands/commit.ts`，`prompt` 类型，INTERNAL_ONLY）

这是 `prompt` 类型命令的典型示例，展示了三个核心设计模式。首先，通过 `allowedTools` **严格限制**模型在执行此命令期间只能使用 git 命令，防止模型在提交过程中执行意外操作：

```typescript
// src/commands/commit.ts:6-10
const ALLOWED_TOOLS = [
  'Bash(git add:*)',      // 只允许 git add
  'Bash(git status:*)',   // 只允许 git status
  'Bash(git commit:*)',   // 只允许 git commit
]
```

其次，`getPromptForCommand()` 方法动态生成包含当前 git 状态的 prompt，使用 `!`\`command\`` 内联 shell 命令语法：

```typescript
// src/commands/commit.ts:57-92（简化）
const command = {
  type: 'prompt',
  name: 'commit',
  allowedTools: ALLOWED_TOOLS,
  source: 'builtin',
  async getPromptForCommand(_args, context) {
    // 生成包含 git status、git diff、git log 的 prompt 模板
    const promptContent = getPromptContent()
    // 执行 prompt 中的 shell 命令占位符（!`git status` 等）
    const finalContent = await executeShellCommandsInPrompt(
      promptContent,
      { ...context, /* 注入 allowedTools 到权限上下文 */ },
      '/commit',
    )
    return [{ type: 'text', text: finalContent }]
  },
} satisfies Command
```

第三，prompt 模板本身包含了完整的 Git 安全协议指令——不修改 git config、不跳过 hooks、不使用 `--amend`、不提交敏感文件——这是**安全优先设计**在 prompt 层面的体现。`executeShellCommandsInPrompt()` 会在发送给模型前执行 `!`\`git status\`` 等占位符并替换为结果，让模型看到最新的仓库状态。

**`/review`** — 代码审查（`src/commands/review.ts`，`prompt` 类型）

分析 PR 的代码更改并提供审查反馈。prompt 模板指导模型执行标准审查流程：获取 PR 信息（`gh pr view`）→ 获取 diff（`gh pr diff`）→ 分析代码质量、风格、性能、安全性 → 生成结构化审查报告。与之对应的 `/ultrareview` 则是 `local-jsx` 类型，在 Web 端运行更深入的 bug 搜索分析（10-20 分钟），展示了同一功能域内 `prompt`（轻量快速）和 `local-jsx`（重量级交互）两种命令类型的互补。

**`/diff`** — 查看更改（`src/commands/diff/`，`local-jsx` 类型）

显示未提交的更改和每轮对话的 diff，帮助用户跟踪 Claude 对文件的修改。渲染交互式 diff 查看器，支持滚动、文件导航和行级对比。这是理解"AI 做了什么"的关键窗口。

**`/rewind`** — 撤销更改（`src/commands/rewind/`，`local-jsx` 类型）

撤销最近的文件更改。渲染交互式界面让用户选择恢复点——可以选择"撤销最后一轮"或精确到特定文件的更改。这是**人在回路**设计的安全网——即使 AI 执行了用户不满意的修改，用户始终可以回退。

**`/security-review`** — 安全审查（`src/commands/security-review.ts`，`prompt` 类型）

专注安全维度的代码审查，指导模型检查 OWASP Top 10 漏洞、注入攻击面、认证/授权缺陷等安全隐患。

### 3.3 配置与设置

**`/config`** — 设置面板（`src/commands/config/`，`local-jsx` 类型）

打开全屏设置面板，管理权限规则、主题、MCP 服务器等配置。别名 `/settings`。这是 Claude Code 中最复杂的 `local-jsx` 命令之一——设置面板包含多个选项卡（权限、外观、MCP、API Key 等），每个选项卡都是独立的 React 组件。`load()` 延迟加载避免了在启动时加载这些 UI 组件。

```typescript
// src/commands/config/index.ts:3-11
const config = {
  aliases: ['settings'],    // /settings 别名
  type: 'local-jsx',
  name: 'config',
  description: 'Open config panel',
  load: () => import('./config.js'),
} satisfies Command
```

**`/permissions`** — 权限管理（`src/commands/permissions/`，`local-jsx` 类型）

管理工具的 Allow/Deny 权限规则。别名 `/allowed-tools`。渲染权限规则编辑器，让用户添加、修改或删除针对特定工具的 Allow/Deny 规则。规则可以按工具类型（如 `Bash`）和参数模式（如 `git *`）精确控制。这些规则持久化到配置文件中，跨会话生效。

**`/memory`** — 记忆编辑（`src/commands/memory/`，`local-jsx` 类型）

编辑 CLAUDE.md 记忆文件。渲染文件编辑器，让用户直接修改项目级（`./CLAUDE.md`）或用户级（`~/.claude/CLAUDE.md`）的持久化指令。修改立即生效——下次查询时新的记忆内容会注入到系统提示中。

**`/theme`** — 主题切换（`src/commands/theme/`，`local-jsx` 类型）

切换终端颜色主题。渲染主题预览选择器，让用户在多个预设主题间选择。选择结果持久化到用户配置。

**`/vim`** — Vim 模式切换（`src/commands/vim/`，`local` 类型）

切换 Vim 和 Normal 编辑模式。注意这是 `local` 类型而不是 `local-jsx`——切换状态不需要交互 UI，只需要一行文本确认。Vim 模式启用后，输入区使用 Doc 4 中描述的 Vim 状态机（11 种 CommandState）处理按键。`supportsNonInteractive: false` 标记表明此命令只在交互式 REPL 中有意义。

### 3.4 诊断与信息

**`/doctor`** — 诊断检查（`src/commands/doctor/`，`local-jsx` 类型）

诊断和验证 Claude Code 安装、设置、依赖是否正常。检查项目包括：Node.js/Bun 运行时版本、git 安装和配置、ripgrep 可用性、API Key 有效性、MCP 服务器连接状态、权限配置完整性等。每个检查项显示通过/失败/警告状态和修复建议。这是**优雅降级**哲学的直接工具——系统出问题时，用户不需要手动排查，`/doctor` 提供系统化的自我诊断。可通过 `DISABLE_DOCTOR_COMMAND` 环境变量禁用。

```typescript
// src/commands/doctor/index.ts:3-12
const doctor: Command = {
  name: 'doctor',
  description: 'Diagnose and verify your Claude Code installation and settings',
  isEnabled: () => !isEnvTruthy(process.env.DISABLE_DOCTOR_COMMAND),
  type: 'local-jsx',
  load: () => import('./doctor.js'),
}
```

**`/cost`** — 费用统计（`src/commands/cost/`，`local` 类型）

显示当前会话的总费用和持续时间，包括输入/输出 token 数量和对应费用。这是 `isHidden` 字段动态使用的典型示例——对按 token 计费的 API 用户显示费用信息至关重要，但对 claude.ai 订阅用户（按月付费）则无意义。使用 getter 而非静态值，因为认证状态可能在会话中改变（如用户执行 `/login` 后）：

```typescript
// src/commands/cost/index.ts:8-23
const cost = {
  type: 'local',
  name: 'cost',
  description: 'Show the total cost and duration of the current session',
  get isHidden() {
    // 对 Anthropic 内部用户始终显示（他们需要看费用明细）
    if (process.env.USER_TYPE === 'ant') { return false }
    // 对 claude.ai 订阅用户隐藏
    return isClaudeAISubscriber()
  },
  supportsNonInteractive: true,
  load: () => import('./cost.js'),
} satisfies Command
```

**`/status`** — 状态信息（`src/commands/status/`，`local-jsx` 类型，`immediate: true`）

显示当前版本、模型、账户、权限模式等状态信息。标记为 `immediate`——可以在查询进行中执行而不等待队列。这对调试和确认当前配置非常重要——用户可能在 AI 工作时想确认使用的是哪个模型。

**`/help`** — 帮助信息（`src/commands/help/`，`local-jsx` 类型）

显示所有可用命令及其描述。按类别组织，标注命令来源（内置、插件、技能等）。使用 `formatDescriptionWithSource()` 为每个命令添加来源标注。

**`/insights`** — 会话分析（`src/commands/insights.ts` shim，`prompt` 类型）

生成当前 Claude Code 使用情况的分析报告。这个命令展示了**延迟加载 shim 模式**——insights.ts 实现文件有 113KB/3200 行（包含 diff 渲染和 HTML 生成），不适合在启动时加载。因此使用一个轻量 shim 对象，只在实际调用时才 `await import()` 真正的实现：

```typescript
// src/commands.ts:190-202
const usageReport: Command = {
  type: 'prompt',
  name: 'insights',
  description: 'Generate a report analyzing your Claude Code sessions',
  contentLength: 0,
  progressMessage: 'analyzing your sessions',
  source: 'builtin',
  async getPromptForCommand(args, context) {
    // 只在调用时才加载 113KB 的实现模块
    const real = (await import('./commands/insights.js')).default
    if (real.type !== 'prompt') throw new Error('unreachable')
    return real.getPromptForCommand(args, context)
  },
}
```

### 3.5 扩展与集成

**`/mcp`** — MCP 管理（`src/commands/mcp/`，`local-jsx` 类型，`immediate: true`）

管理 MCP（Model Context Protocol）服务器的安装、配置和调试。这是连接外部工具生态的核心入口——用户可以通过 `/mcp` 添加新的 MCP 服务器、检查服务器健康状态、调试连接问题。`immediate` 标记确保用户可以在 AI 查询进行中管理 MCP 配置，不会中断工作流。

**`/plugin`** — 插件管理（`src/commands/plugin/`，`local-jsx` 类型，`immediate: true`）

管理插件的安装、卸载和配置。别名 `/plugins` 和 `/marketplace`。渲染插件市场浏览器（`ManagePlugins.tsx`，314KB），支持在线搜索、安装和版本管理。`immediate` 标记同样允许在查询中操作。

**`/skills`** — 技能列表（`src/commands/skills/`，`local-jsx` 类型）

列出所有可用的技能（包括内置捆绑、用户自定义、插件提供的技能），显示每个技能的名称、描述和来源。

**`/hooks`** — Hook 管理（`src/commands/hooks/`，`local-jsx` 类型）

管理 Hook 规则。Hook 是在工具事件发生时自动执行的 shell 命令（如"每次编辑文件后运行 formatter"），通过 CLAUDE.md 中的 if/then 模式匹配定义。

### 3.6 模型与执行控制

**`/model`** — 模型切换（`src/commands/model/`，`local-jsx` 类型）

设置 AI 模型。支持动态描述——在未配置时显示选择提示，已配置时显示当前模型名称。`immediate` 标记（有条件：仅在已有模型配置时）允许快速切换。

**`/plan`** — 计划模式（`src/commands/plan/`，`local-jsx` 类型）

启用计划模式或查看当前会话计划。参数提示 `[open|<description>]`。计划模式下模型在执行每个工具调用前需要用户确认，是**人在回路**设计的高阶形式。

**`/tasks`** — 后台任务（`src/commands/tasks/`，`local-jsx` 类型）

列出和管理后台运行的任务（子智能体、bash 命令等）。别名 `/bashes`。渲染任务列表，显示每个任务的状态、进度和输出。

### 3.7 会话导出与协作

**`/export`** — 导出会话（`src/commands/export/`，`local-jsx` 类型）

导出当前会话或对话内容到文件。渲染导出选项界面，支持多种导出格式。对于需要保存工作记录或与团队分享对话的场景非常有用。

**`/copy`** — 复制消息（`src/commands/copy/`，`local-jsx` 类型）

复制最后一条消息到系统剪贴板。快速提取 AI 生成内容的便捷方式，无需手动选择文本。

**`/session`** — 会话管理（`src/commands/session/`，`local-jsx` 类型）

显示和管理会话信息，包括远程会话的 QR 码/URL。属于 Remote-Safe 命令——可以在远程模式下安全使用，因为它只展示信息而不执行本地操作。

**`/mobile`** — 移动端连接（`src/commands/mobile/`，`local-jsx` 类型）

生成移动端连接的 QR 码，让用户从手机控制 Claude Code 会话。与 Bridge 模式配合使用——手机端通过扫描 QR 码建立 WebSocket 连接，然后可以发送文本输入和接收输出。

### 3.8 初始化与认证

**`/init`** — 初始化项目（`src/commands/init.ts`，`prompt` 类型）

分析代码库并创建 CLAUDE.md 文件。这是最长的 prompt 命令之一（80+ 行 prompt 模板），引导模型执行多阶段流程：阶段 1 询问用户想设置什么（项目 CLAUDE.md / 个人 CLAUDE.local.md / Skills / Hooks）→ 阶段 2 启动子智能体调查代码库（manifest 文件、README、CI 配置、已有规则文件等）→ 阶段 3 向用户确认推断结果 → 阶段 4 生成配置文件。完成后标记项目 onboarding 状态。

**`/login`** / **`/logout`** — 认证管理（`src/commands/login/`、`src/commands/logout/`）

登录/登出账户。仅在非第三方服务时可用（`!isUsing3PServices()`）。`/login` 使用工厂函数 `login()` 生成，因为它需要在构造时读取当前认证状态来动态设置描述文本（如"Switch accounts"或"Sign in"）。可通过 `DISABLE_LOGIN_COMMAND` / `DISABLE_LOGOUT_COMMAND` 环境变量禁用。

---

## 第四章：Feature-Gated 命令

Feature Flag 系统（Doc 2 中详述）在命令系统中得到广泛应用。以下是所有 Feature-Gated 命令的完整列表：

| Feature Flag | 命令名 | 类型 | 描述 |
|-------------|--------|------|------|
| `PROACTIVE` 或 `KAIROS` | `/proactive` | prompt | 主动式辅助 |
| `KAIROS` 或 `KAIROS_BRIEF` | `/brief` | prompt | 简短摘要模式 |
| `KAIROS` | `/assistant` | local-jsx | 助手模式 |
| `BRIDGE_MODE` | `/bridge` | local-jsx | 远程控制桥接 |
| `DAEMON` + `BRIDGE_MODE` | `/remoteControlServer` | local-jsx | 远程控制服务器 |
| `VOICE_MODE` | `/voice` | local-jsx | 语音模式 |
| `HISTORY_SNIP` | `/force-snip` | prompt | 强制上下文裁剪 |
| `WORKFLOW_SCRIPTS` | `/workflows` | local | 工作流命令 |
| `CCR_REMOTE_SETUP` | `/remote-setup` | local-jsx | 远程设置 |
| `KAIROS_GITHUB_WEBHOOKS` | `/subscribe-pr` | prompt | PR 订阅 |
| `ULTRAPLAN` | `/ultraplan` | prompt | 高级规划 |
| `TORCH` | `/torch` | prompt | Torch 功能 |
| `UDS_INBOX` | `/peers` | local-jsx | 对等消息 |
| `FORK_SUBAGENT` | `/fork` | local-jsx | Fork 子智能体 |
| `BUDDY` | `/buddy` | local-jsx | Buddy 助手 |

Feature-Gated 命令使用 `require()` 条件导入（而非 `import`），利用 Bun 的编译时死代码消除：

```typescript
// src/commands.ts:62-65
// 当 PROACTIVE 和 KAIROS 都为 false 时，整个 require 分支和模块
// 在编译时被消除，不会进入最终产物
const proactive =
  feature('PROACTIVE') || feature('KAIROS')
    ? require('./commands/proactive.js').default
    : null
```

### 4.1 内部专用命令

除了 Feature-Gated 命令，还有一组仅对 Anthropic 内部用户（`USER_TYPE=ant`）可用的命令，通过 `INTERNAL_ONLY_COMMANDS` 数组管理：

```typescript
// src/commands.ts:224-254
export const INTERNAL_ONLY_COMMANDS = [
  backfillSessions,  // 会话回填
  breakCache,        // 缓存清除
  bughunter,         // Bug 猎手
  commit,            // Git 提交（内部版本）
  commitPushPr,      // 提交+推送+PR
  ctx_viz,           // 上下文可视化
  goodClaude,        // 反馈收集
  issue,             // Issue 管理
  initVerifiers,     // 验证器初始化
  mockLimits,        // 模拟限流
  bridgeKick,        // 桥接踢出
  version,           // 版本信息
  share,             // 分享
  summary,           // 摘要
  teleport,          // 跨机器迁移
  antTrace,          // 追踪
  perfIssue,         // 性能报告
  env,               // 环境变量查看
  oauthRefresh,      // OAuth 刷新
  debugToolCall,     // 调试工具调用
  agentsPlatform,    // 智能体平台
  autofixPr,         // 自动修复 PR
  // ... Feature-Gated 的内部命令也被条件包含
].filter(Boolean)  // 过滤掉 null（Feature Flag 为 false 时的 require 结果）
```

这些命令在外部构建中被完全消除——不是运行时隐藏，而是编译时移除。

### 4.2 安全分类：Remote-Safe 和 Bridge-Safe

命令还按安全等级分类，控制它们在远程和桥接模式下的可用性：

**Remote-Safe 命令**——在 `--remote` 模式下可用（不依赖本地文件系统、Git、Shell 等）：

```typescript
// src/commands.ts:619-637
export const REMOTE_SAFE_COMMANDS: Set<Command> = new Set([
  session, exit, clear, help, theme, color, vim, cost,
  usage, copy, btw, feedback, plan, keybindings, statusline,
  stickers, mobile,
])
```

**Bridge-Safe 命令**——可通过远程控制桥接（手机/Web 客户端）执行的 `local` 类型命令：

```typescript
// src/commands.ts:651-660
export const BRIDGE_SAFE_COMMANDS: Set<Command> = new Set([
  compact,       // 从手机压缩上下文
  clear,         // 清除会话
  cost,          // 查看费用
  summary,       // 摘要
  releaseNotes,  // 更新日志
  files,         // 列出文件
])
```

桥接安全判断遵循类型规则：

```typescript
// src/commands.ts:672-676
export function isBridgeSafeCommand(cmd: Command): boolean {
  if (cmd.type === 'local-jsx') return false   // JSX 命令始终不安全（渲染 Ink UI）
  if (cmd.type === 'prompt') return true        // Prompt 命令始终安全（展开为文本）
  return BRIDGE_SAFE_COMMANDS.has(cmd)          // Local 命令需要显式白名单
}
```

---

## 第五章：命令扩展模式

### 5.1 添加新的内置命令

添加新命令的模式极其一致。以 `local-jsx` 类型为例：

**步骤 1**：在 `src/commands/` 下创建目录，包含 `index.ts`（元数据）和实现文件：

```typescript
// src/commands/my-command/index.ts
import type { Command } from '../../commands.js'

const myCommand = {
  type: 'local-jsx',
  name: 'my-command',
  description: 'Description visible to users',
  load: () => import('./myCommand.js'),  // 延迟加载
} satisfies Command

export default myCommand
```

**步骤 2**：实现 `call()` 函数：

```typescript
// src/commands/my-command/myCommand.ts
import type { LocalJSXCommandCall } from '../../types/command.js'

export const call: LocalJSXCommandCall = async (onDone, context, args) => {
  // 执行逻辑...
  // 完成时调用 onDone
  onDone('Command completed', { display: 'system' })
  // 返回 React 元素（可选）
  return <MyUI />
}
```

**步骤 3**：在 `src/commands.ts` 的 `COMMANDS()` 数组中注册。

### 5.2 通过技能（Skills）扩展

不修改代码就能添加新命令的方式是通过 **Skills**。技能是 YAML/JSON 格式的 prompt 模板，放在 `.claude/skills/` 目录下：

- **用户技能目录**：`~/.claude/skills/`
- **项目技能目录**：`.claude/skills/`（受信任时加载）
- **插件技能**：通过插件系统注册
- **内置捆绑技能**：`src/skills/bundledSkills.ts`

技能被自动发现并注册为 `prompt` 类型命令，用户可以通过 `/skill-name` 调用。

### 5.3 通过插件（Plugins）扩展

插件可以注册新命令，放在 `~/.claude/plugins/` 目录下。插件命令通过 `getPluginCommands()` 和 `getPluginSkills()` 加载，source 标记为 `'plugin'`。

### 5.4 通过工作流（Workflows）扩展

当 `WORKFLOW_SCRIPTS` Feature Flag 启用时，工作流脚本可以注册为命令：

```typescript
// src/commands.ts:401-406
const getWorkflowCommands = feature('WORKFLOW_SCRIPTS')
  ? require('./tools/WorkflowTool/createWorkflowCommand.js').getWorkflowCommands
  : null
```

工作流命令有 `kind: 'workflow'` 标记，在自动补全中显示特殊徽章。

### 5.5 命令-工具关系

命令和工具的关系是**互补而非竞争**的：

- **命令**是**用户发起**的——用户通过 `/command` 主动触发
- **工具**是 **AI 发起**的——模型在推理过程中决定调用
- `prompt` 类型命令可以通过 `allowedTools` 限制模型可用的工具
- `SkillTool` 是一个特殊工具，允许模型反向调用 prompt 类型命令

这种双向关系形成了人机协作的完整闭环：

```
用户 --/command--> 命令系统 --prompt--> 模型 --tool_use--> 工具系统
                                          ↑                    │
                                          └──── 结果反馈 ──────┘
```

### 5.6 命令-权限集成

`LocalJSXCommandContext` 扩展了 `ToolUseContext`，这意味着命令内部可以访问完整的权限检查上下文：

```typescript
// src/types/command.ts:80-98
export type LocalJSXCommandContext = ToolUseContext & {
  canUseTool?: CanUseToolFn    // 权限检查函数
  setMessages: (updater: (prev: Message[]) => Message[]) => void
  options: {
    dynamicMcpConfig?: Record<string, ScopedMcpServerConfig>
    ideInstallationStatus: IDEExtensionInstallationStatus | null
    theme: ThemeName
  }
  onChangeAPIKey: () => void
  // ... 其他上下文
}
```

`prompt` 命令的 `allowedTools` 字段是命令-权限集成的另一个维度——它在 prompt 执行期间临时扩展模型可用的工具集，执行完成后恢复。

---

## 第六章：设计哲学分析

命令系统是 Claude Code 十大设计哲学（Doc 1 中介绍）的集中体现。以下分析六个核心设计哲学在命令系统中的具体表现。

### 6.1 可组合性（Composability）

Command 类型的设计是**可组合性**的教科书案例。`CommandBase & (PromptCommand | LocalCommand | LocalJSXCommand)` 结构让通用元数据与特定执行模式正交组合——这是 TypeScript 联合类型在架构层面的精彩运用：

- **类型正交组合**：通用字段（name、description、isEnabled、availability）对所有命令类型一致，执行模式（prompt/local/local-jsx）各自独立定义输入输出契约。新增一个通用字段自动应用于所有命令类型，新增一个执行模式不影响已有模式。
- **命令-工具组合**：`allowedTools` 字段让命令与工具系统精确组合——`/commit` 通过 `['Bash(git add:*)', 'Bash(git status:*)', 'Bash(git commit:*)']` 将模型限制在 git 操作范围内，`/init` 则开放全部工具让模型自由探索代码库。
- **命令-Hook 组合**：`hooks` 字段让技能在调用时注册额外的事件处理器，实现命令与 Hook 系统的运行时组合。
- **命令-智能体组合**：`context: 'fork'` 让命令可以在子智能体中执行，与多智能体系统无缝对接。

`getCommands()` 的多来源整合（bundled → builtinPlugin → skillDir → workflow → plugin → pluginSkill → builtin）是组合模式的另一个维度——六种不同来源的命令通过统一的 `Command` 接口无缝混合，消费者无需关心命令从哪里来。`formatDescriptionWithSource()` 在 UI 层用来源标注区分它们，但在类型系统层面它们完全统一。

### 6.2 无需修改的可扩展性（Extensibility Without Modification）

命令系统通过**四个独立的扩展维度**实现了开闭原则（Open-Closed Principle）在系统规模上的应用：

1. **Skills**：用户在 `.claude/skills/` 目录放入 YAML/Markdown 文件就能创建新的 prompt 命令——零代码修改，零重启。`getSkillDirCommands()` 自动发现并注册为 `Command` 对象。
2. **Plugins**：插件通过 `getPluginCommands()` 和 `getPluginSkills()` 注入新命令，可以是任意类型（prompt/local/local-jsx）。插件甚至可以通过 `registerBuiltinPlugin()` 模式捆绑到核心包中。
3. **MCP**：MCP 服务器提供的工具自动通过 `mcp__serverName__toolName` 命名规范注册为可调用命令，无需在命令系统中添加任何代码。
4. **Workflows**：当 `WORKFLOW_SCRIPTS` Feature Flag 启用时，工作流脚本（`kind: 'workflow'`）自动生成命令入口。

Feature-Gated 命令本身也体现了这一原则——新功能通过 Feature Flag 条件编译加入，不修改已有命令的代码。`COMMANDS()` 数组使用展开运算符 `...(flag ? [cmd] : [])` 实现编译时的命令集组合，让命令注册表在不同构建配置下自动调整。

### 6.3 隔离与遏制（Isolation & Containment）

命令系统实现了**三层隔离**，从编译时到运行时逐层递进：

**第一层：编译时物理隔离**。内部命令（`INTERNAL_ONLY_COMMANDS`）在外部构建中被编译时移除——不是运行时禁用，不是 UI 隐藏，而是代码级别的物理消除。Feature-Gated 命令通过 `feature()` + `require()` 实现死代码消除，外部用户的二进制文件中根本不存在这些命令的字节码。

**第二层：认证级别隔离**。`availability` 系统在运行时按认证状态隔离不同用户群体——`claude-ai` 订阅用户看到 `/usage` 和 `/upgrade`，`console` API 用户看到 `/cost`，第三方服务用户看不到 `/login`。`meetsAvailabilityRequirement()` 使用 `never` 类型穷举检查确保新增认证类型时编译器会强制更新逻辑。

**第三层：执行环境隔离**。Bridge-Safe 和 Remote-Safe 分类控制命令在不同执行环境下的可用性。`local-jsx` 命令被所有远程通道阻止（它们渲染本地终端 UI，远程客户端无法显示）。`local` 命令需要通过 `BRIDGE_SAFE_COMMANDS` 白名单才能从手机/Web 端执行。`prompt` 命令因为只产生文本，在所有环境下都安全。

`prompt` 命令的 `context: 'fork'` 选项是最强的运行时隔离——命令在独立的子智能体中执行，有独立的 token 预算和上下文空间，不会污染主对话的消息历史。

### 6.4 优雅降级（Graceful Degradation）

**`/doctor`** 是优雅降级哲学的直接工具体现——它是一个**自我诊断命令**。当系统出现问题时，用户不需要手动排查环境配置，`/doctor` 会系统化检查运行时、依赖、API 连接、MCP 服务器等，每项检查给出通过/失败/警告状态和修复建议。这是"系统应该能诊断自身问题"理念的实现。

命令加载过程本身也体现了**分层降级**：

```typescript
// src/commands.ts:361-373
const [skillDirCommands, pluginSkills] = await Promise.all([
  getSkillDirCommands(cwd).catch(err => {
    logError(toError(err))
    return []  // 技能加载失败 → 返回空数组，继续运行
  }),
  getPluginSkills().catch(err => {
    logError(toError(err))
    return []  // 插件技能加载失败 → 返回空数组，继续运行
  }),
])
```

每个命令来源都有独立的 `.catch()` 错误处理——技能目录读取失败不会阻止内置命令加载，插件崩溃不会影响核心功能。用户可能失去某些扩展命令，但核心命令集始终可用。这种"部分功能优于完全失败"的策略贯穿整个加载链路。

`/compact` 也是优雅降级的体现——当对话上下文接近限制时，不是报错中断，而是压缩历史保留最重要的信息，让对话可以继续。

### 6.5 上下文窗口经济学（Context Window Economics）

**`/compact`** 是上下文窗口经济学的核心工具——它主动管理对话上下文，在接近 token 上限时压缩历史消息但保留摘要。用户可以通过参数指定摘要重点（如 `/compact keep the API design decisions`），确保最重要的上下文被保留。

命令输出消息的类型选择是经济学的另一个体现——不同的消息类型决定了多少上下文空间被消耗：

- `SystemLocalCommandMessage`（`local` 和 `local-jsx` 的默认输出格式）在发送给 API 前被过滤掉——本地命令的 UI 输出不消耗模型 token
- `display: 'skip'` 选项让命令可以完全不生成消息，零上下文开销（用于纯 UI 操作如 `/config dismissed`）
- `display: 'system'` 产生仅在本地可见的消息，不发送给模型
- `display: 'user'`（默认）产生模型可见的消息——仅在命令结果需要 AI 理解时使用
- `isMeta: true` 的 `metaMessages` 选项产生模型可见但用户不可见的消息，用于注入上下文而不干扰用户界面

### 6.6 人在回路（Human-in-the-Loop）

命令系统本身就是**人在回路**设计的核心表达层。命令是用户意图的直接表达，而工具调用是 AI 的自主决策——两者形成了人机协作的完整闭环：

- **用户主动权**：`/command` 让用户在任何时候主动发起操作，不依赖 AI 的判断
- **选择性查询**：`LocalJSXCommandOnDone` 回调的 `shouldQuery` 选项让命令控制是否需要 AI 后续处理——`/config` 调整设置后不需要查询（纯配置操作），但 `/review PR #123` 需要模型分析代码
- **计划模式**：`/plan` 命令是人在回路的高阶形式——启用后模型在执行每个工具调用前需要用户审查和批准，实现了"AI 提议，人类决策"的协作模式
- **权限边界调整**：`/permissions` 和 `/config` 让用户随时修改 AI 的行为边界，而非一次性设定后不可调整
- **桥接人机**：`prompt` 命令独特地桥接了两者——用户发起命令（人在回路入口），命令展开为 prompt 交给模型执行（AI 自主执行），模型在执行过程中可能触发工具调用权限提示（人在回路再入口），形成多层嵌套的人机协作

命令和工具的关系是**互补而非竞争**的。`SkillTool` 允许模型反向调用 `prompt` 类型命令——但 `userInvocable: false` 的技能只能被模型调用，不能被用户直接输入。这种双向通道确保了人类和 AI 各自在最适合的时机参与协作。

---

## 关键要点总结

1. **统一的 Command 类型**：`CommandBase & (PromptCommand | LocalCommand | LocalJSXCommand)` 三变体联合类型，用 `satisfies Command` 确保类型安全
2. **三种执行模式**：`prompt`（模型执行）、`local`（同步文本）、`local-jsx`（React UI），各有不同的 call 签名和生命周期
3. **80+ 内置命令**：覆盖上下文管理、Git 操作、配置设置、诊断信息、扩展集成、模型控制等 7 大类
4. **多层过滤**：Feature Flag 编译时消除 → `availability` 认证级过滤 → `isEnabled()` 运行时检查 → Remote-Safe / Bridge-Safe 安全分类
5. **五种扩展途径**：内置命令、Skills、Plugins、Workflows、MCP——全部通过统一的 `Command` 接口注册和发现
6. **延迟加载**：所有 `local` 和 `local-jsx` 命令通过 `load()` 延迟加载实现模块，Feature-Gated 命令通过 `require()` 条件导入实现编译时消除

---

## 下一篇预览

Doc 6 将深入**工具系统**——如果说命令是用户与 Claude Code 交互的通道，那么工具就是 AI 与外部世界交互的通道。我们将详细分析 `Tool` 接口的设计、`buildTool()` 工厂函数、40+ 个内置工具的分类和实现，以及工具执行的完整生命周期：从权限检查到 Zod 输入验证，从执行上下文构建到结果存储和截断。
