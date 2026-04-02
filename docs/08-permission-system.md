# Doc 8: 权限系统

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）、Doc 2（构建系统与运行时）、Doc 3（入口点与初始化流程）、Doc 4（终端 UI 系统）、Doc 5（命令系统）、Doc 6（工具系统）、Doc 7（查询引擎与 LLM 交互）

在前七篇文档中，我们理解了 Claude Code 如何从启动到运行一个完整的查询循环——用户输入经过 QueryEngine 发送到 Claude API，Claude 的回复可能包含工具调用，工具系统负责执行这些调用。但一个关键的问题始终悬而未决：**谁来决定一个工具调用是否被允许执行？** 这就是权限系统的职责。

权限系统是 Claude Code 安全架构的核心支柱。它控制着每一次工具调用——无论是读取文件、执行 Shell 命令、还是创建子智能体——是否被允许。这个系统的设计理念可以用一句话概括：**信任是需要被赚取的，而不是被假设的（Trust is earned, not assumed）**。

本文档将深入分析权限系统的五种模式、规则引擎、文件系统沙箱、拒绝追踪、Hook 集成、企业管理、ML 分类器，以及完整的权限决策流程。

---

## 第一章：权限模式 src/utils/permissions/PermissionMode.ts

### 1.1 五种权限模式的定义

Claude Code 定义了一个渐进式的权限模式体系。权限模式的类型定义位于 `src/types/permissions.ts`：

```typescript
// src/types/permissions.ts:16-38
// 外部用户可用的权限模式（不包含 auto）
export const EXTERNAL_PERMISSION_MODES = [
  'acceptEdits',        // 自动接受文件编辑，其他仍需询问
  'bypassPermissions',  // 跳过所有权限检查（仅安全检查除外）
  'default',            // 默认模式：每次工具调用都交互式询问
  'dontAsk',            // 不询问，直接拒绝需要权限的操作
  'plan',               // 计划模式：先审查再执行
] as const

export type ExternalPermissionMode = (typeof EXTERNAL_PERMISSION_MODES)[number]

// 内部权限模式类型：在外部模式基础上增加 auto 和 bubble
export type InternalPermissionMode = ExternalPermissionMode | 'auto' | 'bubble'
export type PermissionMode = InternalPermissionMode

// 运行时验证集合：用户可设置的模式
// auto 模式仅在 TRANSCRIPT_CLASSIFIER Feature Flag 启用时可用
export const INTERNAL_PERMISSION_MODES = [
  ...EXTERNAL_PERMISSION_MODES,
  ...(feature('TRANSCRIPT_CLASSIFIER')
    ? (['auto'] as const)    // 内部用户可用的 auto 模式
    : ([] as const)),        // 外部构建中排除 auto
] as const satisfies readonly PermissionMode[]
```

注意 `auto` 模式通过 `feature('TRANSCRIPT_CLASSIFIER')` 进行编译时条件包含——外部构建的产物中根本不存在 auto 模式的代码，这是 Doc 2 中介绍的死代码消除（DCE）技术在安全领域的应用。

### 1.2 模式配置与 UI 表现

每种模式在 UI 中有不同的显示方式，配置定义在 `PermissionMode.ts` 中：

```typescript
// src/utils/permissions/PermissionMode.ts:42-91
const PERMISSION_MODE_CONFIG: Partial<
  Record<PermissionMode, PermissionModeConfig>
> = {
  default: {
    title: 'Default',        // 默认模式标题
    shortTitle: 'Default',
    symbol: '',              // 无特殊符号
    color: 'text',           // 普通文本色
    external: 'default',
  },
  plan: {
    title: 'Plan Mode',      // 计划模式
    shortTitle: 'Plan',
    symbol: PAUSE_ICON,      // 暂停图标 ⏸
    color: 'planMode',       // 蓝色
    external: 'plan',
  },
  acceptEdits: {
    title: 'Accept edits',   // 自动接受编辑
    shortTitle: 'Accept',
    symbol: '⏵⏵',           // 快进符号
    color: 'autoAccept',     // 绿色
    external: 'acceptEdits',
  },
  bypassPermissions: {
    title: 'Bypass Permissions', // 跳过权限
    shortTitle: 'Bypass',
    symbol: '⏵⏵',
    color: 'error',          // 红色——视觉警告
    external: 'bypassPermissions',
  },
  // auto 模式仅在 TRANSCRIPT_CLASSIFIER 启用时存在
  ...(feature('TRANSCRIPT_CLASSIFIER')
    ? {
        auto: {
          title: 'Auto mode',    // AI 自动判断
          shortTitle: 'Auto',
          symbol: '⏵⏵',
          color: 'warning' as ModeColorKey,  // 橙色——提醒用户
          external: 'default' as ExternalPermissionMode,
        },
      }
    : {}),
}
```

注意颜色的安全信号设计：`default` 使用普通文本色（安全），`plan` 使用蓝色（审查），`acceptEdits` 使用绿色（部分自动），`bypassPermissions` 使用**红色**（危险警告），`auto` 使用**橙色**（需要注意）。

### 1.3 五种模式详解

| 模式 | 行为 | 安全级别 | 适用场景 |
|------|------|---------|---------|
| **default** | 每次工具调用都弹出交互式权限提示，用户逐一确认 | 最高 | 首次使用、敏感项目 |
| **plan** | Claude 先制定计划供用户审查，审查通过后才执行 | 高 | 复杂任务、需要全局把控 |
| **acceptEdits** | 自动接受文件编辑操作，Shell 命令等仍需询问 | 中 | 日常开发、信任文件操作 |
| **bypassPermissions** | 跳过大部分权限检查，仅安全检查（.git/、.claude/ 等）仍然生效 | 低 | 完全信任的环境 |
| **auto** | 使用 AI 分类器自动判断是否允许，无需人工确认 | 中 | 内部用户、高效工作流 |
| **dontAsk** | 需要权限的操作直接拒绝，不弹出提示 | 最高（限制性） | 自动化脚本、CI 环境 |

每种模式的安全分析：

**default 模式**——这是最安全的模式，因为每次工具调用都会暂停执行并等待人工确认。用户可以看到工具名称、输入参数和权限请求原因，然后选择允许、拒绝、或设置永久规则。这种模式的代价是交互频繁，但保证了完全的可控性。

**plan 模式**——Claude 在执行任何操作前必须先提出一个执行计划（plan），用户审查并批准后才开始执行。这在复杂任务（如重构多个文件、执行多步部署）中特别有用，因为用户可以在看到整体计划后做出更明智的决策，而不是逐个工具调用地审批。当用户最初以 `bypassPermissions` 启动会话后切换到 `plan` 模式时，plan 模式会保留 bypass 的能力（`isBypassPermissionsModeAvailable` 标记），允许计划批准后自动执行。

**acceptEdits 模式**——仅自动允许文件编辑操作（Read、Write、Edit、Glob、Grep 等），Shell 命令、智能体创建等高风险操作仍需手动确认。这是日常开发中安全性和效率的良好平衡点。

**bypassPermissions 模式**——跳过几乎所有权限检查，但有三个例外：(1) 安全检查（safetyCheck）——修改 `.git/`、`.claude/`、`.vscode/`、shell 配置文件仍需确认；(2) 内容级 ask 规则——用户明确配置的 `ask` 规则（如 `Bash(git push:*)`）仍然生效；(3) 需要用户交互的工具——`requiresUserInteraction()` 返回 `true` 的工具仍会提示。这些例外确保即使在最宽松的模式下，关键安全边界仍然有效。

**auto 模式**——仅内部用户可用（通过 `TRANSCRIPT_CLASSIFIER` Feature Flag 门控，在外部构建中完全排除）。使用 AI 分类器（YOLO Classifier）自动判断每个工具调用是否安全。分类器使用两阶段策略（详见第七章），并配合拒绝追踪系统（第四章）防止过度保守。进入 auto 模式时，系统会自动剥离危险的权限规则（如 `Bash(*)`），防止用户预设的宽松规则绕过分类器的安全检查。

**dontAsk 模式**——所有需要权限的操作直接被拒绝，不弹出任何提示。适用于 CI/CD 环境和自动化脚本，在这些场景中没有人可以响应交互式提示。需要明确区分 `dontAsk` 和 `default` 的行为差异——`default` 模式会暂停等待用户回应，而 `dontAsk` 会立即拒绝。

