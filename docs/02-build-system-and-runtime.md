# Doc 2: 构建系统与运行时

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）
>
> 在理解了项目的宏观架构之后，我们需要深入理解它的"基础设施"——代码是如何从 TypeScript 源码变成可执行程序的？运行时环境是什么？哪些开关控制着不同版本的构建产物？本文档将回答这些问题，为后续理解具体模块的运行机制打下基础。

---

## 第一章：Bun 运行时介绍

### 1.1 什么是 Bun

Bun 是一个现代化的 JavaScript/TypeScript 运行时，由 Jarred Sumner 创建，用 Zig 语言编写。它的定位是 Node.js 的高性能替代品，但不仅仅是一个运行时——它同时集成了打包器（bundler）、包管理器（package manager）和测试运行器（test runner），形成一个"全合一"的开发工具链。

Claude Code 选择 Bun 作为运行时和构建工具，这是一个影响整个项目架构的关键技术决策。

### 1.2 Bun vs Node.js 对比

| 特性 | Bun | Node.js |
|------|-----|---------|
| **启动速度** | ~4 倍快于 Node.js | 基准线 |
| **TypeScript 支持** | 原生支持，无需编译步骤 | 需要 `tsc` 或 `ts-node` 等工具 |
| **打包器** | 内置 `Bun.build()` / `bun:bundle` | 需要 webpack / esbuild / Rollup 等 |
| **包管理器** | 内置（兼容 npm） | 需要 npm / yarn / pnpm |
| **测试运行器** | 内置 `bun test` | 需要 Jest / Vitest / Mocha 等 |
| **底层引擎** | JavaScriptCore（Safari 的 JS 引擎） | V8（Chrome 的 JS 引擎） |
| **编写语言** | Zig + C++ | C++ |
| **单文件可执行** | 原生支持 `bun build --compile` | 需要 pkg / nexe 等工具 |

### 1.3 为什么 Claude Code 选择 Bun

对于一个 CLI 工具来说，**启动速度**是至关重要的用户体验指标。用户每次在终端输入 `claude` 命令时，他们期望几乎瞬间得到响应。Bun 的启动速度优势（约 4 倍于 Node.js）直接改善了这个体验。

此外，Bun 的"全合一"特性意味着项目不需要维护一个复杂的构建工具链——不需要单独配置 webpack 或 esbuild，不需要 `tsconfig.json` 来驱动编译，不需要 Jest 来运行测试。一切都由 Bun 统一处理。

### 1.4 Bun 的关键特性在代码中的体现

#### 运行时检测

项目需要在某些场景下检测自己是否运行在 Bun 环境中：

```typescript
// src/utils/bundledMode.ts（第 7-10 行）
export function isRunningWithBun(): boolean {
  // process.versions.bun 存在时，说明当前进程由 Bun 运行
  // 无论是直接运行 .ts 文件还是运行编译后的可执行文件
  return process.versions.bun !== undefined
}
```

#### 单文件可执行检测

Bun 支持将整个项目编译为单个可执行文件（standalone executable），其中可以嵌入静态资源：

```typescript
// src/utils/bundledMode.ts（第 16-22 行）
export function isInBundledMode(): boolean {
  return (
    typeof Bun !== 'undefined' &&          // 确认 Bun 全局对象存在
    Array.isArray(Bun.embeddedFiles) &&     // 检查嵌入文件 API 是否可用
    Bun.embeddedFiles.length > 0           // 确认有嵌入的文件（编译后才有）
  )
}
```

这个检测函数用于区分两种运行模式：
- **开发模式**：直接用 `bun run src/entrypoints/cli.tsx` 执行源码
- **发布模式**：运行 `bun build --compile` 产生的单文件可执行程序（包含嵌入资源）

#### `bun:bundle` 模块

Bun 提供了一个特殊的内置模块 `bun:bundle`，它在构建时提供编译期功能。这个模块不是一个普通的 JavaScript 模块——它是 Bun 打包器的虚拟模块（virtual module），只在构建时有意义。Claude Code 中最重要的用法是 `feature()` 函数，我们将在第三章详细介绍。

```typescript
// src/entrypoints/cli.tsx（第 1 行）
import { feature } from 'bun:bundle'  // Bun 打包器内置模块（虚拟模块，构建时解析）
```

理解 `bun:bundle` 的关键在于：它的导出值不是运行时计算的，而是在**构建阶段**被 Bun 打包器**静态替换**的。这使得 JavaScript 引擎可以在编译 JIT 阶段就确定代码路径，完全消除运行时开销。

#### 启动性能优化链

选择 Bun 带来的性能优势不只是"启动快"这么简单。Claude Code 围绕 Bun 的特性构建了一整条启动优化链：

```
Bun 冷启动（~50ms）
  → 零编译步骤（原生 TS 支持）
    → feature() 编译时常量折叠（减少运行时判断）
      → MACRO 内联（零 I/O 版本查询）
        → 条件 require()（按需加载模块）
          → 动态 import()（延迟初始化）
```

每一层优化都依赖前一层的基础，而 Bun 的选择是这整条链的根基。如果使用 Node.js，就需要额外的 TypeScript 编译步骤；如果使用 webpack 而非 Bun 打包器，就无法使用 `bun:bundle` 的 `feature()` 函数——整个构建架构都需要重新设计。

### 1.5 构建时宏替换：MACRO 系统

除了 `feature()` 函数，Bun 打包器还支持**构建时宏替换**——在打包阶段将代码中的特定标识符替换为实际值。Claude Code 使用一个名为 `MACRO` 的全局对象来承载这些构建时常量：

```typescript
// src/utils/permissions/filesystem.ts（第 51 行）
declare const MACRO: { VERSION: string }  // TypeScript 类型声明：告诉编译器 MACRO 存在
```

`MACRO` 对象包含以下构建时常量：

| 常量 | 用途 | 使用示例 |
|------|------|---------|
| `MACRO.VERSION` | 应用版本号（如 `1.0.32`） | 启动时显示版本、更新检查 |
| `MACRO.BUILD_TIME` | 构建时间戳 | `--version` 命令输出 |
| `MACRO.PACKAGE_URL` | npm 包地址 | 自动更新器拉取新版本 |
| `MACRO.NATIVE_PACKAGE_URL` | 原生二进制包地址 | 原生安装器使用 |
| `MACRO.ISSUES_EXPLAINER` | 问题报告帮助文本 | 错误提示中的引导信息 |
| `MACRO.FEEDBACK_CHANNEL` | 反馈渠道信息 | 用户反馈引导 |
| `MACRO.VERSION_CHANGELOG` | 版本更新日志 | 版本更新提示 |