### 1.4 模式切换循环

用户通过 `Shift+Tab` 快捷键在模式间循环切换。切换逻辑定义在 `getNextPermissionMode.ts` 中：

```typescript
// src/utils/permissions/getNextPermissionMode.ts:34-78
export function getNextPermissionMode(
  toolPermissionContext: ToolPermissionContext,
): PermissionMode {
  switch (toolPermissionContext.mode) {
    case 'default':
      // 内部用户跳过 acceptEdits 和 plan，直接到 bypass 或 auto
      if (process.env.USER_TYPE === 'ant') {
        if (toolPermissionContext.isBypassPermissionsModeAvailable) {
          return 'bypassPermissions'  // 有 bypass 权限则直接跳转
        }
        if (canCycleToAuto(toolPermissionContext)) {
          return 'auto'               // 否则尝试 auto 模式
        }
        return 'default'              // 都不可用则保持 default
      }
      return 'acceptEdits'            // 外部用户：default → acceptEdits

    case 'acceptEdits':
      return 'plan'                   // acceptEdits → plan

    case 'plan':
      if (toolPermissionContext.isBypassPermissionsModeAvailable) {
        return 'bypassPermissions'    // plan → bypass（如果可用）
      }
      if (canCycleToAuto(toolPermissionContext)) {
        return 'auto'                 // 否则 plan → auto
      }
      return 'default'                // 否则回到 default

    case 'bypassPermissions':
      if (canCycleToAuto(toolPermissionContext)) {
        return 'auto'                 // bypass → auto
      }
      return 'default'                // bypass → default

    default:
      return 'default'                // auto 等其他模式 → 回到 default
  }
}
```

外部用户的切换路径为：`default → acceptEdits → plan → bypassPermissions → default`（循环）。内部用户的路径为：`default → bypassPermissions → auto → default`。这种设计确保了权限的**渐进式提升**——用户必须有意识地逐步放松安全限制。

切换时还需要处理上下文清理，例如进入 `auto` 模式时会自动剥离危险权限规则：

```typescript
// src/utils/permissions/getNextPermissionMode.ts:88-101
export function cyclePermissionMode(
  toolPermissionContext: ToolPermissionContext,
): { nextMode: PermissionMode; context: ToolPermissionContext } {
  const nextMode = getNextPermissionMode(toolPermissionContext)
  return {
    nextMode,
    // transitionPermissionMode 会在进入 auto 时调用
    // stripDangerousPermissionsForAutoMode() 移除危险规则
    context: transitionPermissionMode(
      toolPermissionContext.mode,
      nextMode,
      toolPermissionContext,
    ),
  }
}
```

---

## 第二章：规则引擎 src/utils/permissions/permissions.ts

### 2.1 权限规则的数据结构

权限系统的核心是基于规则的 Allow/Deny/Ask 机制。每条规则由三个部分组成：

```typescript
// src/types/permissions.ts:44-79
// 规则的行为类型：允许、拒绝、或询问
export type PermissionBehavior = 'allow' | 'deny' | 'ask'

// 规则来源：从哪里加载的这条规则
export type PermissionRuleSource =
  | 'userSettings'     // 用户全局设置 ~/.claude/settings.json
  | 'projectSettings'  // 项目设置 .claude/settings.json
  | 'localSettings'    // 本地设置 .claude/settings.local.json
  | 'flagSettings'     // GrowthBook 远程配置
  | 'policySettings'   // 企业 MDM 策略
  | 'cliArg'           // 命令行参数 --allowed-tools
  | 'command'          // 命令定义中的 allowedTools
  | 'session'          // 会话内临时规则

// 规则的值：指定哪个工具和可选的内容匹配
export type PermissionRuleValue = {
  toolName: string      // 工具名称，如 "Bash"、"FileEdit"
  ruleContent?: string  // 可选的内容匹配，如 "npm install"、"prefix:*"
}

// 完整的权限规则
export type PermissionRule = {
  source: PermissionRuleSource     // 规则来源
  ruleBehavior: PermissionBehavior // 行为类型
  ruleValue: PermissionRuleValue   // 规则值
}
```

### 2.2 权限决策类型

权限决策是规则引擎的输出。三种决策类型定义在 `src/types/permissions.ts` 中，每种携带不同的上下文信息：

```typescript
// src/types/permissions.ts:174-246（概要）
// 允许决策：可以携带修改后的输入和反馈
export type PermissionAllowDecision<Input> = {
  behavior: 'allow'
  updatedInput?: Input              // 可选：Hook 修改后的工具输入
  userModified?: boolean            // 用户是否手动修改了输入
  decisionReason?: PermissionDecisionReason  // 允许的原因
  acceptFeedback?: string           // 反馈消息
}

// 询问决策：携带消息和建议
export type PermissionAskDecision<Input> = {
  behavior: 'ask'
  message: string                   // 显示给用户的权限请求消息
  suggestions?: PermissionUpdate[]  // 建议的权限更新（如 "Always allow"）
  pendingClassifierCheck?: PendingClassifierCheck  // 挂起的分类器检查
}

// 拒绝决策：携带原因
export type PermissionDenyDecision = {
  behavior: 'deny'
  message: string                   // 拒绝原因消息
  decisionReason: PermissionDecisionReason  // 拒绝的详细原因
}
```

决策原因（`PermissionDecisionReason`）是一个标记联合类型，记录了导致此决策的具体原因——可能是规则匹配（`type: 'rule'`）、模式要求（`type: 'mode'`）、Hook 决定（`type: 'hook'`）、分类器判断（`type: 'classifier'`）、安全检查（`type: 'safetyCheck'`）、工作目录限制（`type: 'workingDir'`）等。这些原因信息用于向用户显示清晰的权限请求消息。

权限请求消息的生成也很精细——`createPermissionRequestMessage()` 函数根据决策原因类型生成不同的用户可读消息：

```typescript
// src/utils/permissions/permissions.ts:137-211（概要）
export function createPermissionRequestMessage(
  toolName: string,
  decisionReason?: PermissionDecisionReason,
): string {
  if (decisionReason) {
    switch (decisionReason.type) {
      case 'classifier':
        // "Classifier 'xxx' requires approval for this Bash command: reason"
        return `Classifier '${decisionReason.classifier}' requires approval...`
      case 'hook':
        // "Hook 'security-check' blocked this action: reason"
        return `Hook '${decisionReason.hookName}' blocked this action...`
      case 'rule':
        // "Permission rule 'Bash(git push:*)' from project settings requires approval"
        return `Permission rule '${ruleString}' from ${sourceString} requires approval...`
      case 'subcommandResults':
        // "This Bash command contains multiple operations. The following 2 parts require approval: npm publish, git push"
        return `This ${toolName} command contains multiple operations...`
      case 'mode':
        // "Current permission mode (Default) requires approval for this Bash command"
        return `Current permission mode (${modeTitle}) requires approval...`
      // ... 其他原因类型
    }
  }
  return `Claude requested permissions to use ${toolName}, but you haven't granted it yet.`
}
```

这种精确的消息让用户知道**为什么**需要权限确认，而不只是模糊的"需要权限"。

### 2.3 规则的存储格式与解析

规则以字符串形式存储在设置文件中，格式为 `"ToolName"` 或 `"ToolName(content)"`：

```json
{
  "permissions": {
    "allow": ["Read", "Bash(npm install)", "Bash(npm test:*)"],
    "deny": ["Bash(rm -rf:*)"],
    "ask": ["Bash(git push:*)"]
  }
}
```

解析逻辑位于 `permissionRuleParser.ts`：

```typescript
// src/utils/permissions/permissionRuleParser.ts:93-133
export function permissionRuleValueFromString(
  ruleString: string,
): PermissionRuleValue {
  // 查找第一个未转义的左括号
  const parenIndex = findFirstUnescapedParen(ruleString)
  if (parenIndex === -1) {
    // 无括号：整个工具匹配，如 "Bash"
    return { toolName: normalizeLegacyToolName(ruleString) }
  }

  // 有括号：提取工具名和内容
  const toolName = normalizeLegacyToolName(ruleString.slice(0, parenIndex))
  // 去掉两端括号并反转义
  const rawContent = ruleString.slice(parenIndex + 1, -1)
  const ruleContent = unescapeRuleContent(rawContent)
  return { toolName, ruleContent }
}
```

注意 `normalizeLegacyToolName()` 函数——它将旧工具名映射到新名称（如 `Task → Agent`、`KillShell → TaskStop`），保证向后兼容。转义处理也很重要：括号和反斜杠需要正确转义以防止解析攻击。

### 2.4 规则收集与匹配

`permissions.ts` 中的三个核心函数从 `ToolPermissionContext` 中收集所有规则：

```typescript
// src/utils/permissions/permissions.ts:122-231
// 收集所有 allow 规则
export function getAllowRules(
  context: ToolPermissionContext,
): PermissionRule[] {
  // 遍历所有规则来源，将字符串解析为 PermissionRule 对象
  return PERMISSION_RULE_SOURCES.flatMap(source =>
    (context.alwaysAllowRules[source] || []).map(ruleString => ({
      source,
      ruleBehavior: 'allow',
      ruleValue: permissionRuleValueFromString(ruleString),
    })),
  )
}

// 收集所有 deny 规则（结构完全相同）
export function getDenyRules(context: ToolPermissionContext): PermissionRule[] {
  return PERMISSION_RULE_SOURCES.flatMap(source =>
    (context.alwaysDenyRules[source] || []).map(ruleString => ({
      source,
      ruleBehavior: 'deny',
      ruleValue: permissionRuleValueFromString(ruleString),
    })),
  )
}

// 收集所有 ask 规则
export function getAskRules(context: ToolPermissionContext): PermissionRule[] {
  return PERMISSION_RULE_SOURCES.flatMap(source =>
    (context.alwaysAskRules[source] || []).map(ruleString => ({
      source,
      ruleBehavior: 'ask',
      ruleValue: permissionRuleValueFromString(ruleString),
    })),
  )
}
```

工具匹配的核心是 `toolMatchesRule()` 函数，它处理了直接匹配和 MCP 服务器级别匹配：

```typescript
// src/utils/permissions/permissions.ts:238-269
function toolMatchesRule(
  tool: Pick<Tool, 'name' | 'mcpInfo'>,
  rule: PermissionRule,
): boolean {
  // 规则不能有内容——整个工具匹配只匹配 "Bash" 而非 "Bash(xxx)"
  if (rule.ruleValue.ruleContent !== undefined) {
    return false
  }

  const nameForRuleMatch = getToolNameForPermissionCheck(tool)

  // 直接工具名匹配
  if (rule.ruleValue.toolName === nameForRuleMatch) {
    return true
  }

  // MCP 服务器级别权限匹配：
  // 规则 "mcp__server1" 匹配工具 "mcp__server1__tool1"
  // 规则 "mcp__server1__*" 匹配 server1 的所有工具
  const ruleInfo = mcpInfoFromString(rule.ruleValue.toolName)
  const toolInfo = mcpInfoFromString(nameForRuleMatch)

  return (
    ruleInfo !== null &&
    toolInfo !== null &&
    (ruleInfo.toolName === undefined || ruleInfo.toolName === '*') &&
    ruleInfo.serverName === toolInfo.serverName
  )
}
```

### 2.5 规则优先级与评估顺序

规则的评估遵循**严格的优先级顺序**，在 `hasPermissionsToUseToolInner()` 函数（第 1158 行起）中实现。以下是评估的关键步骤：

```
规则评估优先级（从高到低）：
Step 1a: deny 规则（整个工具） —— 最高优先级
Step 1b: ask 规则（整个工具） —— 除非沙箱可自动允许
Step 1c: 工具自身的 checkPermissions() —— 内容级检查
Step 1d: 工具实现返回 deny —— 工具自主拒绝
Step 1e: requiresUserInteraction —— 即使 bypass 也要询问
Step 1f: 内容级 ask 规则 —— bypass-immune
Step 1g: 安全检查（safetyCheck） —— bypass-immune
Step 2a: bypassPermissions 模式 —— 跳过后续检查
Step 2b: allow 规则（整个工具） —— 通过规则
Step 3:  passthrough → ask 转换 —— 默认行为
```

`deny` 规则优先级最高（安全优先原则）——只要任何来源有一条 deny 规则匹配，工具调用立即被拒绝，后续步骤不再评估。所有来源的规则**合并评估**而非层级覆盖——用户设置中的 allow 规则不会覆盖策略设置中的 deny 规则。

特别值得注意的是步骤 1f 和 1g——它们是**bypass-immune**（绕过免疫）的。即使用户启用了 `bypassPermissions` 模式，内容级 ask 规则和安全检查仍然会强制弹出权限提示。这意味着 `Bash(git push:*)` 这样的用户自定义 ask 规则即使在 bypass 模式下也会被尊重——因为用户明确配置它就意味着用户认为这个操作需要额外审查。

### 2.6 内容级规则匹配

除了整个工具的匹配，规则还支持内容级匹配。以 Bash 工具为例，用户可以配置精确到命令级别的规则：

```json
{
  "permissions": {
    "allow": ["Bash(npm install)", "Bash(npm test:*)"],
    "deny": ["Bash(rm -rf:*)"],
    "ask": ["Bash(git push:*)"]
  }
}
```

其中 `:*` 后缀表示前缀匹配——`Bash(npm test:*)` 匹配所有以 `npm test` 开头的命令（如 `npm test`、`npm test -- --coverage`）。这种精细粒度的控制让用户可以在允许日常开发命令的同时，对危险命令保持警觉。

每个工具通过自身的 `checkPermissions()` 方法实现内容级规则匹配。BashTool 会解析命令字符串，提取子命令，然后逐一匹配规则。FileEditTool 会检查目标文件路径是否在受保护区域。AgentTool 会检查智能体类型是否被允许。

---

## 第三章：文件系统沙箱 src/utils/permissions/filesystem.ts

### 3.1 危险文件与目录保护

文件系统沙箱是权限系统中最精细的防御层。它定义了哪些文件和目录需要特殊保护：

```typescript
// src/utils/permissions/filesystem.ts:57-79
// 危险文件列表——可被用于代码执行或数据泄露
export const DANGEROUS_FILES = [
  '.gitconfig',      // Git 配置——可通过 hook 执行任意代码
  '.gitmodules',     // Git 子模块——可引入恶意仓库
  '.bashrc',         // Bash 配置——登录时自动执行
  '.bash_profile',   // Bash 登录配置
  '.zshrc',          // Zsh 配置——shell 启动时执行
  '.zprofile',       // Zsh 登录配置
  '.profile',        // 通用 shell 配置
  '.ripgreprc',      // ripgrep 配置——搜索时可执行
  '.mcp.json',       // MCP 服务器配置——可引入恶意服务
  '.claude.json',    // Claude Code 配置
] as const