实际使用示例——零开销的版本查询：

```typescript
// src/entrypoints/cli.tsx（第 36-42 行）
// --version 的快速路径：零模块加载
if (args.length === 1 && (args[0] === '--version' || args[0] === '-v' || args[0] === '-V')) {
  // MACRO.VERSION 在构建时被替换为实际的版本字符串
  // 这个代码路径不需要加载任何其他模块，实现了最快的响应
  console.log(`${MACRO.VERSION} (Claude Code)`)
  return
}
```

`MACRO.VERSION` 在打包阶段被 Bun 替换为一个字符串字面量（如 `"1.0.32"`），因此运行时没有任何查找开销。

---

## 第二章：项目配置文件详解

### 2.1 特殊的项目结构

需要特别说明的是，本仓库中的源代码是**从 npm 发布包的 Source Map 中提取的源码快照**，而非 Anthropic 内部的完整开发仓库。因此，传统的配置文件（如 `tsconfig.json`、`package.json`、`bunfig.toml`）并不存在于本快照中。

但是，通过分析代码中的导入路径、类型声明和构建产物引用，我们可以还原出这些配置的关键设置。

### 2.2 TypeScript 配置还原

从代码特征可以推断出以下 TypeScript 配置：

```jsonc
// 推断的 tsconfig.json 关键配置
{
  "compilerOptions": {
    // 严格模式——代码中大量使用了严格类型检查特性
    "strict": true,

    // 模块系统：ESNext + NodeNext 解析
    // 证据：所有导入都使用 .js 后缀（import ... from './foo.js'）
    // 这是 NodeNext 模块解析的要求
    "module": "ESNext",
    "moduleResolution": "NodeNext",

    // 路径别名：src/ 目录可以用绝对路径导入
    // 证据：import { logEvent } from 'src/services/analytics/index.js'
    "paths": {
      "src/*": ["./src/*"]
    },

    // JSX 支持：项目大量使用 .tsx 文件
    "jsx": "react-jsx",

    // 目标输出：现代 JavaScript
    "target": "ESNext"
  }
}
```

**关键观察——`.js` 后缀导入模式：**

你会注意到整个代码库中，TypeScript 文件之间的导入都使用 `.js` 后缀而不是 `.ts`：

```typescript
// src/setup.ts（第 8 行）
import { logEvent } from 'src/services/analytics/index.js'  // .js 后缀
//                                                    ^^^
// 尽管实际文件是 index.ts，但导入路径写的是 .js
```

这是 TypeScript `moduleResolution: "NodeNext"` 的要求。在这种模式下，TypeScript 要求导入路径与最终编译产物的路径一致。由于 TypeScript 文件最终会被编译为 `.js`，所以导入时就写 `.js` 后缀。Bun 在处理时会自动将 `.js` 映射到对应的 `.ts` 文件。

### 2.3 路径别名与模块解析

代码中存在两种导入风格：

```typescript
// 风格 1：相对路径导入（同目录或相近目录）
import { getCwd } from './utils/cwd.js'

// 风格 2：绝对路径导入（跨层级较远的引用）
import { logEvent } from 'src/services/analytics/index.js'
```

绝对路径导入 `src/...` 是通过 TypeScript 的路径别名实现的，它让深层嵌套目录中的文件可以避免冗长的相对路径（如 `../../../../services/analytics/index.js`）。

### 2.4 ESLint 自定义规则

代码中频繁出现的 ESLint 禁用注释揭示了项目强制执行的一些自定义规则：

```typescript
// src/entrypoints/cli.tsx（第 4-5 行）
// eslint-disable-next-line custom-rules/no-top-level-side-effects
process.env.COREPACK_ENABLE_AUTO_PIN = '0'
```

| 自定义规则 | 用途 |
|-----------|------|
| `custom-rules/no-top-level-side-effects` | 禁止在模块顶层执行副作用代码（确保 tree-shaking 安全） |
| `custom-rules/no-process-env-top-level` | 禁止在模块顶层直接访问 `process.env`（防止环境变量在模块加载时被意外捕获） |
| `custom-rules/safe-env-boolean-check` | 要求使用安全的方式检查环境变量布尔值（防止 `'false'` 被当作 truthy） |
| `biome-ignore lint/suspicious/noConsole` | Biome linter 禁止直接使用 `console.log`（使用项目自有日志系统） |

这些规则体现了项目对**启动性能**和**代码安全性**的高度关注。`no-top-level-side-effects` 规则确保模块在被导入时不会执行意外的代码，这对于 Bun 的 tree-shaking（死代码消除）至关重要。

---

## 第三章：Feature Flag 系统

Feature Flag（功能开关）是 Claude Code 构建系统的核心机制，它决定了哪些功能被编译进最终产物、哪些功能在构建时被完全移除。

### 3.1 `feature()` 函数的实现原理

```typescript
// src/entrypoints/cli.tsx（第 1 行）
import { feature } from 'bun:bundle'
```

`feature()` 函数来自 Bun 打包器的内置模块 `bun:bundle`。它的工作原理是：

1. **构建时**：Bun 打包器读取构建配置中定义的 Feature Flag 列表
2. **替换**：将代码中所有 `feature('FLAG_NAME')` 调用替换为布尔字面量 `true` 或 `false`
3. **消除**：JavaScript 引擎的死代码消除（Dead Code Elimination, DCE）自动移除永远不会执行的分支

```
构建前：                              构建后（FLAG 启用）：    构建后（FLAG 未启用）：
if (feature('KAIROS')) {              if (true) {             // 整个 if 块被移除
  require('./kairos.js')                require('./kairos.js')
}                                     }
```

这意味着 `feature()` 不是运行时检查——它是**编译时常量折叠**。未启用的功能不只是"关闭了"，它们**完全不存在**于最终的构建产物中。

### 3.2 完整的 Feature Flag 列表

通过对整个源代码库的静态分析，共发现 **89 个**不同的 Feature Flag，在代码中被引用超过 **1,000 次**。以下按功能类别分组：