// 危险目录——包含敏感配置或可执行文件
export const DANGEROUS_DIRECTORIES = [
  '.git',            // Git 内部数据——hooks/ 可执行代码
  '.vscode',         // VS Code 配置——tasks 可执行命令
  '.idea',           // JetBrains 配置
  '.claude',         // Claude Code 配置——settings, hooks
] as const
```

### 3.2 路径安全检查

文件系统沙箱的安全检查包含多层防护。首先是大小写规范化——防止在大小写不敏感的文件系统（macOS/Windows）上通过混合大小写绕过检查：

```typescript
// src/utils/permissions/filesystem.ts:90-92
// 规范化路径大小写以防止绕过攻击
// 例如 ".cLauDe/Settings.locaL.json" 试图绕过检查
export function normalizeCaseForComparison(path: string): string {
  return path.toLowerCase()
}
```

设置文件路径的检测展示了多层安全防护：

```typescript
// src/utils/permissions/filesystem.ts:200-222
export function isClaudeSettingsPath(filePath: string): boolean {
  // 安全措施：先展开路径结构，防止通过冗余 "./" 绕过
  // 例如 "./.claude/./settings.json" 会绕过 endsWith() 检查
  const expandedPath = expandPath(filePath)

  // 规范化大小写以防止绕过
  const normalizedPath = normalizeCaseForComparison(expandedPath)

  // 使用平台特定的路径分隔符
  if (
    normalizedPath.endsWith(`${sep}.claude${sep}settings.json`) ||
    normalizedPath.endsWith(`${sep}.claude${sep}settings.local.json`)
  ) {
    return true
  }
  // 检查当前项目的所有设置文件路径
  return getSettingsPaths().some(
    settingsPath =>
      normalizeCaseForComparison(settingsPath) === normalizedPath,
  )
}
```

### 3.3 工作目录边界强制

文件系统沙箱的一个核心功能是限制文件访问在工作目录范围内。当工具试图访问工作目录之外的文件时，系统会发出警告并要求用户确认。路径验证使用 POSIX 风格路径和 gitignore 模式匹配：

```typescript
// src/utils/permissions/filesystem.ts:170-179
// 跨平台相对路径计算，始终返回 POSIX 风格路径
export function relativePath(from: string, to: string): string {
  if (getPlatform() === 'windows') {
    // Windows 路径转换为 POSIX 以实现一致比较
    const posixFrom = windowsPathToPosixPath(from)
    const posixTo = windowsPathToPosixPath(to)
    return posix.relative(posixFrom, posixTo)
  }
  return posix.relative(from, to)  // Unix 直接使用 POSIX 路径
}
```

工作目录边界检查的核心逻辑是判断给定路径是否在工作目录内。如果路径的相对路径以 `..` 开头（即需要向上遍历才能到达），则该路径位于工作目录之外。系统还支持通过 `--add-dir` 参数或权限更新添加额外的工作目录，扩展允许访问的路径范围。

安全路径判定还考虑了多个特殊位置：
- **会话内存目录**（`getSessionMemoryDir()`）：允许读写当前会话的内存文件
- **计划文件**（`isSessionPlanFile()`）：允许读写当前会话的计划文件
- **工具结果目录**（`getToolResultsDir()`）：允许读取持久化的工具结果
- **CLAUDE.md 配置文件**（`isClaudeConfigFilePath()`）：需要特殊的 ask 处理

这些特殊路径的处理确保了系统功能正常运作的同时，不会无意中放开对其他敏感路径的访问。

### 3.4 Skill 作用域隔离

当 Claude 编辑 `.claude/skills/` 目录下的文件时，系统会提供更窄的权限建议——只允许编辑特定 skill，而非整个 `.claude/` 目录：

```typescript
// src/utils/permissions/filesystem.ts:101-157
export function getClaudeSkillScope(
  filePath: string,
): { skillName: string; pattern: string } | null {
  const absolutePath = expandPath(filePath)
  const absolutePathLower = normalizeCaseForComparison(absolutePath)

  // 检查项目级和全局级 skills 目录
  const bases = [
    { dir: expandPath(join(getOriginalCwd(), '.claude', 'skills')),
      prefix: '/.claude/skills/' },
    { dir: expandPath(join(homedir(), '.claude', 'skills')),
      prefix: '~/.claude/skills/' },
  ]

  for (const { dir, prefix } of bases) {
    const dirLower = normalizeCaseForComparison(dir)
    for (const s of [sep, '/']) {
      if (absolutePathLower.startsWith(dirLower + s.toLowerCase())) {
        const rest = absolutePath.slice(dir.length + s.length)
        // 提取 skill 名称
        const slash = rest.indexOf('/')
        const cut = /* ... 处理 Windows/Unix 路径分隔 ... */
        if (cut <= 0) return null
        const skillName = rest.slice(0, cut)
        // 安全检查：拒绝路径遍历和 glob 元字符
        if (!skillName || skillName === '.' || skillName.includes('..'))
          return null
        if (/[*?[\]]/.test(skillName)) return null
        // 返回 skill 名称和受限模式
        return { skillName, pattern: prefix + skillName + '/**' }
      }
    }
  }
  return null
}
```

这个函数展示了精细的安全设计——拒绝路径遍历（`..`）、拒绝 glob 元字符（`*?[]`，防止 `*` 目录名产生匹配所有 skill 的模式），并且在匹配时使用小写比较但保留原始大小写的 skill 名称。

---

## 第四章：拒绝追踪 src/utils/permissions/denialTracking.ts

### 4.1 分类器信心反馈回路

当 `auto` 模式使用 AI 分类器自动判断权限时，分类器可能会出错。拒绝追踪系统通过监控连续拒绝次数来检测分类器可能的过度保守行为：

```typescript
// src/utils/permissions/denialTracking.ts:1-45
// 拒绝追踪状态
export type DenialTrackingState = {
  consecutiveDenials: number  // 连续拒绝次数
  totalDenials: number        // 总拒绝次数
}

// 阈值常量
export const DENIAL_LIMITS = {
  maxConsecutive: 3,   // 连续 3 次拒绝后回退到手动提示
  maxTotal: 20,        // 累计 20 次拒绝后回退
} as const

// 创建初始状态（两个计数器都为 0）
export function createDenialTrackingState(): DenialTrackingState {
  return { consecutiveDenials: 0, totalDenials: 0 }
}

// 记录一次拒绝——两个计数器都递增
export function recordDenial(
  state: DenialTrackingState,
): DenialTrackingState {
  return {
    ...state,
    consecutiveDenials: state.consecutiveDenials + 1,
    totalDenials: state.totalDenials + 1,
  }
}

// 记录一次成功——重置连续拒绝计数器
export function recordSuccess(
  state: DenialTrackingState,
): DenialTrackingState {
  if (state.consecutiveDenials === 0) return state // 无需变更
  return { ...state, consecutiveDenials: 0 }
}

// 判断是否应该回退到手动提示
export function shouldFallbackToPrompting(
  state: DenialTrackingState,
): boolean {
  return (
    state.consecutiveDenials >= DENIAL_LIMITS.maxConsecutive ||
    state.totalDenials >= DENIAL_LIMITS.maxTotal
  )
}
```

### 4.2 反馈回路的工作机制

这个系统形成了一个精巧的反馈回路：

```
 ┌─────────────────────────────────────────────────────┐
 │                 auto 模式分类器判断                    │
 │                                                      │
 │  工具调用请求 ──→ 分类器判断 ──→ 允许? ──→ 执行工具    │
 │                      │              │                 │
 │                      │              │    recordSuccess │
 │                      │              │    连续拒绝归零   │
 │                      ↓              │                 │
 │                   拒绝?              │                 │
 │                      │              │                 │
 │                recordDenial         │                 │
 │               连续+1, 累计+1        │                 │
 │                      │              │                 │
 │                      ↓              │                 │
 │           连续≥3 或 累计≥20?        │                 │
 │              │           │          │                 │
 │              ↓           ↓          │                 │
 │            是          否           │                 │
 │              │           │          │                 │
 │              ↓           ↓          │                 │
 │         回退到手动    保持自动       │                 │
 │         用户提示      分类器        │                 │
 └─────────────────────────────────────────────────────┘
```

两个阈值的设计有不同含义：
- **连续 3 次**（`maxConsecutive: 3`）：检测分类器对当前操作类型的系统性误判——如果连续 3 次拒绝，很可能分类器无法正确理解用户意图
- **累计 20 次**（`maxTotal: 20`）：检测分类器整体的保守偏差——即使间歇性成功，如果累计拒绝过多，说明分类器的判断标准可能不适合当前工作流

`recordSuccess()` 只重置连续计数器而不重置累计计数器——这意味着一次成功可以打破连续拒绝的"恐慌"，但不会消除长期的保守倾向。

---

## 第五章：Hook 集成 src/utils/hooks/

### 5.1 Hook 系统概述

Hook 系统是权限系统的可扩展层。它允许用户和企业通过配置文件注册自定义脚本，在特定事件发生时执行。Hook 的类型定义位于 `src/types/hooks.ts`：

```typescript
// src/types/hooks.ts（概要）
// Hook 可以是以下类型之一：
// - 命令 Hook：执行 shell 脚本，捕获 stdout/stderr
// - HTTP Hook：发送 POST 请求，解析 JSON 响应
// - Agent Hook：调用 Claude API，获取结构化输出
// - Prompt Hook：显示交互式提示，获取用户输入
// - Callback Hook：SDK 注册的回调函数

// Hook 事件类型定义
export type HookEventName =
  | 'PreToolUse'        // 工具执行前
  | 'PostToolUse'       // 工具执行后（成功）
  | 'PostToolUseFailure'// 工具执行后（失败）
  | 'PermissionRequest' // 权限请求时
  | 'PermissionDenied'  // 权限被拒绝时
  | 'SessionStart'      // 会话开始
  | 'Setup'             // 初始化设置
  | 'SubagentStart'     // 子智能体启动
  | 'UserPromptSubmit'  // 用户提交输入
  | 'Elicitation'       // 信息获取
  | 'ConfigChange'      // 配置变更
  // ... 更多事件
```

### 5.2 PermissionRequest Hook 集成

权限系统与 Hook 系统的最重要集成点是 `PermissionRequest` 事件。当工具调用需要权限决策时，可以通过 Hook 来做出或影响决策：

```typescript
// src/utils/hooks.ts（概要，约第 4157-4192 行）
export async function* executePermissionRequestHooks<ToolInput>(
  toolName: string,
  toolUseID: string,
  toolInput: ToolInput,
  toolUseContext: ToolUseContext,
  permissionMode?: string,
  permissionSuggestions?: PermissionUpdate[],
  signal?: AbortSignal,
  timeoutMs: number = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
): AsyncGenerator<AggregatedHookResult> {
  // 构造 Hook 输入
  const hookInput: PermissionRequestHookInput = {
    hook_event_name: 'PermissionRequest',
    tool_name: toolName,          // 请求权限的工具名
    tool_input: toolInput,        // 工具的输入参数
    permission_suggestions: permissionSuggestions,  // 建议的权限更新
  }
  // 执行所有匹配的 Hook，异步迭代返回结果
  // ...
}
```

Hook 的响应可以包含权限决策：

```typescript
// Hook 输出中的权限决策（src/types/hooks.ts 概要）
// PreToolUse Hook 可以返回：
{
  decision: {
    behavior: 'allow' | 'deny',  // 允许或拒绝
    reason?: string,              // 原因说明
    updatedInput?: object,        // 可选：修改后的工具输入
  }
}
```

这意味着 Hook 不仅可以**拒绝**工具调用，还可以**修改**工具的输入参数后允许——例如，一个安全 Hook 可以在允许文件写入前自动删除敏感内容。

### 5.3 Hook 匹配与执行流程

Hook 通过 if/then 模式匹配来决定是否对某个事件触发。用户在 settings.json 中配置：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": { "tool_name": "Bash" },
        "hooks": [
          { "type": "command", "command": "python3 /path/to/security-check.py" }
        ]
      }
    ],
    "PermissionRequest": [
      {
        "matcher": { "tool_name": "FileEdit" },
        "hooks": [
          { "type": "http", "url": "https://internal-api/approve" }
        ]
      }
    ]
  }
}
```

匹配器（matcher）支持工具名称匹配。当事件触发时，系统遍历该事件类型下的所有 Hook 配置，只执行匹配成功的 Hook。

Hook 的执行有严格的超时控制（`TOOL_HOOK_EXECUTION_TIMEOUT_MS`，默认 10 分钟），防止 Hook 脚本挂起导致系统卡死。如果 Hook 超时或出错，系统会记录错误并继续执行后续决策步骤（fail-open 用于非权限 Hook，fail-closed 用于安全关键 Hook）。

ConfigChange Hook 有特殊处理——当配置变更来自企业策略（`policy_settings`）时，Hook 照常触发（用于审计日志），但**阻止结果被忽略**——企业策略的变更永远不会被用户 Hook 阻止：

```typescript
// src/utils/hooks.ts（概要，约第 4214-4239 行）
// 策略设置是企业管理的——Hook 触发用于审计，
// 但绝不阻止策略变更的应用
if (source === 'policy_settings') {
  return results.map(r => ({ ...r, blocked: false }))
}
```

### 5.4 Hook 事件广播系统

Hook 事件的广播通过 `hookEvents.ts` 管理：

```typescript
// src/utils/hooks/hookEvents.ts:18-20
// 始终发射的事件——即使 includeHookEvents 选项未开启
const ALWAYS_EMITTED_HOOK_EVENTS = ['SessionStart', 'Setup'] as const

// 最大待处理事件数——超过此数量时丢弃最旧的事件
// MAX_PENDING_EVENTS = 100
```

事件广播采用队列模式——如果还没有注册事件处理器，事件会被缓存（最多 100 条），等处理器注册后一次性刷新。这保证了启动阶段的事件不会丢失。

### 5.5 企业 Hook 管控

企业管理员可以通过 MDM 策略控制 Hook 行为：

```typescript
// src/utils/hooks/hooksConfigSnapshot.ts（概要）
// 是否只允许企业管理的 Hook
export function shouldAllowManagedHooksOnly(): boolean {
  const policySettings = getSettingsForSource('policySettings')
  // 如果策略设置了 allowManagedHooksOnly，则只使用管理的 Hook
  if (policySettings?.allowManagedHooksOnly === true) {
    return true
  }
  return false
}

// 是否完全禁用所有 Hook（包括管理的）
export function shouldDisableAllHooksIncludingManaged(): boolean {
  return getSettingsForSource('policySettings')?.disableAllHooks === true
}
```

这实现了三级控制：
1. **正常模式**：所有来源的 Hook 都可执行
2. **allowManagedHooksOnly**：只有企业策略中定义的 Hook 可执行，用户自定义 Hook 被忽略
3. **disableAllHooks**：完全禁用所有 Hook，包括企业管理的

---

## 第六章：权限初始化与企业管理

### 6.1 权限上下文初始化

权限上下文的初始化是一个复杂的多步骤过程，定义在 `permissionSetup.ts` 中：

```
权限初始化流程：
1. 解析 CLI 参数：--allowed-tools, --disallowed-tools, --base-tools
2. 从磁盘加载规则：loadAllPermissionRulesFromDisk()
3. 检查 Statsig 门控：bypassPermissions 是否可用
4. 检测危险权限：findDangerousClassifierPermissions()
5. 应用规则级联：applyPermissionRulesToPermissionContext()
6. 验证 --add-dir 目录（并行）
7. 返回上下文 + 警告 + 危险权限列表
```

### 6.2 危险权限检测

权限初始化的一个关键步骤是检测可能危及 auto 模式安全性的权限规则。`findDangerousClassifierPermissions()` 函数扫描所有规则和 CLI 参数，返回结构化的危险权限信息：

```typescript
// src/utils/permissions/permissionSetup.ts（概要）
type DangerousPermissionInfo = {
  ruleValue: PermissionRuleValue    // 危险规则的值
  source: PermissionRuleSource      // 规则来源
  ruleDisplay: string               // 显示格式，如 "Bash(*)"
  sourceDisplay: string             // 来源显示，如 "settings.json"
}
```

系统检测四类危险模式：(1) Bash 通配符和解释器规则——`Bash(*)`、`Bash(python:*)`、`Bash(node:*)` 等允许运行任意代码；(2) PowerShell 危险 cmdlet——`Invoke-Expression`、`Start-Process`、嵌套 shell 启动等；(3) Agent 通配符——`Agent(*)` 绕过分类器的子智能体评估；(4) Tmux send-keys——允许向其他终端会话注入任意命令。

进入 `auto` 模式时，系统会自动检测并剥离这些危险权限规则：

```typescript
// src/utils/permissions/permissionSetup.ts:94-147（概要）
export function isDangerousBashPermission(
  toolName: string,
  ruleContent: string | undefined,
): boolean {
  if (toolName !== BASH_TOOL_NAME) return false

  // 工具级别 allow（允许所有命令）——危险！
  if (!ruleContent) return true

  // 解释器前缀规则（允许运行任意 Python/Node/Ruby 代码）——危险！
  const interpreterPatterns = [
    'python:', 'node:', 'ruby:', 'perl:', 'php:', 'go:',
  ]
  for (const pattern of interpreterPatterns) {
    if (ruleContent === `${pattern}*` ||
        ruleContent.startsWith(`${pattern} `)) {
      return true
    }
  }

  return false
}
```

`stripDangerousPermissionsForAutoMode()` 在进入 auto 模式时移除这些危险规则，并将它们暂存在 `strippedDangerousRules` 中，退出 auto 模式时恢复。

### 6.3 MDM 策略集成

企业可以通过 Mobile Device Management（MDM）系统管理 Claude Code 的权限策略。MDM 设置按平台从不同位置读取：