#### 核心模式与入口

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `KAIROS` | 154 | 助理模式（Assistant Mode）——将 Claude Code 从开发工具转变为通用 AI 助手 |
| `KAIROS_BRIEF` | 39 | 助理模式的精简变体 |
| `KAIROS_CHANNELS` | 19 | 助理模式的频道功能（多会话管理） |
| `KAIROS_DREAM` | 3 | 助理模式的实验性"梦境"功能 |
| `KAIROS_GITHUB_WEBHOOKS` | 3 | 助理模式的 GitHub Webhook 集成 |
| `KAIROS_PUSH_NOTIFICATION` | 4 | 助理模式的推送通知功能 |
| `BRIDGE_MODE` | 28 | 桥接模式——连接不同的 Claude Code 实例 |
| `COORDINATOR_MODE` | 32 | 协调器模式——管理多个智能体的协作 |
| `DAEMON` | 3 | 后台守护进程模式 |
| `VOICE_MODE` | 46 | 语音交互模式 |

#### AI 行为与推理

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `TRANSCRIPT_CLASSIFIER` | 107 | 对话转录分类器——支持自动模式（Auto Mode）的核心 |
| `PROACTIVE` | 37 | 主动建议功能——AI 主动发起操作 |
| `BASH_CLASSIFIER` | 45 | Bash 命令安全分类器 |
| `ULTRAPLAN` | 10 | 高级计划模式 |
| `ULTRATHINK` | 2 | 深度思考模式 |
| `BUDDY` | 16 | 伙伴功能 |

#### 上下文窗口管理

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `HISTORY_SNIP` | 15 | 历史消息裁剪——在对话过长时智能移除旧消息 |
| `CONTEXT_COLLAPSE` | 20 | 上下文折叠——压缩长对话以节省 token |
| `CACHED_MICROCOMPACT` | 12 | 缓存微压缩——优化压缩操作的性能 |
| `REACTIVE_COMPACT` | 4 | 响应式压缩 |
| `COMPACTION_REMINDERS` | 2 | 压缩提醒 |
| `TOKEN_BUDGET` | 9 | Token 预算控制 |
| `PROMPT_CACHE_BREAK_DETECTION` | 9 | 提示缓存断裂检测 |

#### 工具与技能

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `AGENT_TRIGGERS` | 11 | 定时任务触发器（Cron 功能） |
| `AGENT_TRIGGERS_REMOTE` | 2 | 远程触发器 |
| `WEB_BROWSER_TOOL` | 4 | 网页浏览器工具 |
| `MONITOR_TOOL` | 13 | 监控工具 |
| `WORKFLOW_SCRIPTS` | 10 | 工作流脚本自动化 |
| `EXPERIMENTAL_SKILL_SEARCH` | 21 | 实验性技能搜索 |
| `MCP_SKILLS` | 9 | MCP 协议技能集成 |
| `MCP_RICH_OUTPUT` | 3 | MCP 富文本输出 |
| `CHICAGO_MCP` | 16 | Computer Use MCP 服务器 |

#### 通信与协作

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `UDS_INBOX` | 17 | Unix Domain Socket 消息收件箱（Mac/Linux 进程间通信） |
| `TEAMMEM` | 51 | 团队记忆共享——多智能体共享知识 |
| `FORK_SUBAGENT` | 4 | 子智能体分叉功能 |
| `DIRECT_CONNECT` | 5 | 直连模式 |
| `SSH_REMOTE` | 4 | SSH 远程连接 |
| `CCR_REMOTE_SETUP` | 3 | 远程云环境设置 |
| `CCR_AUTO_CONNECT` | 2 | 云环境自动连接 |
| `CCR_MIRROR` | 4 | 云环境镜像 |

#### 记忆与持久化

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `EXTRACT_MEMORIES` | 7 | 自动提取会话记忆 |
| `AGENT_MEMORY_SNAPSHOT` | 2 | 智能体记忆快照 |
| `MEMORY_SHAPE_TELEMETRY` | 3 | 记忆结构遥测 |
| `FILE_PERSISTENCE` | 2 | 文件持久化 |
| `BG_SESSIONS` | 11 | 后台会话 |

#### 遥测与调试

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `SHOT_STATS` | 10 | 请求统计信息 |
| `PERFETTO_TRACING` | 2 | Perfetto 性能追踪集成 |
| `SLOW_OPERATION_LOGGING` | 2 | 慢操作日志 |
| `ENHANCED_TELEMETRY_BETA` | 2 | 增强遥测 Beta |
| `COWORKER_TYPE_TELEMETRY` | 2 | 协作者类型遥测 |
| `COMMIT_ATTRIBUTION` | 12 | 提交归属追踪（标记 AI 辅助的 Git 提交） |

#### 安全与验证

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `VERIFICATION_AGENT` | 4 | 验证智能体 |
| `REVIEW_ARTIFACT` | 4 | 代码审查产物 |
| `NATIVE_CLIENT_ATTESTATION` | 2 | 原生客户端认证 |
| `HARD_FAIL` | 2 | 严格失败模式（遇到错误立即终止而不降级） |
| `ABLATION_BASELINE` | 1 | 消融实验基线——用于 A/B 测试的对照组 |

#### UI 与体验

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `STREAMLINED_OUTPUT` | 2 | 精简输出模式 |
| `MESSAGE_ACTIONS` | 5 | 消息操作按钮 |
| `TERMINAL_PANEL` | 4 | 终端面板功能 |
| `HISTORY_PICKER` | 4 | 历史会话选择器 |
| `AUTO_THEME` | 2 | 自动主题切换 |
| `TEMPLATES` | 6 | 模板系统 |
| `NATIVE_CLIPBOARD_IMAGE` | 2 | 原生剪贴板图片支持 |

#### 其他