```
MDM 设置来源（按优先级）：
┌──────────────────────────────────────────────────────────┐
│ macOS:                                                    │
│   1. /Library/Managed Preferences/{user}/                │
│      com.anthropic.claudecode.plist     （每用户策略）     │
│   2. /Library/Managed Preferences/                       │
│      com.anthropic.claudecode.plist     （设备级策略）     │
│                                                          │
│ Windows:                                                  │
│   1. HKLM\SOFTWARE\Policies\ClaudeCode （管理员级，最高） │
│   2. HKCU\SOFTWARE\Policies\ClaudeCode （用户级，最低）   │
│                                                          │
│ Linux:                                                    │
│   1. /etc/claude-code/managed-settings.json              │
│   2. /etc/claude-code/managed-settings.d/*.json （覆盖） │
│                                                          │
│ 优先级原则："第一个来源获胜"                                │
│   remote (SDK) > HKLM/plist > managed-settings >         │
│   HKCU > user/project/local settings                     │
└──────────────────────────────────────────────────────────┘
```

### 6.4 企业权限规则加载

企业可以通过 `allowManagedPermissionRulesOnly` 策略完全控制权限规则来源：

```typescript
// src/utils/permissions/permissionsLoader.ts:120-133
export function loadAllPermissionRulesFromDisk(): PermissionRule[] {
  // 如果启用了 "仅使用企业管理的权限规则"
  if (shouldAllowManagedPermissionRulesOnly()) {
    // 只加载策略来源的规则，忽略用户/项目/本地设置
    return getPermissionRulesForSource('policySettings')
  }

  // 正常模式：从所有启用的来源加载规则
  const rules: PermissionRule[] = []
  for (const source of getEnabledSettingSources()) {
    rules.push(...getPermissionRulesForSource(source))
  }
  return rules
}
```

当 `allowManagedPermissionRulesOnly` 启用时：
- 只有 `policySettings` 中的规则被加载
- 用户设置、项目设置、本地设置中的权限规则被完全忽略
- "Always Allow" 选项在 UI 中被隐藏（`shouldShowAlwaysAllowOptions()` 返回 `false`）

### 6.5 SDK 权限上下文与嵌套智能体

当 Claude Code 作为 SDK 被嵌入其他应用时，权限上下文需要特殊处理。SDK 调用者可以通过 `ToolPermissionContext` 预设权限规则，子智能体通过 `createSubagentContext()` 继承并限制父级的权限上下文。

嵌套智能体的权限遵循**最小权限原则**——子智能体的权限只能是父级权限的子集，永远不能超越父级。`shouldAvoidPermissionPrompts` 标记告诉权限系统当前上下文没有交互式用户，需要权限的操作应该直接拒绝而非弹出提示。

### 6.6 Bypass Permissions 远程熔断

`bypassPermissions` 模式可以通过远程配置（Statsig 门控）被紧急禁用：

```typescript
// src/utils/permissions/bypassPermissionsKillswitch.ts（概要）
export async function checkAndDisableBypassPermissionsIfNeeded(
  toolPermissionContext: ToolPermissionContext,
  setAppState: (f: (prev: AppState) => AppState) => void,
): Promise<void> {
  // 只在首次查询前检查一次
  if (bypassPermissionsCheckRan) return
  bypassPermissionsCheckRan = true

  // 如果 bypass 模式不可用则跳过
  if (!toolPermissionContext.isBypassPermissionsModeAvailable) return

  // 检查远程门控是否要求禁用 bypass
  const shouldDisable = await shouldDisableBypassPermissions()
  if (!shouldDisable) return

  // 禁用 bypass 模式——更新应用状态
  setAppState(prev => ({
    ...prev,
    toolPermissionContext: createDisabledBypassPermissionsContext(
      prev.toolPermissionContext,
    ),
  }))
}
```

这个熔断机制允许 Anthropic 在发现安全问题时远程禁用所有用户的 bypass 模式，无需等待用户更新客户端。

---

## 第七章：ML 分类器 (auto mode)

### 7.1 分类器概述

`auto` 模式使用 AI 分类器（称为 YOLO Classifier）来自动判断工具调用是否安全。分类器定义在 `yoloClassifier.ts`（约 1,495 行）中，通过 `TRANSCRIPT_CLASSIFIER` Feature Flag 门控。

分类器的核心思路是：将当前对话记录和工具调用请求发送给一个**独立的 Claude 模型实例**，让它判断该操作是否应该被阻止。

### 7.2 两阶段分类策略

分类器采用两阶段策略来平衡速度和准确性：

```
两阶段分类流程：
┌───────────────────────────────────────────────────────┐
│                                                        │
│  工具调用请求                                           │
│       │                                                │
│       ↓                                                │
│  ┌──────────────────┐                                  │
│  │ 阶段 1（快速判断）│                                  │
│  │ 无 thinking       │                                  │
│  │ 偏向阻止         │                                  │
│  │ "有疑问就阻止"    │                                  │
│  └────────┬─────────┘                                  │
│           │                                            │
│       判断结果                                          │
│       ╱    ╲                                           │
│    允许     阻止                                        │
│     │       │                                          │
│     ↓       ↓                                          │
│  立即返回  ┌──────────────────┐                         │
│  （快速    │ 阶段 2（深度推理）│                         │
│   路径）   │ 有 thinking       │                         │
│           │ 更仔细的分析      │                         │
│           │ 链式推理          │                         │
│           └────────┬─────────┘                         │
│                    │                                   │
│                判断结果                                  │
│                ╱    ╲                                   │
│             允许     阻止                               │
│              │       │                                 │
│              ↓       ↓                                 │
│           执行工具  拒绝并                               │
│                   返回原因                              │
└───────────────────────────────────────────────────────┘
```

**阶段 1（快速判断）**：不使用 thinking，系统提示后缀为"有疑问就阻止"。如果判断为**允许**，立即返回（快速路径）；如果判断为**阻止**，进入阶段 2。

**阶段 2（深度推理）**：使用链式思维（chain-of-thought），通过 XML 标签（`<thinking>`、`<block>`、`<reason>`）进行结构化推理。这个阶段给分类器更多空间来分析上下文，减少误判。

### 7.3 对话记录构建

分类器需要理解工具调用的上下文，因此会构建精简的对话记录：

```typescript
// src/utils/permissions/yoloClassifier.ts（概要，约第 302-360 行）
// 将 Message[] 转换为精简的 TranscriptEntry[]
export function buildTranscriptEntries(
  messages: Message[],
): TranscriptEntry[] {
  // 只保留用户文本和助手的工具调用
  // 过滤掉系统消息、工具结果等
  // 目的：给分类器提供足够的上下文，同时控制 token 用量
}
```

### 7.4 系统提示与用户规则

分类器的系统提示会融入用户自定义的 auto mode 规则：

```typescript
// src/utils/permissions/yoloClassifier.ts（概要，约第 484-540 行）
export function buildYoloSystemPrompt(
  context: ToolPermissionContext,
): string {
  // 从模板文件加载基础系统提示
  let prompt = BASE_PROMPT

  // 注入用户的 allow/deny 规则
  // 来自 settings.json 的 autoMode 配置
  // 例如：用户可以配置 "在这个项目中允许 npm publish"

  return prompt
}
```

### 7.5 分类器结果解析

分类器使用 XML 格式输出，通过专门的解析函数提取决策：

```typescript
// src/utils/permissions/yoloClassifier.ts（概要，约第 578-596 行）
// 去除 thinking 标签以避免误匹配
function stripThinking(text: string): string { /* ... */ }

// 解析 <block>yes/no</block> 标签
function parseXmlBlock(text: string): boolean | null {
  // yes = 阻止, no = 允许, null = 解析错误
}

// 提取 <reason>...</reason> 内容
function parseXmlReason(text: string): string | null { /* ... */ }
```

### 7.6 acceptEdits 快速路径

在运行昂贵的分类器 API 调用之前，auto 模式会先检查 `acceptEdits` 模式是否会允许该操作。如果 `acceptEdits` 也会允许（如文件编辑操作），则直接允许而无需调用分类器——这是一个重要的性能优化：