| Feature Flag | 使用次数 | 用途描述 |
|-------------|---------|---------|
| `LODESTONE` | 6 | 内部功能 |
| `TORCH` | 2 | 内部功能 |
| `CONNECTOR_TEXT` | 7 | 连接器文本摘要 |
| `DOWNLOAD_USER_SETTINGS` | 5 | 下载用户设置 |
| `UPLOAD_USER_SETTINGS` | 2 | 上传用户设置 |
| `BUILDING_CLAUDE_APPS` | 2 | 构建 Claude 应用辅助 |
| `BUILTIN_EXPLORE_PLAN_AGENTS` | 2 | 内置探索/计划智能体 |
| `HOOK_PROMPTS` | 2 | Hook 提示 |
| `NEW_INIT` | 2 | 新初始化流程 |
| `TREE_SITTER_BASH` | 3 | Tree-sitter Bash 解析器 |
| `TREE_SITTER_BASH_SHADOW` | 5 | Tree-sitter Bash 影子模式 |
| `QUICK_SEARCH` | 5 | 快速搜索 |
| `SELF_HOSTED_RUNNER` | 2 | 自托管运行器 |
| `BYOC_ENVIRONMENT_RUNNER` | 2 | 自带容器环境运行器 |
| `ALLOW_TEST_VERSIONS` | 2 | 允许测试版本 |
| `ANTI_DISTILLATION_CC` | 2 | 反蒸馏保护 |
| `AWAY_SUMMARY` | 2 | 离开摘要 |
| `BREAK_CACHE_COMMAND` | 2 | 缓存清除命令 |
| `IS_LIBC_GLIBC` / `IS_LIBC_MUSL` | 2 | C 库类型检测（Alpine Linux 兼容性） |
| `OVERFLOW_TEST_TOOL` | 2 | 溢出测试工具 |
| `POWERSHELL_AUTO_MODE` | 2 | PowerShell 自动模式 |
| `RUN_SKILL_GENERATOR` | 2 | 技能生成器 |
| `SKILL_IMPROVEMENT` | 2 | 技能改进 |
| `UNATTENDED_RETRY` | 2 | 无人值守重试 |

### 3.3 实际代码示例

#### 示例 1：条件模块加载——命令注册

这是 Feature Flag 最常见的使用模式——根据构建配置决定是否加载整个模块：

```typescript
// src/commands.ts（第 59-99 行）
import { feature } from 'bun:bundle'

// 死代码消除：条件导入
/* eslint-disable @typescript-eslint/no-require-imports */
const proactive =                              // 主动建议命令
  feature('PROACTIVE') || feature('KAIROS')    // 两个 flag 任一启用时加载
    ? require('./commands/proactive.js').default
    : null                                     // 否则为 null（模块不会被打包）

const voiceCommand = feature('VOICE_MODE')     // 语音命令
  ? require('./commands/voice/index.js').default
  : null

const forceSnip = feature('HISTORY_SNIP')      // 历史裁剪命令
  ? require('./commands/force-snip.js').default
  : null

const workflowsCmd = feature('WORKFLOW_SCRIPTS')  // 工作流命令
  ? (
      require('./commands/workflows/index.js') as typeof import('./commands/workflows/index.js')
    ).default                                      // 类型断言保留 TypeScript 类型信息
  : null
```

注意这里使用了 `require()` 而不是 `import`——这是因为 `import` 是静态声明，必须在文件顶部且不能条件化，而 `require()` 可以在条件表达式中使用。当 Feature Flag 为 `false` 时，`require()` 调用不会执行，对应的模块也不会被打包。

#### 示例 2：条件工具注册

工具系统同样使用 Feature Flag 控制可用的工具集：

```typescript
// src/tools.ts（第 14-50 行）
// 死代码消除：条件导入 ant-only（Anthropic 内部）工具
const REPLTool =
  process.env.USER_TYPE === 'ant'                    // USER_TYPE 也是构建时常量
    ? require('./tools/REPLTool/REPLTool.js').REPLTool
    : null

const SleepTool =
  feature('PROACTIVE') || feature('KAIROS')          // 仅在主动/助理模式下可用
    ? require('./tools/SleepTool/SleepTool.js').SleepTool
    : null

const cronTools = feature('AGENT_TRIGGERS')          // Cron 定时任务工具
  ? [
      require('./tools/ScheduleCronTool/CronCreateTool.js').CronCreateTool,
      require('./tools/ScheduleCronTool/CronDeleteTool.js').CronDeleteTool,
      require('./tools/ScheduleCronTool/CronListTool.js').CronListTool,
    ]
  : []                                               // flag 关闭时返回空数组
```

#### 示例 3：查询引擎中的运行时逻辑分支

Feature Flag 也用于控制核心查询流水线中的处理步骤：

```typescript
// src/query.ts（第 401-410 行）
// 在发送消息给 API 之前，对历史消息进行裁剪
let snipTokensFreed = 0
if (feature('HISTORY_SNIP')) {                       // 编译时决定是否包含此代码块
  queryCheckpoint('query_snip_start')                // 性能检查点
  const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery)
  messagesForQuery = snipResult.messages             // 更新消息列表
  snipTokensFreed = snipResult.tokensFreed           // 记录释放的 token 数
  if (snipResult.boundaryMessage) {
    yield snipResult.boundaryMessage                 // 生成边界标记消息
  }
  queryCheckpoint('query_snip_end')
}
```

#### 示例 4：初始化阶段的功能激活

```typescript
// src/setup.ts（第 95-101 行）
// 启动 Unix Domain Socket 消息服务器（仅 Mac/Linux）
if (feature('UDS_INBOX')) {
  const m = await import('./utils/udsMessaging.js')    // 动态导入（懒加载）
  await m.startUdsMessaging(
    messagingSocketPath ?? m.getDefaultUdsSocketPath(), // 使用自定义路径或默认路径
    { isExplicit: messagingSocketPath !== undefined },
  )
}
```

#### 示例 5：Beta Header 的条件生成

Feature Flag 甚至控制发送给 API 的请求头：

```typescript
// src/constants/betas.ts（第 23-31 行）
// 连接器文本摘要 Beta——仅在 CONNECTOR_TEXT flag 启用时发送此 header
export const SUMMARIZE_CONNECTOR_TEXT_BETA_HEADER = feature('CONNECTOR_TEXT')
  ? 'summarize-connector-text-2026-03-13'
  : ''                                               // flag 关闭时为空字符串

// 自动模式 Beta——依赖 TRANSCRIPT_CLASSIFIER flag
export const AFK_MODE_BETA_HEADER = feature('TRANSCRIPT_CLASSIFIER')
  ? 'afk-mode-2026-01-31'
  : ''

// 内部 Beta——依赖运行时 USER_TYPE 判断
export const CLI_INTERNAL_BETA_HEADER =
  process.env.USER_TYPE === 'ant' ? 'cli-internal-2026-02-09' : ''
```

### 3.4 死代码消除（DCE）的工作原理

死代码消除是一个三步过程：

```
步骤 1 - 常量折叠                    步骤 2 - 条件简化                步骤 3 - 树摇（Tree-shaking）
feature('KAIROS') → false           if (false) {                    （整个 if 块被移除，
                                      require('./kairos.js')          kairos.js 不会被打包）
                                    }
```

代码中明确标注了这一机制：