```typescript
// src/utils/permissions/permissions.ts:593-610（概要）
// 在运行 auto 模式分类器之前，检查 acceptEdits 模式是否会允许
// 这避免了对安全操作（如工作目录内的文件编辑）的昂贵分类器 API 调用
// 注意：Agent 和 REPL 工具被排除——
// 它们的 checkPermissions 在 acceptEdits 模式下返回 'allow'，
// 这会默默绕过分类器。REPL 代码可能包含 VM 逃逸，
// 分类器必须看到完整的 JavaScript 代码
if (
  result.behavior === 'ask' &&
  tool.name !== AGENT_TOOL_NAME &&
  tool.name !== REPL_TOOL_NAME
) {
  const acceptEditsResult = await tool.checkPermissions(parsedInput, {
    ...context,
    getAppState: () => ({
      ...appState,
      toolPermissionContext: {
        ...appState.toolPermissionContext,
        mode: 'acceptEdits',  // 模拟 acceptEdits 模式
      },
    }),
  })
  if (acceptEditsResult.behavior === 'allow') {
    return { behavior: 'allow', /* ... */ }  // 跳过分类器
  }
}
```

这里排除 Agent 和 REPL 工具的原因值得深思——REPL 工具可以执行任意 JavaScript 代码，其中可能包含 VM 沙箱逃逸。如果使用 acceptEdits 快速路径，这些代码会绕过分类器的安全审查。

### 7.7 分类器熔断机制

分类器还有一个 30 分钟的熔断刷新机制：

```typescript
// src/utils/permissions/permissions.ts:107
const CLASSIFIER_FAIL_CLOSED_REFRESH_MS = 30 * 60 * 1000 // 30 分钟
```

如果分类器连续失败或超时，系统会在 30 分钟内将所有请求回退到手动提示模式（fail-closed 原则），之后再尝试重新启用分类器。

### 7.8 安全考量

auto 模式的安全设计有几个关键考量：

1. **危险权限剥离**：进入 auto 模式时，`stripDangerousPermissionsForAutoMode()` 会自动移除可能绕过分类器的规则（如 `Bash(*)`、`Bash(python:*)`、`Agent(*)`）。这些规则被暂存，退出 auto 模式时恢复。

2. **不可分类器审批的安全检查**：某些安全检查（如修改 `.git/hooks/`）标记为 `classifierApprovable: false`，即使在 auto 模式下也会强制弹出用户提示，分类器无权批准。

3. **PowerShell 特殊处理**：除非启用了 `POWERSHELL_AUTO_MODE` Feature Flag（仅内部构建），PowerShell 命令在 auto 模式下始终需要用户确认，因为 PowerShell 的 `Invoke-Expression` 等命令可以执行任意代码，而分类器对 PowerShell 的理解不如 Bash 深入。

4. **异步子智能体的拒绝追踪**：异步子智能体的 `setAppState` 是空操作（no-op），因此使用 `context.localDenialTracking` 替代全局状态，确保每个子智能体有独立的拒绝追踪。

---

## 第八章：权限决策流程图

### 8.1 完整决策流程

以下是从工具调用请求到最终权限决策的完整流程，对应 `permissions.ts` 中 `hasPermissionsToUseToolInner()` 函数（约第 1158-1319 行）的逻辑：

```
工具调用请求（tool, input, context）
│
├── 1a. 整个工具被 deny 规则匹配？
│   └── 是 → 返回 DENY（type: rule）
│
├── 1b. 整个工具有 ask 规则？
│   └── 是 → 沙箱自动允许？
│       ├── 是（sandboxed Bash）→ 继续到 1c
│       └── 否 → 返回 ASK（type: rule）
│
├── 1c. 工具自身的 checkPermissions()
│   │   （每个工具实现自己的权限检查逻辑）
│   │   BashTool: 解析子命令，逐一匹配规则
│   │   FileEditTool: 检查路径安全性
│   │   AgentTool: 检查智能体类型规则
│   │
│   ├── 1d. 返回 deny？→ 返回 DENY
│   │
│   ├── 1e. 工具需要用户交互？（requiresUserInteraction）
│   │   └── 是 + ask → 返回 ASK（即使 bypass 模式也要询问）
│   │
│   ├── 1f. 内容级 ask 规则匹配？（如 Bash(npm publish:*)）
│   │   └── 是 → 返回 ASK（即使 bypass 模式也要询问）
│   │
│   └── 1g. 安全检查触发？（.git/, .claude/, shell 配置）
│       └── 是 → 返回 ASK（bypass-immune，不可绕过）
│
├── 2a. bypassPermissions 模式？
│   │   （或 plan 模式 + isBypassPermissionsModeAvailable）
│   └── 是 → 返回 ALLOW（type: mode）
│
├── 2b. 整个工具有 allow 规则？
│   └── 是 → 返回 ALLOW（type: rule）
│
├── 3. passthrough → 转换为 ask
│
├── 外层 hasPermissionsToUseTool() 继续处理：
│
├── 结果是 allow？
│   └── 是 + auto 模式 → recordSuccess()，重置连续拒绝
│
├── 结果是 ask？
│   │
│   ├── dontAsk 模式？→ 转换为 DENY
│   │
│   ├── auto 模式（TRANSCRIPT_CLASSIFIER）？
│   │   │
│   │   ├── 不可分类器审批的 safetyCheck？
│   │   │   └── DENY（或保持 ASK 如果可交互）
│   │   │
│   │   ├── 工具需要用户交互？→ 保持 ASK
│   │   │
│   │   ├── acceptEdits 快速路径检查
│   │   │   （Agent 和 REPL 工具除外）
│   │   │   └── acceptEdits 会允许？→ ALLOW（跳过分类器）
│   │   │
│   │   ├── 拒绝追踪检查
│   │   │   └── shouldFallbackToPrompting？→ 保持 ASK
│   │   │
│   │   └── 运行 YOLO 分类器
│   │       ├── 允许 → ALLOW + recordSuccess()
│   │       └── 阻止 → recordDenial()
│   │           ├── 现在 shouldFallback？→ ASK（回退到提示）
│   │           └── 否 → DENY
│   │
│   └── 其他模式 → 返回 ASK（弹出交互式提示）
│
└── 结果是 deny？→ 返回 DENY
```

这个流程图中有几个关键设计决策值得强调：

**deny 优先于一切**（步骤 1a）：在评估的最开始就检查 deny 规则。如果一个工具被拒绝，后续的所有检查都不需要执行，这既是安全保证也是性能优化。

**安全检查是 bypass-immune 的**（步骤 1g）：即使用户选择了 `bypassPermissions` 模式，修改 `.git/hooks/`、`.bashrc`、`.claude/settings.json` 等危险文件仍然需要确认。这是不可协商的安全边界——这些文件的修改可能导致代码执行或配置篡改，风险太高以至于任何模式都不应该自动允许。

**auto 模式的多层检查**：auto 模式不是简单地调用分类器。它首先检查 safetyCheck 是否可以被分类器审批、然后检查工具是否需要用户交互、接着尝试 acceptEdits 快速路径、然后检查拒绝追踪阈值，最后才调用分类器。每一层都是一个安全防线。

**dontAsk 的最终转换**：`dontAsk` 模式的处理不在 `hasPermissionsToUseToolInner()` 内部，而是在外层的 `hasPermissionsToUseTool()` 中——它等待内部逻辑返回 `ask` 后，将其转换为 `deny`。这种设计保证了 dontAsk 不会影响规则匹配逻辑的正确性。

### 8.2 权限决策的原子性保证

在异步多智能体场景中，多个权限请求可能同时到达。`createResolveOnce()` 模式防止竞态条件：

```typescript
// src/hooks/toolPermission/PermissionContext.ts:75-94
export function createResolveOnce<T>(
  resolve: (value: T) => void,
): ResolveOnce<T> {
  let claimed = false    // 是否已被认领
  let delivered = false  // 是否已交付结果

  return {
    resolve(value: T) {
      if (delivered) return   // 已交付则忽略
      delivered = true
      claimed = true
      resolve(value)
    },
    isResolved() {
      return claimed
    },
    claim() {
      if (claimed) return false  // 原子性认领：只有第一个成功
      claimed = true
      return true
    },
  }
}
```

`claim()` 方法实现了"先到先得"的原子认领——在多个异步权限处理器（Coordinator、Swarm、Bash Classifier、Interactive）竞争时，只有第一个成功 `claim()` 的处理器可以做出权限决策。

### 8.3 四级权限处理器竞争