```typescript
// src/setup.ts（第 350-360 行）
if (feature('COMMIT_ATTRIBUTION')) {
  // 动态导入以启用死代码消除（模块包含需要排除的字符串）。
  // 延迟到下一个 tick，这样 git 子进程在首次渲染之后启动，
  // 而不是在 setup() 微任务窗口中。
  setImmediate(() => {
    void import('./utils/attributionHooks.js').then(
      ({ registerAttributionHooks }) => {
        registerAttributionHooks()
      },
    )
  })
}
```

注意代码注释 "Dynamic import to enable dead code elimination"——当 `COMMIT_ATTRIBUTION` flag 为 `false` 时，整个 `if` 块被移除，`attributionHooks.js` 模块也不会出现在构建产物中。

### 3.5 内部版本 vs 外部版本

Feature Flag 系统的一个核心用途是维护**同一代码库的两个构建变体**：

- **内部版本**（`USER_TYPE === 'ant'`）：Anthropic 员工使用，启用所有 Feature Flag
- **外部版本**（`USER_TYPE === 'external'`）：公开发布版，仅启用稳定功能

```
          同一源代码仓库
               │
       ┌───────┴───────┐
       │               │
  内部构建配置      外部构建配置
  (所有 Flag ON)   (部分 Flag ON)
       │               │
       ▼               ▼
  内部版本产物      外部版本产物
  (~100% 功能)     (~60% 功能)
```

这意味着 Claude Code 的公开版本中，约 40% 的代码在构建时被完全移除——包括实验性功能、内部工具、调试命令等。

---

## 第四章：环境变量系统

Claude Code 使用了一套庞大而精心设计的环境变量系统，涵盖了认证、路由、功能开关、调试日志等方方面面。

### 4.1 环境变量的安全分层

环境变量并非"都是平等的"——有些是安全的配置项，有些可能被恶意利用。Claude Code 对此有严格的分层：

```typescript
// src/utils/managedEnvConstants.ts（第 84-107 行）
/**
 * 可以在信任对话框之前安全应用的环境变量。
 * 这些是 Claude Code 特定的设置，不构成安全风险。
 *
 * 重要：这是哪些环境变量"安全"的唯一权威来源。
 * 不在此列表中的任何环境变量都被视为危险的，
 * 当通过远程托管设置设定时会触发安全对话框。
 *
 * 危险环境变量（不在此列表中）：
 *
 * === 重定向到攻击者控制的服务器 ===
 * - ANTHROPIC_BASE_URL, ANTHROPIC_BEDROCK_BASE_URL...
 * - HTTP_PROXY, HTTPS_PROXY...
 *
 * === 信任攻击者控制的服务器 ===
 * - NODE_TLS_REJECT_UNAUTHORIZED
 * - NODE_EXTRA_CA_CERTS
 *
 * === 切换到攻击者控制的项目 ===
 * - ANTHROPIC_FOUNDRY_RESOURCE
 * - ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
 */
export const SAFE_ENV_VARS = new Set([
  'ANTHROPIC_MODEL',
  'CLAUDE_CODE_USE_BEDROCK',
  'CLAUDE_CODE_USE_VERTEX',
  'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
  // ... 共 83 个安全变量
])
```

环境变量的应用分为两个阶段：

```
阶段 1（信任对话框之前）         阶段 2（信任建立之后）
只应用 SAFE_ENV_VARS 中的变量    应用所有环境变量
├── 模型选择                     ├── 自定义 API 端点
├── 区域路由                     ├── 代理配置
├── 遥测配置                     ├── TLS 证书
└── 功能开关                     └── 认证凭据
```

### 4.2 关键环境变量分类表

#### 认证与 OAuth

| 环境变量 | 默认值 | 描述 |
|---------|-------|------|
| `ANTHROPIC_API_KEY` | 无 | Claude API 密钥——最高优先级认证方式 |
| `CLAUDE_CODE_OAUTH_TOKEN` | 无 | OAuth 令牌 |
| `CLAUDE_CODE_OAUTH_REFRESH_TOKEN` | 无 | OAuth 刷新令牌 |
| `CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR` | 无 | 通过文件描述符传递 API 密钥（避免进程列表泄露） |
| `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` | 无 | API 密钥辅助脚本的缓存 TTL |

#### 提供商路由

| 环境变量 | 默认值 | 描述 |
|---------|-------|------|
| `CLAUDE_CODE_USE_BEDROCK` | 无 | 使用 AWS Bedrock 作为推理后端 |
| `CLAUDE_CODE_USE_VERTEX` | 无 | 使用 Google Vertex AI 作为推理后端 |
| `CLAUDE_CODE_USE_FOUNDRY` | 无 | 使用 Anthropic Foundry 作为推理后端 |
| `ANTHROPIC_BASE_URL` | 无 | 自定义 API 基础 URL |
| `ANTHROPIC_MODEL` | 无 | 覆盖默认模型 |
| `CLAUDE_CODE_SUBAGENT_MODEL` | 无 | 子智能体使用的模型 |
| `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST` | 无 | 标记提供商路由由宿主管理（防止用户设置覆盖） |

#### 运行模式

| 环境变量 | 默认值 | 描述 |
|---------|-------|------|
| `NODE_ENV` | `production` | 运行环境（`production` / `development` / `test`） |
| `USER_TYPE` | `external` | 用户类型（`ant` = Anthropic 内部 / `external` = 外部用户） |
| `CLAUDE_CODE_SIMPLE` | 无 | 精简模式（`--bare`）——跳过 hooks、LSP、插件等 |
| `CLAUDE_CODE_REMOTE` | 无 | 远程模式标记（云容器环境） |
| `CLAUDE_CODE_ENTRYPOINT` | `cli` | 入口来源（`cli` / `sdk-cli` / `sdk-ts` / `sdk-py` / `mcp` / `remote` / `claude-desktop` 等） |

#### 功能禁用开关

| 环境变量 | 默认值 | 描述 |
|---------|-------|------|
| `CLAUDE_CODE_DISABLE_FAST_MODE` | 无 | 禁用快速模式 |
| `CLAUDE_CODE_DISABLE_THINKING` | 无 | 禁用思考模式 |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY` | 无 | 禁用自动记忆 |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | 无 | 禁用后台任务 |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | 无 | 禁用非必要网络请求 |
| `CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING` | 无 | 禁用文件检查点 |
| `CLAUDE_CODE_DISABLE_CLAUDE_MDS` | 无 | 禁用 CLAUDE.md 文件加载 |
| `CLAUDE_CODE_DISABLE_CRON` | 无 | 禁用定时任务 |

#### 性能调优

| 环境变量 | 默认值 | 描述 |
|---------|-------|------|
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | 无 | 最大输出 token 数 |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | 无 | 最大上下文 token 数 |
| `CLAUDE_CODE_MAX_RETRIES` | 无 | 最大重试次数 |
| `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` | 无 | 工具并发执行上限 |
| `CLAUDE_CODE_IDLE_THRESHOLD_MINUTES` | 无 | 空闲超时阈值（分钟） |
| `BASH_DEFAULT_TIMEOUT_MS` | 无 | Bash 命令默认超时时间 |
| `BASH_MAX_TIMEOUT_MS` | 无 | Bash 命令最大超时时间 |
| `MAX_THINKING_TOKENS` | 无 | 最大思考 token 数 |

#### 日志与调试

| 环境变量 | 默认值 | 描述 |
|---------|-------|------|
| `CLAUDE_CODE_DEBUG_LOG_LEVEL` | 无 | 调试日志级别 |
| `CLAUDE_CODE_DEBUG_LOGS_DIR` | 无 | 调试日志目录 |
| `CLAUDE_CODE_PERFETTO_TRACE` | 无 | Perfetto 性能追踪输出文件 |
| `CLAUDE_CODE_SESSION_LOG` | 无 | 会话日志文件 |
| `CLAUDE_CODE_JSONL_TRANSCRIPT` | 无 | JSONL 格式完整对话记录 |
| `CLAUDE_CODE_FRAME_TIMING_LOG` | 无 | 帧时序日志 |

### 4.3 环境变量的安全辅助函数

项目提供了一套标准化的环境变量处理函数，避免了常见的 JavaScript 布尔值陷阱：

```typescript
// src/utils/envUtils.ts（第 32-47 行）
// 安全地检查环境变量是否为"真"
export function isEnvTruthy(envVar: string | boolean | undefined): boolean {
  if (!envVar) return false                         // undefined/null/''/0/false → false
  if (typeof envVar === 'boolean') return envVar    // 直接的布尔值
  const normalizedValue = envVar.toLowerCase().trim()
  return ['1', 'true', 'yes', 'on'].includes(normalizedValue)  // 多种 truthy 表示
}

// 安全地检查环境变量是否被明确设为"假"
export function isEnvDefinedFalsy(
  envVar: string | boolean | undefined,
): boolean {
  if (envVar === undefined) return false             // 未定义 ≠ 明确为假
  if (typeof envVar === 'boolean') return !envVar
  if (!envVar) return false
  const normalizedValue = envVar.toLowerCase().trim()
  return ['0', 'false', 'no', 'off'].includes(normalizedValue)
}
```

为什么需要这些函数？因为在 JavaScript 中，字符串 `'false'` 是 truthy 的：

```javascript
if (process.env.SOME_FLAG) { ... }  // 'false' 也会进入 if 块！
if (isEnvTruthy(process.env.SOME_FLAG)) { ... }  // 正确：'false' → false
```

### 4.4 `--bare` 模式：最小化启动

`--bare` 模式（等同于设置 `CLAUDE_CODE_SIMPLE=1`）是一个特殊的运行模式，跳过几乎所有非核心功能：

```typescript
// src/utils/envUtils.ts（第 60-65 行）
export function isBareMode(): boolean {
  return (
    isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE) ||  // 环境变量方式
    process.argv.includes('--bare')                  // 命令行参数方式
  )
}
```

`--bare` 模式跳过的功能包括：hooks 执行、LSP 连接、插件同步、技能目录扫描、提交归属、后台预取、以及**所有 keychain/凭据读取**（仅允许通过 `ANTHROPIC_API_KEY` 环境变量或 `--settings` 中的 `apiKeyHelper` 认证）。这个模式在代码库中有约 30 个检查点。

### 4.5 子进程环境安全

当 Claude Code 在 GitHub Actions 中运行时，需要特别注意不要将敏感环境变量泄露给子进程：

```typescript
// src/utils/managedEnvConstants.ts（第 14-56 行，PROVIDER_MANAGED_ENV_VARS 集合）
// 当 CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST 为真时，
// 以下变量会从用户设置来源的环境中被剥离，
// 防止用户的 ~/.claude/settings.json 覆盖宿主的路由配置
const PROVIDER_MANAGED_ENV_VARS = new Set([
  'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST',  // 标志本身——设置不能撤销它
  'CLAUDE_CODE_USE_BEDROCK',               // 提供商选择
  'CLAUDE_CODE_USE_VERTEX',
  'ANTHROPIC_BASE_URL',                    // 端点配置
  'ANTHROPIC_API_KEY',                     // 认证
  'ANTHROPIC_MODEL',                       // 模型默认值
  // ... 总共 30+ 个变量
])
```

### 4.6 平台与环境检测

Claude Code 通过读取大量环境变量来检测运行平台和环境：

```typescript
// src/utils/env.ts 中的检测逻辑（简化）
// 检测终端类型
// TERM_PROGRAM: 'vscode', 'iTerm.app', 'ghostty', 'WezTerm' 等
// TERM: 'xterm-ghostty', 'xterm-kitty' 等

// 检测 IDE
// VSCODE_GIT_ASKPASS_MAIN → VS Code
// CURSOR_TRACE_ID → Cursor
// __CFBundleIdentifier → macOS 应用标识
// TERMINAL_EMULATOR === 'JetBrains-JediTerm' → JetBrains IDE

// 检测 CI/CD
// GITHUB_ACTIONS → GitHub Actions
// GITLAB_CI → GitLab CI
// CI → 通用 CI 标记