当权限决策为 `ask` 时，`useCanUseTool` Hook 会启动最多四个处理器并行竞争：

```
ask 决策的处理器竞争：
┌─────────────────────────────────────────────────────┐
│                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐│
│  │ Coordinator  │  │   Swarm     │  │    Bash      ││
│  │ (Hooks +     │  │ (转发给     │  │  Classifier  ││
│  │  Classifier) │  │  Leader)    │  │ (2s 超时)    ││
│  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘│
│         │                │                │         │
│         └────────┬───────┴────────┬───────┘         │
│                  │                │                  │
│                  ↓                ↓                  │
│           claim() 竞争      ┌──────────────┐        │
│           第一个成功的       │ Interactive  │        │
│           处理器获胜         │ (对话框 +    │        │
│                             │  Bridge +    │        │
│                             │  Channel)    │        │
│                             └──────────────┘        │
│                                                      │
│  如果前三个都未在超时内响应，Interactive 处理器        │
│  会弹出用户交互提示                                   │
└─────────────────────────────────────────────────────┘
```

---

## 设计哲学分析

权限系统是 Claude Code 中最集中体现安全设计理念的子系统。以下分析它如何体现和交织了系列文档中的 10 大设计哲学。

### Safety-First Design（安全优先）与 Progressive Trust Model（渐进信任）

权限系统是 **Safety-First Design** 的最纯粹表达。系统的默认立场是**拒绝**——`default` 模式下每一次工具调用都需要用户明确授权。这不是为了制造不便，而是一个深思熟虑的安全基线：当 AI 系统可以执行 Shell 命令、修改文件、甚至创建子智能体时，默认拒绝是防止意外损害的最后屏障。

五种权限模式形成了一个精心设计的**信任阶梯**：

```
信任阶梯（从低到高）：
dontAsk → default → plan → acceptEdits → auto → bypassPermissions
（拒绝全部） （逐一确认）（先审查）（信任编辑）（AI 判断）（完全信任）
```

每一步的提升都是**有意识的决策**——用户必须主动切换模式（`Shift+Tab`），不存在自动升级。而且信任是**可撤回**的——随时可以切回更严格的模式。这体现了"信任是被赚取的，而不是被假设的"的核心理念。

### Human-in-the-Loop（人在回路）

交互式权限提示是 **Human-in-the-Loop** 的最纯粹形式。在 `default` 模式下，每次工具调用都会暂停执行并等待用户确认——这保证了人类始终在决策回路中。即使在 `bypassPermissions` 模式下，安全检查（步骤 1g）仍然会弹出提示——对 `.git/`、`.claude/`、shell 配置文件的修改**永远**需要用户确认。

拒绝追踪系统是另一个人在回路的表现——当 AI 分类器连续拒绝 3 次后，系统自动回退到人工确认，承认 AI 的判断可能存在偏差，将决策权交还给人类。

### Isolation & Containment（隔离与遏制）

文件系统沙箱体现了 **Isolation & Containment** 的思想。`DANGEROUS_FILES` 和 `DANGEROUS_DIRECTORIES` 定义了一个"不可触碰"的区域——即使在最宽松的权限模式下，修改 `.gitconfig`、`.bashrc`、`.zshrc` 这些可以执行任意代码的文件仍然需要明确授权。

路径规范化是隔离的技术实现——`normalizeCaseForComparison()` 防止通过大小写混合绕过检查，`expandPath()` 防止通过 `./` 冗余路径绕过，`containsPathTraversal()` 防止通过 `..` 逃逸工作目录边界。这些都是"纵深防御"的具体体现。

Skill 作用域隔离（`getClaudeSkillScope()`）展示了最小权限原则——编辑一个 skill 不应该获得整个 `.claude/` 目录的写入权限。

### Extensibility Without Modification（无需修改的可扩展性）

Hook 系统是权限系统中 **Extensibility Without Modification** 的体现。企业和用户可以通过配置 Hook 来注入自定义安全策略——审计日志、合规检查、自定义审批流程——而无需修改 Claude Code 的源代码。

规则引擎本身也体现了这一原则：规则来自 8 个不同来源（userSettings、projectSettings、localSettings、flagSettings、policySettings、cliArg、command、session），新的安全策略可以通过添加规则来实施，而非修改代码。

### Composability（可组合性）

规则引擎的三种行为（allow/deny/ask）来自 8 个来源的规则可以自由组合，形成灵活的安全策略。MCP 工具的服务器级别权限匹配（规则 `mcp__server1` 匹配所有 `mcp__server1__*` 工具）展示了规则的层次化组合能力。

权限决策的多级处理器竞争（Coordinator → Swarm → Bash Classifier → Interactive）也是可组合的——不同的决策策略可以并行工作，通过 `createResolveOnce()` 的原子认领机制组合为一个统一的决策。

### Defensive Programming（防御性编程）

权限系统充满了防御性编程的实践：

- **Fail-closed 原则**：分类器失败时默认拒绝（而非默认允许）
- **路径规范化**：多层防护（大小写、路径遍历、冗余分隔符）
- **转义处理**：规则内容的括号转义防止解析注入
- **UNC 路径保护**：防止 NTLM 凭证泄露
- **glob 元字符拒绝**：skill 名称中的 `*` 不会变成匹配所有 skill 的通配符

每一层防护都针对具体的攻击向量，而非盲目地添加检查。

### Graceful Degradation（优雅降级）

权限系统的多个降级路径保证了即使部分组件失败，系统仍然安全可用：

- 分类器失败 → 回退到手动提示（而非拒绝所有操作）
- 连续拒绝过多 → 回退到手动提示（而非卡住）
- MDM 读取超时（5 秒） → 使用默认策略
- Hook 执行失败 → 跳过 Hook，继续到后续决策步骤
- 远程配置不可用 → 使用本地缓存

所有降级路径都遵循 fail-closed 原则——降级到更安全的状态，而非更宽松的状态。

### Context Window Economics（上下文窗口经济学）

分类器的对话记录构建（`buildTranscriptEntries()`）体现了上下文窗口经济学——只发送必要的对话内容（用户文本和助手的工具调用），过滤掉系统消息和工具结果，在保证分类准确性的同时控制 token 用量。两阶段分类策略同样是经济性考虑——大部分安全的操作在阶段 1 就被快速允许，只有可疑操作才进入更昂贵的阶段 2。acceptEdits 快速路径更是经济性的极致——在已知安全的操作上完全跳过分类器 API 调用。

### Performance-Conscious Startup（性能敏感启动）

权限初始化的设计也体现了性能意识。MDM 设置的读取（通过 `startMdmSettingsLoad()` 启动）在启动初期就触发异步加载，超时限制为 5 秒——如果企业管理服务器响应慢，系统不会因此卡住。`--add-dir` 目录的验证使用并行处理（`Promise.all`），确保多个额外工作目录同时验证。GrowthBook Feature Gate 的值使用缓存读取（`CACHED_MAY_BE_STALE`），避免在每次权限检查时等待远程配置的网络请求。

---

## 关键要点总结

1. **五种权限模式**形成渐进式信任阶梯：`default → acceptEdits → plan → bypassPermissions → auto`，每一步都需要用户有意识地选择
2. **规则引擎**支持 8 个来源的 allow/deny/ask 规则，deny 优先级最高（安全优先），规则可以匹配整个工具或特定内容
3. **文件系统沙箱**通过多层路径验证（大小写规范化、路径遍历检查、危险文件保护）保护敏感文件
4. **拒绝追踪**通过连续/累计计数器形成反馈回路，防止分类器过度保守
5. **Hook 集成**允许通过配置注入自定义安全策略，支持企业级管控
6. **MDM 策略**支持跨平台企业管理，提供三级 Hook 控制和权限规则锁定
7. **ML 分类器**使用两阶段策略（快速判断 + 深度推理），在速度和准确性间取平衡
8. **权限决策流程**包含 7 个检查步骤和多个模式转换，安全检查是 bypass-immune 的

---

## 下一篇预览

在理解了权限系统如何控制"谁能做什么"之后，下一篇文档（Doc 9：状态管理）将深入 Claude Code 的状态管理架构——AppState、Store 模式、消息系统和数据流。状态管理是连接 UI 层、查询引擎和权限系统的"胶水层"，它决定了数据如何在整个系统中流转和持久化。