// 检测云平台
// CODESPACES → GitHub Codespaces
// AWS_LAMBDA_FUNCTION_NAME → AWS Lambda
// K_SERVICE → GCP Cloud Run
// KUBERNETES_SERVICE_HOST → Kubernetes
```

以下是终端检测函数的核心逻辑：

```typescript
// src/utils/env.ts（第 135-213 行，简化版）
function detectTerminal(): string | null {
  // 优先级 1：IDE 特有的环境变量（最精确）
  if (process.env.CURSOR_TRACE_ID) return 'cursor'
  if (process.env.VSCODE_GIT_ASKPASS_MAIN?.includes('cursor')) return 'cursor'
  if (process.env.VSCODE_GIT_ASKPASS_MAIN?.includes('windsurf')) return 'windsurf'

  // 优先级 2：macOS Bundle ID（macOS 独有）
  const bundleId = process.env.__CFBundleIdentifier?.toLowerCase()
  if (bundleId?.includes('vscodium')) return 'codium'

  // 优先级 3：TERM 变量中的终端标识
  if (process.env.TERM === 'xterm-ghostty') return 'ghostty'

  // 优先级 4：TERM_PROGRAM（大多数终端设置）
  if (process.env.TERM_PROGRAM) return process.env.TERM_PROGRAM

  // 优先级 5：终端多路复用器
  if (process.env.TMUX) return 'tmux'
  if (process.env.STY) return 'screen'

  // 优先级 6：Linux 特定终端的环境变量
  if (process.env.KONSOLE_VERSION) return 'konsole'
  if (process.env.KITTY_WINDOW_ID) return 'kitty'
  if (process.env.ALACRITTY_LOG) return 'alacritty'

  // 优先级 7：Windows 特定检测
  if (process.env.WT_SESSION) return 'windows-terminal'
  if (process.env.WSL_DISTRO_NAME) return `wsl-${process.env.WSL_DISTRO_NAME}`

  return null
}
```

这个函数展示了环境检测的精细程度——它能区分 VS Code、Cursor、Windsurf 这三个基于同一底层（Electron + VS Code）的不同编辑器，以及 20+ 种不同的终端模拟器。共检测 **50+ 个**平台特征环境变量，用于遥测分析和功能适配。

---

## 第五章：依赖管理

### 5.1 核心运行时依赖

| 依赖包 | 用途 | 代码中的使用 |
|--------|------|-------------|
| `@anthropic-ai/sdk` | Claude API 官方 SDK | `src/services/api/client.ts` — 创建 API 客户端 |
| `react` | UI 组件框架 | `src/components/` — 所有 UI 组件 |
| `ink` | 终端 UI 渲染引擎（基于 React） | `src/ink/` — 终端渲染层 |
| `commander` | CLI 参数解析 | `src/main.tsx` — 命令行选项定义 |
| `zod` | 运行时类型验证 | `src/schemas/` — Hook、配置等的 schema 验证 |
| `lodash-es` | 通用工具函数库（ESM 版本） | 全局使用 — `memoize`、`debounce` 等 |
| `chalk` | 终端文本着色 | `src/ink/colorize.ts` — 输出着色 |
| `semver` | 语义化版本比较 | `src/cli/update.ts` — 版本更新检查 |
| `diff` | 文本差异比较 | `src/tools/FileEditTool/` — 文件编辑 |
| `strip-ansi` | 移除 ANSI 转义码 | 输出处理中使用 |

### 5.2 开发依赖 vs 运行时依赖

由于 Bun 的打包器在构建时将所有代码打包为单文件，运行时不需要 `node_modules`。因此：

- **运行时依赖**在构建时被内联到打包产物中
- **开发依赖**仅在开发和构建阶段使用

这意味着最终用户安装 Claude Code 时，不需要安装任何 npm 依赖——一切都在单文件可执行程序中。

### 5.3 依赖引入的设计原则

从依赖列表可以观察到几个设计原则：

**最小化原则**：项目没有使用"大而全"的框架（如 NestJS、Express），而是选择了最小化的专用库。UI 层用 React + Ink（而不是 Blessed 或其他终端框架），CLI 解析用 Commander（而不是 yargs 或 oclif），验证用 Zod（而不是 Joi 或 Yup）。每个依赖只做一件事。

**ESM 优先**：注意使用的是 `lodash-es` 而不是 `lodash`——ES Module 版本支持 tree-shaking，打包器可以只包含实际使用的函数而不是整个 lodash 库。

**避免运行时类型检查开销**：Zod 仅在系统边界使用（用户输入、外部配置文件），而不是在内部模块间使用。内部数据流信任 TypeScript 的静态类型检查，避免了运行时验证的性能开销。

### 5.4 外部工具依赖

Claude Code 依赖一些外部命令行工具，这些不是通过 npm 安装的，而是期望在用户系统上已经存在：

| 外部工具 | 用途 | 必需/可选 |
|---------|------|----------|
| `ripgrep` (`rg`) | 高性能代码搜索 | 必需（可使用内置版本：`USE_BUILTIN_RIPGREP`） |
| `git` | 版本控制操作 | 必需（大量功能依赖 Git） |
| `bash` / `zsh` / `powershell` | Shell 命令执行 | 必需（BashTool 核心依赖） |
| `bubblewrap` (`bwrap`) | Linux 沙箱隔离 | 可选（Linux 上的 Bash 命令沙箱） |

### 5.5 GrowthBook——远程功能开关

除了编译时的 Feature Flag，Claude Code 还使用 GrowthBook（一个开源的 Feature Flag 和 A/B 测试平台）进行**运行时**功能控制：

```
编译时 Feature Flag (bun:bundle)        运行时 Feature Flag (GrowthBook)
├── 控制代码是否编译进产物                 ├── 控制已编译功能是否激活
├── 二元选择：存在 or 不存在              ├── 支持百分比灰度发布
├── 无法动态修改                          ├── 可远程动态修改
└── 影响构建产物大小                       └── 不影响构建产物大小
```

这两层功能控制的组合提供了极大的灵活性：编译时 Flag 决定"能力边界"，运行时 Flag 决定"在能力范围内的具体行为"。

---

## 第六章：构建产物结构

### 6.1 构建模式

Claude Code 支持两种构建/分发模式：

```
模式 1: npm 包分发                     模式 2: 原生可执行文件
┌─────────────────────┐              ┌─────────────────────┐
│ @anthropic-ai/       │              │ claude（单文件）       │
│   claude-code/       │              │                     │
│   ├── dist/          │              │ 内嵌：               │
│   │   ├── cli.js     │              │ ├── 打包后的 JS      │
│   │   ├── cli.js.map │   ←Source    │ ├── 嵌入式资源       │
│   │   └── ...        │     Map      │ └── Bun 运行时       │
│   └── package.json   │              │                     │
└─────────────────────┘              └─────────────────────┘
 通过 npm install 安装                 直接下载可执行文件
 需要 Bun 或 Node.js 运行             自包含，无需运行时
```

### 6.2 Source Map 与源码泄露

**Source Map**（源码映射）是一种将打包后的代码映射回原始源码的技术。它通常用于调试——当打包后的代码出错时，调试器可以通过 Source Map 显示原始的 TypeScript 源码位置。

本仓库的代码正是通过 Source Map 提取的。npm 发布的包中包含了 `.js.map` 文件，这些文件嵌入了完整的原始 TypeScript 源码。这是一个非预期的源码暴露——生产发布包中通常不应包含完整的 Source Map。

### 6.3 不同 Feature Flag 组合下的构建差异

由于 Feature Flag 的死代码消除机制，不同的构建配置会产生**显著不同**的产物：

```
外部版本（~60% 功能）：
├── 核心 REPL 对话
├── 文件操作工具
├── Bash 执行
├── 代码搜索（Grep/Glob）
├── Web 工具
├── 子智能体
├── MCP 协议
└── 基础权限系统

内部版本（100% 功能）= 外部版本 + ：
├── KAIROS 助理模式（全部子功能）
├── VOICE_MODE 语音交互
├── COORDINATOR_MODE 多智能体协调
├── BRIDGE_MODE 实例桥接
├── TRANSCRIPT_CLASSIFIER 对话分析
├── COMMIT_ATTRIBUTION 提交归属
├── 各种实验性工具和命令
├── 高级调试功能
├── REPL 工具（ant-only）
└── 内部管理命令
```

这种设计确保了外部版本的精简——用户不会下载到他们无法使用的功能代码，同时也保护了 Anthropic 的内部实验性功能不被提前曝光。

### 6.4 嵌入式资源

在 bundled 模式（编译为可执行文件）下，Claude Code 将一些静态资源嵌入到可执行文件中：

```typescript
// src/skills/bundledSkills.ts 中的逻辑
// 打包的技能定义（Skill）被嵌入到可执行文件中
// 运行时按需提取到磁盘
```

`Bun.embeddedFiles` API 允许访问这些嵌入的文件，而 `isInBundledMode()` 函数用于检测当前是否运行在包含嵌入资源的编译模式下。

---

## 设计哲学分析

构建系统和运行时配置看似只是"基础设施"，但它们深刻体现了 Claude Code 的多个核心设计哲学。

### 可扩展性无需修改（Extensibility Without Modification）

Feature Flag 系统是"开闭原则"（Open-Closed Principle）在构建层面的完美实践。要添加一个新功能，开发者只需：

1. 创建新模块
2. 在构建配置中注册新的 Feature Flag
3. 用 `feature('NEW_FLAG')` 守卫新代码

**不需要修改任何现有代码**。已有的模块完全不知道新功能的存在，新功能也不会影响已有模块的行为。89 个 Feature Flag 的存在证明了这个模式的可扩展性——每个 Flag 都是独立添加的，不需要修改框架本身。

### 性能敏感启动（Performance-Conscious Startup）

整个构建系统的设计都围绕着"最快的启动速度"这个目标：

- **Bun 运行时**的选择直接带来了 4 倍的启动提速
- **死代码消除**确保外部版本不加载不需要的功能模块
- **MACRO 系统**在构建时内联版本号等常量，避免运行时文件读取
- **`--version` 快速路径**实现了零模块加载的版本查询
- **`require()` 条件导入**确保未启用的功能不会触发模块解析
- **ESLint `no-top-level-side-effects` 规则**确保模块导入时不会执行意外的初始化代码

### 隔离与遏制（Isolation & Containment）

`USER_TYPE` 的分离是隔离原则在构建层面的体现。内部用户和外部用户不仅看到不同的功能集，而且在构建层面就已经物理隔离——外部版本中根本不存在内部功能的代码。

环境变量的安全分层同样体现了遏制思想：`SAFE_ENV_VARS` 白名单确保只有已知安全的配置可以在信任建立之前应用；`PROVIDER_MANAGED_ENV_VARS` 确保宿主环境的路由配置不会被用户设置意外覆盖；子进程环境清洗确保敏感凭据不会泄露到 Bash 命令执行的子进程中。

### 可组合性（Composability）

89 个独立的 Feature Flag 可以组合出大量不同的产品变体：

```typescript
// src/commands.ts（第 76-78 行）
const remoteControlServerCommand =
  feature('DAEMON') && feature('BRIDGE_MODE')  // 两个 Flag 的 AND 组合
    ? require('./commands/remoteControlServer/index.js').default
    : null
```

一些功能需要多个 Flag 同时启用才能工作（如远程控制服务器需要 `DAEMON` 和 `BRIDGE_MODE`），而另一些功能可以在不同的 Flag 组合下以不同方式工作（如 `PROACTIVE` 和 `KAIROS` 都可以激活 `SleepTool`）。这种组合性让同一个代码库能够产出多种产品形态。

### 防御性编程（Defensive Programming）

环境变量处理中的 `isEnvTruthy()` / `isEnvDefinedFalsy()` 函数，以及 `DANGEROUS_SHELL_SETTINGS` 列表的存在，体现了对外部输入的不信任原则。环境变量本质上是"外部世界传入的字符串"——项目不假设它们的格式，而是通过标准化的解析函数来处理。`SAFE_ENV_VARS` 白名单机制更是"默认拒绝，显式允许"的经典安全模式。

---

## 关键要点总结

1. **Bun 运行时**为 Claude Code 提供了 4 倍于 Node.js 的启动速度，以及内置的 TypeScript 支持、打包器和测试运行器，形成全合一的工具链
2. **Feature Flag 系统**（`bun:bundle` 的 `feature()` 函数）是构建系统的核心，89 个 Flag 控制着功能的编译时开关，未启用的功能通过死代码消除被完全移除
3. **MACRO 系统**在构建时内联版本号、构建时间等常量，实现零开销的运行时访问
4. **环境变量系统**包含 150+ 个变量，通过 `SAFE_ENV_VARS` 白名单实现安全分层，保护用户免受恶意配置注入
5. **内部/外部版本分离**通过 `USER_TYPE` 和 Feature Flag 的组合，从同一代码库产生功能集截然不同的构建产物
6. **两层功能控制**——编译时 Flag 控制"能力边界"，运行时 GrowthBook 控制"行为细节"——提供了最大的灵活性

---

## 下一篇预览

> **Doc 3: 入口点与初始化流程**
>
> 现在你已经理解了构建系统如何产生最终的可执行文件，下一步我们将跟随代码从 `cli.tsx` 启动到 REPL 可用的完整初始化流程。你将看到 Feature Flag 如何在启动的每个阶段影响代码路径，环境变量如何被分阶段应用，以及为什么某些模块使用动态导入而不是静态导入。
