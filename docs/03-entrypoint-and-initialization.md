# Doc 3: 入口点与初始化流程

> **前置阅读：** Doc 0（TypeScript/JavaScript 语言基础）、Doc 1（项目总览与架构鸟瞰）、Doc 2（构建系统与运行时）

在前两篇文档中，我们了解了 Claude Code 的项目结构和构建系统。现在，我们将沿着代码执行的时间线，从用户在终端敲下 `claude` 命令的那一刻开始，逐行追踪系统是如何启动的。这是理解整个系统的关键——启动流程揭示了架构师在性能、安全和可靠性之间做出的每一个权衡决策。

---

## 第一章：src/main.tsx 深度解析

`src/main.tsx` 是 Claude Code 的核心入口文件，也是整个代码库中第四大文件（4,683 行）。它负责三件事：CLI 参数定义与解析、启动优化（并行预取）、以及根据参数分发到不同的执行路径（交互式 REPL 或非交互式 `--print` 模式）。

### 1.1 文件头部：副作用导入与并行预取

`main.tsx` 的前 20 行是整个启动流程中最精心设计的部分。普通的 TypeScript 文件通常把所有 `import` 语句放在顶部，然后才开始执行逻辑。但 `main.tsx` 刻意将**副作用调用**穿插在 `import` 语句之间：

```typescript
// src/main.tsx:1-20
// 这些副作用必须在所有其他导入之前运行：
// 1. profileCheckpoint 在重量级模块求值开始前打标记
// 2. startMdmRawRead 启动 MDM 子进程（plutil/reg query），
//    使它们与后续 ~135ms 的导入并行运行
// 3. startKeychainPrefetch 并行启动两个 macOS 钥匙串读取
//    （OAuth + 旧版 API 密钥），否则 isRemoteManagedSettingsEligible()
//    会通过同步 spawn 顺序读取它们（每次 macOS 启动 ~65ms）
import { profileCheckpoint, profileReport }
  from './utils/startupProfiler.js';

profileCheckpoint('main_tsx_entry');               // 记录进入时间戳

import { startMdmRawRead }
  from './utils/settings/mdm/rawRead.js';

startMdmRawRead();                                 // 启动 MDM 子进程

import { ensureKeychainPrefetchCompleted, startKeychainPrefetch }
  from './utils/secureStorage/keychainPrefetch.js';

startKeychainPrefetch();                           // 启动钥匙串并行读取
```

这段代码展示了一个重要的启动优化模式：**利用模块求值时间做并行 I/O**。JavaScript 的 `import` 语句在执行时会触发模块文件的**求值**（evaluation），即执行该模块的顶层代码、初始化其导出值。`main.tsx` 导入了约 140 个模块，这个过程大约需要 ~135ms。通过在导入链的最开头启动三个异步操作，它们可以与后续 135ms 的模块求值**并行**运行，从而将这些 I/O 延迟完全隐藏在模块加载时间中。

三个预取操作各自解决一个具体问题：

| 预取操作 | 启动什么 | 节省多少时间 | 源文件 |
|---------|---------|------------|--------|
| `profileCheckpoint` | 记录 `main_tsx_entry` 时间点 | N/A（度量基准点） | `src/utils/startupProfiler.ts:65` |
| `startMdmRawRead` | macOS `plutil` / Windows `reg query` 子进程 | ~20ms | `src/utils/settings/mdm/rawRead.ts:1-10` |
| `startKeychainPrefetch` | 两个 `security find-generic-password` 子进程 | ~65ms（两次 ~32ms 从顺序变并行） | `src/utils/secureStorage/keychainPrefetch.ts:1-22` |

在所有 `import` 语句执行完毕后，第 209 行标记了一个关键里程碑：

```typescript
// src/main.tsx:209
profileCheckpoint('main_tsx_imports_loaded');
// 此时所有模块已求值完毕，~135ms 已过
// startMdmRawRead 和 startKeychainPrefetch 启动的子进程应已完成
```

### 1.2 条件导入与死代码消除

在 `import` 语句区域之后，`main.tsx` 使用了一种独特的**条件 `require()`** 模式来实现死代码消除：

```typescript
// src/main.tsx:68-81
// 延迟 require 避免循环依赖：teammate.ts -> AppState.tsx -> ... -> main.tsx
const getTeammateUtils = () =>
  require('./utils/teammate.js') as typeof import('./utils/teammate.js');
const getTeammatePromptAddendum = () =>
  require('./utils/swarm/teammatePromptAddendum.js')
    as typeof import('./utils/swarm/teammatePromptAddendum.js');

// 死代码消除：仅在 COORDINATOR_MODE Feature Flag 开启时导入
const coordinatorModeModule = feature('COORDINATOR_MODE')
  ? require('./coordinator/coordinatorMode.js')
    as typeof import('./coordinator/coordinatorMode.js')
  : null;

// 死代码消除：仅在 KAIROS Feature Flag 开启时导入
const assistantModule = feature('KAIROS')
  ? require('./assistant/index.js')
    as typeof import('./assistant/index.js')
  : null;
```

这里有两种不同的延迟导入策略：

1. **避免循环依赖**：`getTeammateUtils` 等函数用**箭头函数包裹 `require()`**。在外部构建中，这些模块始终存在，只是被推迟到首次调用时才加载，避免模块初始化顺序导致的循环引用。

2. **Feature Flag 条件导入**：`coordinatorModeModule` 和 `assistantModule` 使用 `feature()` 函数做三元判断。在 Doc 2 中我们学过，Bun 打包器会在编译时将 `feature('COORDINATOR_MODE')` 替换为 `true` 或 `false` 常量，然后死代码消除会移除不可达的分支。对于外部构建，`feature('COORDINATOR_MODE')` 为 `false`，所以 `require('./coordinator/coordinatorMode.js')` 这一整行会被从最终产物中删除。

### 1.3 Commander.js CLI 参数定义

Claude Code 使用 `@commander-js/extra-typings` 库（Commander.js 的类型增强版）定义 CLI 参数。主程序在 `run()` 函数中创建，大约从第 884 行开始：

```typescript
// src/main.tsx:884-903
async function run(): Promise<CommanderCommand> {
  profileCheckpoint('run_function_start');

  // 创建按字母排序的帮助信息配置
  function createSortedHelpConfig() {
    const getOptionSortKey = (opt: Option): string =>
      opt.long?.replace(/^--/, '') ?? opt.short?.replace(/^-/, '') ?? '';
    return Object.assign(
      { sortSubcommands: true, sortOptions: true } as const,
      {
        compareOptions: (a: Option, b: Option) =>
          getOptionSortKey(a).localeCompare(getOptionSortKey(b))
      }
    );
  }

  const program = new CommanderCommand()
    .configureHelp(createSortedHelpConfig())  // 帮助信息按字母排序
    .enablePositionalOptions();                // 支持位置参数
  profileCheckpoint('run_commander_initialized');
  // ...
}
```

Commander.js 的 `program` 对象是整个 CLI 的骨架。它通过链式调用 `.option()` 和 `.addOption()` 定义所有命令行参数。以下是核心参数的分类概览：

```typescript
// src/main.tsx:968-1006（精简展示）
program
  .name('claude')
  .description('Claude Code - starts an interactive session by default')
  .argument('[prompt]', 'Your prompt', String)  // 可选的位置参数

  // === 调试与输出控制 ===
  .option('-d, --debug [filter]', '启用调试模式，可选分类过滤')
  .option('--verbose', '覆盖 verbose 模式设置')
  .option('-p, --print', '输出回复后退出（用于管道）')
  .option('--bare', '最小模式：跳过 hooks、LSP、插件同步等')
  .addOption(new Option('--output-format <format>', '输出格式')
    .choices(['text', 'json', 'stream-json']))

  // === 安全与权限 ===
  .option('--dangerously-skip-permissions', '跳过所有权限检查')
  .addOption(new Option('--permission-mode <mode>', '权限模式')
    .choices(PERMISSION_MODES))                 // 来自 PermissionMode.ts
  .option('--allowedTools <tools...>', '允许的工具列表')
  .option('--disallowedTools <tools...>', '禁止的工具列表')

  // === 模型与推理 ===
  .option('--model <model>', '会话模型')
  .addOption(new Option('--effort <level>', '推理努力级别')
    .argParser(rawValue => { /* 验证 low/medium/high/max */ }))
  .option('--fallback-model <model>', '过载时的备用模型')
  .addOption(new Option('--thinking <mode>', '思维模式')
    .choices(['enabled', 'adaptive', 'disabled']))

  // === 会话管理 ===
  .option('-c, --continue', '继续当前目录最近的对话')
  .option('-r, --resume [value]', '按会话 ID 恢复对话')
  .option('--session-id <uuid>', '指定会话 ID')
  .option('-n, --name <name>', '设置会话显示名称')

  // === 扩展与集成 ===
  .option('--mcp-config <configs...>', '加载 MCP 服务器配置')
  .option('--settings <file-or-json>', '额外设置文件路径或 JSON')
  .option('--add-dir <directories...>', '添加工具可访问的目录')
  .option('--plugin-dir <path>', '加载插件目录（可重复）')
  .option('--agents <json>', '自定义 Agent 定义 JSON')
```

参数被分为五大类：调试与输出控制、安全与权限、模型与推理、会话管理、扩展与集成。每一类都对应着 Claude Code 架构中的一个独立子系统，体现了 Doc 1 中介绍的"可组合性"设计哲学——用户可以通过组合不同的 CLI 参数来构建完全不同的运行模式。

### 1.4 preAction 钩子：初始化时序

Commander.js 的 `preAction` 钩子在执行任何命令之前运行，Claude Code 将核心初始化放在这里而不是顶层代码中，这样 `claude --help` 就无需执行初始化：

```typescript
// src/main.tsx:907-967
program.hook('preAction', async thisCommand => {
  profileCheckpoint('preAction_start');

  // 等待模块求值阶段启动的 MDM 和钥匙串异步读取完成
  await ensureMdmSettingsLoaded();               // 等待 MDM 子进程完成
  await ensureKeychainPrefetchCompleted();        // 等待钥匙串读取完成
  profileCheckpoint('preAction_after_mdm');

  // init() 是整个系统的核心初始化函数（memoized，只执行一次）
  await init();
  profileCheckpoint('preAction_after_init');

  // 初始化分析 sink 和错误日志
  const { initSinks } = await import('./utils/sinks.js');
  initSinks();
  profileCheckpoint('preAction_after_sinks');

  // 处理 --plugin-dir 参数
  const pluginDir = thisCommand.getOptionValue('pluginDir');
  if (Array.isArray(pluginDir) && pluginDir.length > 0) {
    setInlinePlugins(pluginDir);
    clearPluginCache('preAction: --plugin-dir inline plugins');
  }

  // 运行数据库迁移
  runMigrations();
  profileCheckpoint('preAction_after_migrations');

  // 加载远程管理设置（企业功能，非阻塞）
  void loadRemoteManagedSettings();
  void loadPolicyLimits();
  profileCheckpoint('preAction_after_remote_settings');
});
```

注意 `void` 关键字的使用——`loadRemoteManagedSettings()` 和 `loadPolicyLimits()` 是**fire-and-forget**模式（Doc 0 第二章介绍过），它们在后台异步完成，不阻塞启动流程。加载完成后通过热重载（hot-reload）机制应用设置。

### 1.5 action 处理器：主流程分发

Commander.js 的 `.action()` 回调是用户实际命令的处理入口。它是 `main.tsx` 中最长的单个函数（超过 3,000 行），负责解析参数、初始化运行时、然后分发到交互式或非交互式路径。关键的执行序列如下：

```typescript
// src/main.tsx:1007-1010, 1904-1936（精简）
.action(async (prompt, options) => {
  profileCheckpoint('action_handler_start');

  // --bare 模式：设置环境变量让所有门控生效
  if (options.bare) {
    process.env.CLAUDE_CODE_SIMPLE = '1';
  }

  // ... ~900 行的参数解析和验证 ...

  // 加载工具列表
  let tools = getTools(toolPermissionContext);
  profileCheckpoint('action_tools_loaded');

  // === 关键：setup() 与命令加载并行化 ===
  profileCheckpoint('action_before_setup');

  // 注册内建插件和技能（纯内存操作，<1ms）
  initBuiltinPlugins();
  initBundledSkills();

  // setup() 主要耗时在 startUdsMessaging（socket 绑定 ~20ms）
  // 与 getCommands 的文件读取不冲突，可以并行
  const setupPromise = setup(preSetupCwd, permissionMode, ...);
  const commandsPromise = worktreeEnabled
    ? null
    : getCommands(preSetupCwd);       // 并行加载命令
  const agentDefsPromise = worktreeEnabled
    ? null
    : getAgentDefinitionsWithOverrides(preSetupCwd);  // 并行加载 Agent 定义

  // 抑制 Promise.all 连接前的短暂 unhandledRejection
  commandsPromise?.catch(() => {});
  agentDefsPromise?.catch(() => {});

  await setupPromise;                  // 等待 setup 完成
  profileCheckpoint('action_after_setup');
});
```

这里展示了一个精妙的并行化技巧：`setup()` 主要耗时在 UDS socket 绑定（~20ms 的网络 I/O），而 `getCommands()` 和 `getAgentDefinitionsWithOverrides()` 是文件系统读取。两者不竞争同一资源，所以可以安全地并行执行。但当 `--worktree` 启用时，`setup()` 会 `process.chdir()` 改变工作目录，命令和 Agent 定义需要从新目录加载，因此必须等 setup 完成后再执行（`worktreeEnabled ? null : ...`）。

### 1.6 子命令结构

除了默认的交互式/打印模式外，`main.tsx` 还注册了多个子命令：

```
claude                     # 默认：交互式 REPL 或 --print 模式
claude mcp serve           # 启动 MCP 服务器
claude mcp add <name>      # 添加 MCP 服务器
claude mcp remove <name>   # 移除 MCP 服务器
claude auth login           # OAuth 登录
claude auth logout          # 登出
claude auth status          # 认证状态
claude plugin list          # 列出插件
claude plugin install <p>   # 安装插件
claude agents              # 列出配置的 Agent
claude install [target]    # 安装原生构建
claude open <cc-url>       # 连接到远程服务器（Direct Connect）
```

每个子命令都有自己的 `.action()` 处理器和独立的参数集，但共享 `preAction` 钩子中的初始化逻辑。

---

## 第二章：src/setup.ts 解析

`setup()` 函数是 `main.tsx` action 处理器中调用的核心启动步骤。它负责**设置运行时环境**——工作目录、消息通道、钥匙串恢复、worktree 创建、后台任务启动。

### 2.1 函数签名与参数

```typescript
// src/setup.ts:56-66
export async function setup(
  cwd: string,                              // 当前工作目录
  permissionMode: PermissionMode,           // 权限模式（default/plan/auto/bypass）
  allowDangerouslySkipPermissions: boolean, // 是否允许跳过权限
  worktreeEnabled: boolean,                 // 是否启用 worktree 隔离
  worktreeName: string | undefined,         // worktree 名称（可选）
  tmuxEnabled: boolean,                     // 是否创建 tmux 会话
  customSessionId?: string | null,          // 自定义会话 ID
  worktreePRNumber?: number,                // PR 编号（用于 worktree）
  messagingSocketPath?: string,             // UDS 消息通道路径
): Promise<void> {
  logForDiagnosticsNoPII('info', 'setup_started');
```

这 9 个参数清晰地映射到 CLI 的不同功能维度。`PermissionMode` 类型（来自 `src/utils/permissions/PermissionMode.ts`）的五个值将在 Doc 8 中详细分析。

### 2.2 UDS 消息通道初始化

`setup()` 的第一个关键步骤是建立 Unix Domain Socket（UDS）消息通道：

```typescript
// src/setup.ts:88-102
// --bare 模式跳过 UDS 消息服务器和 teammate 快照
// 脚本化调用不接收注入消息，不使用 swarm teammates
if (!isBareMode() || messagingSocketPath !== undefined) {
  // 启动 UDS 消息服务器（仅 Mac/Linux）
  // 默认对 ant 用户启用——在 tmpdir 创建 socket
  // 使用 await 确保服务器绑定完成后才导出
  // $CLAUDE_CODE_MESSAGING_SOCKET 环境变量
  if (feature('UDS_INBOX')) {
    const m = await import('./utils/udsMessaging.js');  // 动态导入
    await m.startUdsMessaging(
      messagingSocketPath ?? m.getDefaultUdsSocketPath(),
      { isExplicit: messagingSocketPath !== undefined },
    );
  }
}
```

注意这里使用了 `await import()` **动态导入**（Doc 0 第三章）——`udsMessaging.js` 模块只在需要时才加载，节省了不使用 UDS 功能时的模块求值开销。同时，`await` 确保 socket 服务器已绑定完成，这样后续的 SessionStart hook 可以通过 `process.env.CLAUDE_CODE_MESSAGING_SOCKET` 找到它。

### 2.3 终端备份恢复

对于交互式会话，`setup()` 检查并恢复可能被中断的终端设置：

```typescript
// src/setup.ts:115-157
if (!getIsNonInteractiveSession()) {
  // iTerm2 备份检查（仅 swarms 启用时）
  if (isAgentSwarmsEnabled()) {
    const restoredIterm2Backup = await checkAndRestoreITerm2Backup();
    if (restoredIterm2Backup.status === 'restored') {
      console.log(chalk.yellow(
        '检测到中断的 iTerm2 设置。原始设置已恢复。'
      ));
    }
  }

  // Terminal.app 备份恢复
  try {
    const restoredTerminalBackup = await checkAndRestoreTerminalBackup();
    if (restoredTerminalBackup.status === 'restored') {
      console.log(chalk.yellow(
        '检测到中断的 Terminal.app 设置。原始设置已恢复。'
      ));
    }
  } catch (error) {
    logError(error);  // 记录但不崩溃
  }
}
```

这段代码体现了**优雅降级**（Graceful Degradation）哲学：即使上次会话崩溃导致终端设置损坏，系统也能自动恢复，而且恢复失败本身也不会阻止启动。

### 2.4 工作目录与 Hooks 快照

```typescript
// src/setup.ts:160-173
// 重要：setCwd() 必须在任何依赖 cwd 的代码之前调用
setCwd(cwd);

// 捕获 hooks 配置快照以检测隐藏的修改
// 重要：必须在 setCwd() 之后调用，这样 hooks 从正确的目录加载
const hooksStart = Date.now();
captureHooksConfigSnapshot();
logForDiagnosticsNoPII('info', 'setup_hooks_captured', {
  duration_ms: Date.now() - hooksStart,
});

// 初始化 FileChanged hook 监视器
initializeFileChangedWatcher(cwd);
```

`captureHooksConfigSnapshot()` 是安全性的关键——它记录 hooks 配置的初始状态，后续任何对 hooks 的修改都会被检测到（防止恶意代码在运行时注入 hooks）。

### 2.5 Worktree 创建

如果用户通过 `--worktree` 启用了 Git Worktree 隔离，`setup()` 会执行一系列操作来创建独立的工作树：

```typescript
// src/setup.ts:176-285（精简）
if (worktreeEnabled) {
  // 必须在 Git 仓库中（除非配置了 WorktreeCreate hook）
  const hasHook = hasWorktreeCreateHook();
  const inGit = await getIsGit();
  if (!hasHook && !inGit) {
    process.stderr.write(chalk.red(
      `Error: Can only use --worktree in a git repository`
    ));
    process.exit(1);
  }

  // 解析到主仓库根目录（处理在 worktree 内调用的情况）
  const mainRepoRoot = findCanonicalGitRoot(getCwd());

  // 创建 worktree
  const worktreeSession = await createWorktreeForSession(
    getSessionId(), slug, tmuxSessionName, ...
  );

  // 切换到 worktree 目录
  process.chdir(worktreeSession.worktreePath);
  setCwd(worktreeSession.worktreePath);
  setOriginalCwd(getCwd());
  setProjectRoot(getCwd());            // worktree 就是项目根
  saveWorktreeState(worktreeSession);   // 持久化 worktree 信息
  clearMemoryFileCaches();              // 清除旧目录的缓存
  updateHooksConfigSnapshot();          // 从新目录重新捕获 hooks
}
```

Worktree 创建后，`setup()` 执行四个关键的"重置"操作：切换工作目录、清除内存文件缓存、保存 worktree 状态、重新捕获 hooks 快照。这确保了后续所有操作都在新的隔离环境中进行。

### 2.6 后台任务与预取

```typescript
// src/setup.ts:287-381（精简）
// === 后台任务——在首次查询前必须完成的注册 ===
if (!isBareMode()) {
  initSessionMemory();                 // 同步：注册 hook，延迟检查
  if (feature('CONTEXT_COLLAPSE')) {
    require('./services/contextCollapse/index.js').initContextCollapse();
  }
}
void lockCurrentVersion();             // 锁定当前版本防止被其他进程删除

profileCheckpoint('setup_before_prefetch');

// === 预取——只包含渲染前需要的项目 ===
if (!skipPluginPrefetch) {
  void getCommands(getProjectRoot());  // 预加载命令列表
}
void import('./utils/plugins/loadPluginHooks.js').then(m => {
  if (!skipPluginPrefetch) {
    void m.loadPluginHooks();          // 预加载插件 hooks
    m.setupPluginHookHotReload();      // 设置热重载
  }
});

// --bare 模式跳过：归因 hook、仓库分类、会话文件访问分析
if (!isBareMode()) {
  if (feature('COMMIT_ATTRIBUTION')) {
    setImmediate(() => {               // 推迟到下一 tick
      void import('./utils/attributionHooks.js').then(
        ({ registerAttributionHooks }) => registerAttributionHooks()
      );
    });
  }
}

initSinks();                           // 附加错误日志和分析 sink

// 会话成功率基线信标——在所有解析、网络、I/O 之前发出
// （inc-3694 P0 崩溃导致此点之后的所有事件丢失）
logEvent('tengu_started', {});

profileCheckpoint('setup_after_prefetch');
```

这段代码中有几个值得注意的模式：

1. **`setImmediate()` 延迟**：归因 hooks 的注册被推迟到"下一 tick"，避免 git 子进程在 `setup()` 微任务窗口中启动，而是在首次渲染之后。

2. **`tengu_started` 信标**：这是会话成功率的分母——它在所有可能失败的操作之前发出。注释引用了一个真实的 P0 事故（inc-3694），当时 CHANGELOG 读取崩溃导致后续所有分析事件丢失。

3. **`--bare` 模式的细粒度跳过**：`--bare` 不是简单的"跳过所有"，而是选择性地跳过非必要的后台工作（归因、分析、团队记忆），但保留安全检查、`tengu_started` 信标、API 密钥预取等关键操作。

### 2.7 权限模式安全验证

`setup()` 末尾包含了对 `--dangerously-skip-permissions` 的安全验证：

```typescript
// src/setup.ts:397-441（精简）
if (permissionMode === 'bypassPermissions' || allowDangerouslySkipPermissions) {
  // 检查 1：Unix 系统上不允许以 root 身份运行（除非在沙箱中）
  if (process.platform !== 'win32' &&
      process.getuid?.() === 0 &&
      process.env.IS_SANDBOX !== '1') {
    console.error('--dangerously-skip-permissions 不能与 root/sudo 一起使用');
    process.exit(1);
  }

  // 检查 2：Anthropic 内部用户——必须在无网络的容器中
  if (process.env.USER_TYPE === 'ant' &&
      process.env.CLAUDE_CODE_ENTRYPOINT !== 'local-agent') {
    const [isDocker, hasInternet] = await Promise.all([
      envDynamic.getIsDocker(),
      env.hasInternetAccess(),
    ]);
    const isSandboxed = isDocker || isBubblewrap || isSandbox;
    if (!isSandboxed || hasInternet) {
      console.error(
        `--dangerously-skip-permissions 只能在无网络的沙箱容器中使用`
      );
      process.exit(1);
    }
  }
}
```

这是**安全优先**（Safety-First）设计的直接体现：即使用户明确请求跳过权限，系统仍然验证环境是否安全。内部用户甚至需要满足双重条件（在容器中且无网络访问）。

---

## 第三章：启动性能优化分析

Claude Code 的启动性能优化不是事后添加的，而是从架构层面系统性设计的。本章分析三个核心优化机制：性能剖析系统、延迟加载策略、以及延迟预取。

### 3.1 Profile Checkpoint 机制

`src/utils/startupProfiler.ts` 实现了一个轻量级的启动性能剖析系统，贯穿整个启动路径。它有两种运行模式：

```typescript
// src/utils/startupProfiler.ts:26-36
// 模块级状态——在模块加载时决定一次
const DETAILED_PROFILING =
  isEnvTruthy(process.env.CLAUDE_CODE_PROFILE_STARTUP);

// Statsig 日志采样：100% 内部用户，0.5% 外部用户
const STATSIG_SAMPLE_RATE = 0.005;
const STATSIG_LOGGING_SAMPLED =
  process.env.USER_TYPE === 'ant' ||
  Math.random() < STATSIG_SAMPLE_RATE;

// 满足任一条件即启用剖析
const SHOULD_PROFILE = DETAILED_PROFILING || STATSIG_LOGGING_SAMPLED;
```

两种模式对应两种用途：

| 模式 | 触发条件 | 覆盖率 | 用途 |
|------|---------|--------|------|
| 采样日志 | 自动 | 100% 内部 / 0.5% 外部 | 生产环境持续监控 |
| 详细剖析 | `CLAUDE_CODE_PROFILE_STARTUP=1` | 手动 | 开发者性能调优 |

`profileCheckpoint()` 函数本身极其轻量：

```typescript
// src/utils/startupProfiler.ts:65-75
export function profileCheckpoint(name: string): void {
  if (!SHOULD_PROFILE) return;       // 未采样时零开销

  const perf = getPerformance();
  perf.mark(name);                   // 使用 Node.js 标准 Performance API

  // 仅详细模式捕获内存快照
  if (DETAILED_PROFILING) {
    memorySnapshots.push(process.memoryUsage());
  }
}
```

关键设计：`SHOULD_PROFILE` 在**模块加载时**计算一次。对于未被采样的 99.5% 外部用户，`profileCheckpoint()` 在运行时只执行一个 `if (false) return`，开销几乎为零。

系统定义了四个关键的**阶段**（Phase）供生产监控：

```typescript
// src/utils/startupProfiler.ts:49-54
const PHASE_DEFINITIONS = {
  import_time: ['cli_entry', 'main_tsx_imports_loaded'],  // 模块加载时间
  init_time: ['init_function_start', 'init_function_end'], // init() 耗时
  settings_time: ['eagerLoadSettings_start', 'eagerLoadSettings_end'], // 设置加载
  total_time: ['cli_entry', 'main_after_run'],             // 总启动时间
} as const;
```

整个启动路径中散布的 30+ 个 checkpoint 形成了一条完整的时间线。如果用户设置 `CLAUDE_CODE_PROFILE_STARTUP=1`，他们会看到类似这样的报告：

```
================================================================================
STARTUP PROFILING REPORT
================================================================================
    0.0ms +  0.0ms  profiler_initialized
    0.2ms +  0.2ms  cli_entry
    0.5ms +  0.3ms  main_tsx_entry
  135.1ms +134.6ms  main_tsx_imports_loaded
  135.4ms +  0.3ms  main_function_start
  135.6ms +  0.2ms  main_warning_handler_initialized
  ...
Total startup time: 285.3ms
================================================================================
```

### 3.2 延迟加载（Lazy Loading）策略

Claude Code 使用三种延迟加载策略，每种针对不同的场景：

**策略 1：Feature Flag 死代码消除**

```typescript
// src/main.tsx:76-77
const coordinatorModeModule = feature('COORDINATOR_MODE')
  ? require('./coordinator/coordinatorMode.js') : null;
```

编译时消除——外部构建根本不包含这些模块。

**策略 2：`await import()` 动态导入**

```typescript
// src/setup.ts:96-98
if (feature('UDS_INBOX')) {
  const m = await import('./utils/udsMessaging.js');
  await m.startUdsMessaging(...);
}
```

运行时按需加载——模块仅在条件满足时才加载到内存。

**策略 3：`setImmediate()` 延迟到下一 tick**

```typescript
// src/setup.ts:354-358
setImmediate(() => {
  void import('./utils/attributionHooks.js').then(
    ({ registerAttributionHooks }) => registerAttributionHooks()
  );
});
```

推迟到事件循环的下一个迭代，避免在当前微任务队列中产生 I/O 竞争。

这三种策略按"激进程度"递增：编译时消除 > 运行时按需加载 > 推迟到下一 tick。选择哪种策略取决于模块的大小、使用频率和时序要求。

### 3.3 延迟预取（Deferred Prefetches）

`startDeferredPrefetches()` 是启动优化的另一半——它定义了所有**不需要在首次渲染前完成**的预取工作，推迟到 REPL 渲染之后执行：

```typescript
// src/main.tsx:388-431
export function startDeferredPrefetches(): void {
  // 如果只是测量启动性能，跳过所有预取
  if (isEnvTruthy(process.env.CLAUDE_CODE_EXIT_AFTER_FIRST_RENDER) ||
      isBareMode()) {
    return;                          // --bare 跳过所有预取
  }

  // === 进程级预取（在用户打字时并行执行） ===
  void initUser();                   // 用户信息
  void getUserContext();             // 用户上下文
  prefetchSystemContextIfSafe();     // 系统上下文（需要信任检查）
  void getRelevantTips();            // 使用提示

  // === 云提供商凭据预取 ===
  if (isEnvTruthy(process.env.CLAUDE_CODE_USE_BEDROCK)) {
    void prefetchAwsCredentialsAndBedRockInfoIfSafe();
  }
  if (isEnvTruthy(process.env.CLAUDE_CODE_USE_VERTEX)) {
    void prefetchGcpCredentialsIfSafe();
  }

  // === 分析与功能初始化 ===
  void countFilesRoundedRg(getCwd(), AbortSignal.timeout(3000), []);
  void initializeAnalyticsGates();
  void prefetchOfficialMcpUrls();
  void refreshModelCapabilities();

  // === 文件变更检测器（从 init() 中延迟出来） ===
  void settingsChangeDetector.initialize();
  void skillChangeDetector.initialize();

  // === 事件循环阻塞检测器（仅内部用户） ===
  if ("external" === 'ant') {
    void import('./utils/eventLoopStallDetector.js')
      .then(m => m.startEventLoopStallDetector());
  }
}
```

这个函数在 `renderAndRun()` 中被调用，时机是在 REPL 的 React 组件树渲染完成之后：

```typescript
// src/interactiveHelpers.tsx:98-103
export async function renderAndRun(
  root: Root,
  element: React.ReactNode
): Promise<void> {
  root.render(element);              // 先渲染 UI
  startDeferredPrefetches();         // 然后启动后台预取
  await root.waitUntilExit();        // 等待退出
  await gracefulShutdown(0);         // 优雅关闭
}
```

这种设计利用了一个用户行为洞察：REPL 启动后，用户通常需要几秒钟来组织想法和输入提示。这几秒的"打字时间"被用来并行执行所有后台预取，当用户按下回车时，大部分数据已经就绪。

### 3.4 真正的入口：src/entrypoints/cli.tsx

实际上，在 `main.tsx` 之前还有一个更早的入口点——`src/entrypoints/cli.tsx`。它是 Bun 打包后的真正入口，负责两件事：

```typescript
// src/entrypoints/cli.tsx:33-48
async function main(): Promise<void> {
  const args = process.argv.slice(2);

  // 快速路径：--version 零模块加载
  if (args.length === 1 &&
      (args[0] === '--version' || args[0] === '-v')) {
    console.log(`${MACRO.VERSION} (Claude Code)`);
    return;                          // 立即返回，不加载任何模块
  }

  // 其他路径：加载启动剖析器
  const { profileCheckpoint } = await import('../utils/startupProfiler.js');
  profileCheckpoint('cli_entry');    // 记录 CLI 入口时间

  // ... 其他快速路径检查（--dump-system-prompt, --chrome-native-host）...
}
```

`--version` 的"零加载"快速路径是极致的性能优化——它只使用编译时内联的 `MACRO.VERSION` 常量，不导入任何模块，执行时间几乎为零。

### 3.5 init() 函数：核心初始化

`src/entrypoints/init.ts` 中的 `init()` 函数是 `preAction` 钩子中调用的核心初始化逻辑。它被 `memoize()` 包裹，确保只执行一次：

```typescript
// src/entrypoints/init.ts:57-214（精简）
export const init = memoize(async (): Promise<void> => {
  profileCheckpoint('init_function_start');

  // 1. 启用配置系统（验证配置文件合法性）
  enableConfigs();
  profileCheckpoint('init_configs_enabled');

  // 2. 应用安全的环境变量（信任对话之前）
  applySafeConfigEnvironmentVariables();
  applyExtraCACertsFromConfig();     // TLS 证书必须在首次握手前设置
  profileCheckpoint('init_safe_env_vars_applied');

  // 3. 设置优雅关闭处理器
  setupGracefulShutdown();
  profileCheckpoint('init_after_graceful_shutdown');

  // 4. 初始化 1P 事件日志（非阻塞）
  void Promise.all([
    import('../services/analytics/firstPartyEventLogger.js'),
    import('../services/analytics/growthbook.js'),
  ]).then(([fp, gb]) => {
    fp.initialize1PEventLogging();
    gb.onGrowthBookRefresh(() => {
      void fp.reinitialize1PEventLoggingIfConfigChanged();
    });
  });

  // 5. OAuth 账户信息填充（非阻塞）
  void populateOAuthAccountInfoIfNeeded();

  // 6. 远程管理设置加载准备
  if (isEligibleForRemoteManagedSettings()) {
    initializeRemoteManagedSettingsLoadingPromise();
  }

  // 7. 网络配置
  configureGlobalMTLS();             // mTLS 设置
  configureGlobalAgents();           // HTTP 代理
  preconnectAnthropicApi();          // 预连接到 API（重叠 TCP+TLS 握手 ~100-200ms）
  profileCheckpoint('init_network_configured');

  // 8. 平台特定设置
  setShellIfWindows();               // Windows 下设置 git-bash

  // 9. 注册清理回调
  registerCleanup(shutdownLspServerManager);
  registerCleanup(async () => {
    const { cleanupSessionTeams } = await import('../utils/swarm/teamHelpers.js');
    await cleanupSessionTeams();
  });

  profileCheckpoint('init_function_end');
});
```

`init()` 函数展示了几个关键的设计决策：

1. **安全环境变量的两阶段应用**：`applySafeConfigEnvironmentVariables()` 在信任对话之前调用，只应用安全的环境变量；`applyConfigEnvironmentVariables()` 在信任建立后调用，应用所有环境变量（包括潜在危险的配置）。

2. **API 预连接**：`preconnectAnthropicApi()` 在 CA 证书和代理配置完成后立即执行，将 TCP+TLS 握手（~100-200ms）与后续 ~100ms 的 action 处理器工作重叠。

3. **`memoize()` 保护**：`init()` 可能从多个路径被调用（preAction 钩子、子命令等），`memoize()` 确保只执行一次。

### 3.6 完整启动时间线

综合以上分析，从 `cli.tsx` 到 REPL 就绪的完整时间线如下：

```
时间    事件                              文件
────────────────────────────────────────────────────────────
0ms     cli_entry                        cli.tsx:48
        ↓ 动态导入 startupProfiler
1ms     main_tsx_entry                   main.tsx:12
        ├── startMdmRawRead()            ──→ (MDM 子进程并行运行)
        ├── startKeychainPrefetch()      ──→ (钥匙串读取并行运行)
        ↓ ~135ms 的模块 import 求值
136ms   main_tsx_imports_loaded          main.tsx:209
        ↓ 调试检查、安全防护
137ms   main_function_start              main.tsx:586
        ↓ SIGINT 处理器、深度链接、SSH
138ms   main_warning_handler_initialized main.tsx:607
        ↓ Commander.js 初始化
139ms   run_function_start               main.tsx:885
140ms   run_commander_initialized        main.tsx:903
        ↓ program.parse(process.argv) 触发...

═══ preAction 钩子 ═══
141ms   preAction_start                  main.tsx:908
        ├── await ensureMdmSettingsLoaded()   (MDM 子进程应已完成)
        ├── await ensureKeychainPrefetchCompleted()
142ms   preAction_after_mdm
        ├── await init()                 ──→ init.ts
        │   ├── enableConfigs()
        │   ├── applySafeConfigEnvironmentVariables()
        │   ├── setupGracefulShutdown()
        │   ├── void 1P event logging (async)
        │   ├── configureGlobalMTLS()
        │   ├── configureGlobalAgents()
        │   └── preconnectAnthropicApi() ──→ (TCP+TLS 并行)
160ms   preAction_after_init
        ├── initSinks()
        ├── runMigrations()
        ├── void loadRemoteManagedSettings() (async)
175ms   preAction_after_remote_settings

═══ action 处理器 ═══
176ms   action_handler_start             main.tsx:1007
        ↓ ~800 行参数解析和验证
200ms   action_after_input_prompt
        ├── getTools()                   加载工具列表
201ms   action_tools_loaded
        ├── initBuiltinPlugins()         (<1ms, 纯内存)
        ├── initBundledSkills()          (<1ms, 纯内存)
        ├── setup() ─────────────┐       并行
        │   ├── UDS socket       │
        │   ├── hooks snapshot   │
        │   └── background jobs  │
        ├── getCommands() ───────┤       并行
        └── getAgentDefs() ──────┘       并行
230ms   action_after_setup
        ↓ MCP 连接、插件加载、模型解析...
280ms   action_mcp_configs_loaded

═══ 交互式路径 ═══
        ├── showSetupScreens()           信任对话、权限确认
        ├── root.render(<App><REPL/></App>)  渲染 UI
        ├── startDeferredPrefetches()    ──→ 后台预取启动
        │   ├── void initUser()
        │   ├── void getUserContext()
        │   ├── void getSystemContext()
        │   ├── void getRelevantTips()
        │   └── void refreshModelCapabilities()
~300ms  REPL 就绪，等待用户输入
        用户打字期间，预取在后台完成...
```

整个启动流程的核心思想是**将不可避免的延迟变成并行 I/O 的窗口**：

- 模块求值的 ~135ms → MDM + 钥匙串并行读取
- setup() 的 ~28ms → 命令和 Agent 定义并行加载
- 用户打字的几秒 → 系统上下文、用户信息、Tips 并行预取
- init() 后续工作 → API 预连接与 action 处理器并行

---

## 第四章：src/entrypoints/init.ts 解析

`init.ts`（341 行）是整个系统的**核心初始化函数**。它通过 lodash-es 的 `memoize()` 保证只执行一次，并采用"同步关键路径 + 异步即发即忘"的双轨策略：关键操作（配置验证、环境变量、网络设置）必须同步完成后才能继续，而非关键操作（分析日志、OAuth 缓存、IDE 检测）以 `void` 方式异步执行，不阻塞主流程。

### 4.1 Memoize 保护：确保单次执行

```typescript
// src/entrypoints/init.ts:5,57
import memoize from 'lodash-es/memoize.js'   // lodash-es 的 memoize 工具

export const init = memoize(async (): Promise<void> => {
  // 整个 init 函数体被 memoize 包装
  // 第一次调用时执行，后续调用直接返回缓存的 Promise
  // 这很重要：main.tsx 的多个代码路径都可能调用 init()
})
```

**为什么需要 memoize？** `init()` 可能从 `preAction` 钩子、`action` 处理器等多处被调用。如果不做保护，重复执行会导致重复注册清理回调、重复初始化网络代理等副作用。`memoize()` 在第一次调用后缓存返回的 `Promise`，后续调用直接返回同一个 `Promise`。

### 4.2 两阶段环境变量：信任之前与信任之后

`init()` 最精巧的设计之一是将环境变量应用分为两个阶段。这是 Claude Code 安全模型的体现——在用户确认信任工作区之前，只应用**安全的**环境变量：

```typescript
// src/entrypoints/init.ts:71-84
// 阶段一：仅应用安全的环境变量（信任对话之前）
// 完整的环境变量在信任建立后才应用
const envVarsStart = Date.now()
applySafeConfigEnvironmentVariables()       // 只应用 SAFE_ENV_VARS 白名单中的变量

// 在任何 TLS 连接之前应用 CA 证书配置
// Bun 通过 BoringSSL 在启动时缓存 TLS 证书库，
// 所以必须在第一次 TLS 握手之前完成
applyExtraCACertsFromConfig()               // 自定义 CA 证书注入
```

阶段二发生在完全不同的时机——`showSetupScreens()` 中信任对话完成后：

```typescript
// src/interactiveHelpers.tsx:184
// 信任对话完成后，应用完整的环境变量
// 这包括来自不受信任源的潜在危险环境变量
applyConfigEnvironmentVariables()           // 应用全部环境变量（含远程设置）
```

以及 `initializeTelemetryAfterTrust()` 中远程管理设置加载后的重新应用：

```typescript
// src/entrypoints/init.ts:268-269
// 远程管理设置加载后，重新应用环境变量以包含远程配置
applyConfigEnvironmentVariables()
```

这个两阶段模式的安全意义在于：项目的 `.claude/settings.json` 可能包含恶意环境变量设置（例如重定向 API 端点到攻击者服务器），这些必须在用户明确信任工作区之后才生效。

### 4.3 异步即发即忘操作

`init()` 中大量使用 `void` 前缀启动异步操作，这些操作不阻塞初始化流程：

```typescript
// src/entrypoints/init.ts:94-128

// === 分析日志初始化 ===
// Promise.all 并行加载两个分析模块
void Promise.all([
  import('../services/analytics/firstPartyEventLogger.js'),
  import('../services/analytics/growthbook.js'),
]).then(([fp, gb]) => {
  fp.initialize1PEventLogging()              // 初始化第一方事件日志
  // 当 GrowthBook 配置刷新时，重建日志提供者
  gb.onGrowthBookRefresh(() => {             // 注册配置变更回调
    void fp.reinitialize1PEventLoggingIfConfigChanged()
  })
})

// === OAuth 账户信息 ===
void populateOAuthAccountInfoIfNeeded()      // 填充 OAuth 缓存（VSCode 扩展登录场景需要）

// === JetBrains IDE 检测 ===
void initJetBrainsDetection()                // 异步检测，填充缓存供后续同步访问

// === GitHub 仓库检测 ===
void detectCurrentRepository()               // 用于 gitDiff PR 链接

// === 远程管理设置加载 Promise 初始化 ===
if (isEligibleForRemoteManagedSettings()) {  // 检查是否有资格加载远程设置
  initializeRemoteManagedSettingsLoadingPromise()  // 早期初始化 Promise
}                                            // 其他系统（如插件钩子）可以 await 这个 Promise
if (isPolicyLimitsEligible()) {              // 策略限制同理
  initializePolicyLimitsLoadingPromise()     // 含超时保护防止死锁
}
```

这些操作的共同特点是：**结果不是启动必需的，但缓存后可以加速后续操作**。例如，`initJetBrainsDetection()` 异步检测 IDE 环境并缓存结果，使得后续需要判断是否在 JetBrains 中运行时可以同步读取缓存。

### 4.4 网络配置与 API 预连接

网络配置是 `init()` 中**必须同步完成**的关键部分——后续所有网络请求都依赖正确的代理和 TLS 设置：

```typescript
// src/entrypoints/init.ts:134-159

// 配置全局 mTLS（相互 TLS 认证）设置
configureGlobalMTLS()                        // 企业环境可能需要客户端证书

// 配置全局 HTTP 代理（代理和/或 mTLS）
configureGlobalAgents()                      // 设置 HTTP/HTTPS 代理 agent

// 预连接 Anthropic API —— 将 TCP+TLS 握手（~100-200ms）
// 与 action 处理器的 ~100ms 工作重叠
// 在 CA 证书 + 代理 agent 配置完成后执行，
// 确保预热的连接使用正确的传输层
// 即发即忘；在代理/mTLS/unix/云提供商模式下跳过
preconnectAnthropicApi()
```

`preconnectAnthropicApi()` 是一个巧妙的优化：它在 `init()` 返回后、action 处理器解析参数的 ~100ms 窗口中完成 TCP + TLS 握手。这样当第一个 API 请求发出时，连接已经建立好了。

### 4.5 条件初始化与清理注册

`init()` 还包含几个条件性操作和清理注册：

```typescript
// src/entrypoints/init.ts:167-200

// CCR 上游代理：仅在 CLAUDE_CODE_REMOTE 环境下启动
if (isEnvTruthy(process.env.CLAUDE_CODE_REMOTE)) {
  try {
    const { initUpstreamProxy, getUpstreamProxyEnv } =
      await import('../upstreamproxy/upstreamproxy.js')   // 延迟加载
    const { registerUpstreamProxyEnvFn } =
      await import('../utils/subprocessEnv.js')
    registerUpstreamProxyEnvFn(getUpstreamProxyEnv)       // 注册环境变量注入函数
    await initUpstreamProxy()                             // 启动本地 CONNECT 中继
  } catch (err) {
    // 失败时静默继续（fail-open 策略）
    logForDebugging(`[init] upstreamproxy init failed: ...`)
  }
}

// Windows 环境特有：设置 Git Bash
setShellIfWindows()

// 注册 LSP 管理器清理（实际初始化在 main.tsx 中 --plugin-dir 处理后）
registerCleanup(shutdownLspServerManager)

// 注册团队清理处理器（gh-32730 修复：子智能体创建的团队不再遗留在磁盘上）
registerCleanup(async () => {
  const { cleanupSessionTeams } = await import(
    '../utils/swarm/teamHelpers.js'                       // 延迟加载：swarm 代码在 Feature Gate 后面
  )
  await cleanupSessionTeams()
})
```

注意 `registerCleanup()` 模式：将清理函数注册到全局清理注册表中，由 `gracefulShutdown()` 在退出时统一执行。延迟导入 `teamHelpers.js` 体现了"不为未使用的功能付出加载代价"的原则——大多数会话不会创建团队。

### 4.6 错误处理：配置错误的特殊路径

`init()` 对 `ConfigParseError` 有特殊处理——当配置文件格式错误时，需要区分交互式和非交互式场景：

```typescript
// src/entrypoints/init.ts:215-237
} catch (error) {
  if (error instanceof ConfigParseError) {
    // 非交互式模式（如桌面插件管理器运行 `plugin marketplace list --json`）
    // 跳过 Ink 对话框，直接写入 stderr
    if (getIsNonInteractiveSession()) {
      process.stderr.write(
        `Configuration error in ${error.filePath}: ${error.message}\n`,
      )
      gracefulShutdownSync(1)                             // 同步退出
      return
    }

    // 交互式模式：动态导入配置错误对话框
    // 注意：动态导入避免在 init 时就加载 React
    return import('../components/InvalidConfigDialog.js')
      .then(m => m.showInvalidConfigDialog({ error }))
    // 对话框本身处理 process.exit
  } else {
    throw error                                           // 非配置错误：继续向上抛出
  }
}
```

### 4.7 遥测初始化：信任之后的第二阶段

遥测（telemetry）初始化被刻意推迟到信任对话完成之后，因为它需要 OTEL 端点环境变量和认证信息：

```typescript
// src/entrypoints/init.ts:247-303

export function initializeTelemetryAfterTrust(): void {
  if (isEligibleForRemoteManagedSettings()) {
    // SDK/headless + beta tracing 模式：急切初始化
    if (getIsNonInteractiveSession() && isBetaTracingEnabled()) {
      void doInitializeTelemetry()                        // 确保首次查询前 tracer 就绪
    }
    // 等待远程管理设置加载完成
    void waitForRemoteManagedSettingsToLoad()
      .then(async () => {
        applyConfigEnvironmentVariables()                 // 重新应用环境变量（含远程设置）
        await doInitializeTelemetry()                     // 然后初始化遥测
      })
  } else {
    void doInitializeTelemetry()                          // 非远程设置用户：直接初始化
  }
}

async function doInitializeTelemetry(): Promise<void> {
  if (telemetryInitialized) { return }                    // 双重初始化保护
  telemetryInitialized = true                             // 在初始化前设标志防并发
  try {
    await setMeterState()                                 // 延迟加载 ~400KB OpenTelemetry
  } catch (error) {
    telemetryInitialized = false                          // 失败时重置标志允许重试
    throw error
  }
}
```

`doInitializeTelemetry()` 的标志管理值得关注：先设 `true` 防止并发调用，失败后重置为 `false` 允许重试。这是异步互斥锁（async mutex）的轻量实现。

---

## 第五章：REPL 启动——从 interactiveHelpers.tsx 到用户界面

`src/interactiveHelpers.tsx` 是连接初始化流程和用户界面的桥梁。它包含三个关键功能：安全设置对话流（`showSetupScreens`）、REPL 渲染启动（`renderAndRun`）、以及渲染上下文配置（`getRenderContext`）。

### 5.1 renderAndRun：四行代码的精确编排

```typescript
// src/interactiveHelpers.tsx:98-103

// 将主 UI 渲染到 root 中并等待退出
// 处理通用结尾：启动延迟预取、等待退出、优雅关闭
export async function renderAndRun(
  root: Root,                                // Ink 的渲染根节点
  element: React.ReactNode                   // 要渲染的 React 元素树
): Promise<void> {
  root.render(element);                      // 1. 挂载 React 组件树到 Ink
  startDeferredPrefetches();                 // 2. 启动后台预取任务
  await root.waitUntilExit();                // 3. 阻塞直到用户退出 REPL
  await gracefulShutdown(0);                 // 4. 清理并退出
}
```

这四行代码的顺序至关重要：

1. **先渲染**：用户立即看到界面（首次绘制），感知延迟最小化
2. **再预取**：`startDeferredPrefetches()` 在首次绘制**之后**执行，不阻塞初始绘制
3. **然后等待**：`root.waitUntilExit()` 返回一个 `Promise`，在 REPL 会话结束时 resolve
4. **最后清理**：确保所有注册的清理回调（日志刷新、LSP 关闭、团队清理等）执行完毕

### 5.2 launchRepl：延迟加载的组件挂载

```typescript
// src/replLauncher.tsx:12-22

export async function launchRepl(
  root: Root,
  appProps: AppWrapperProps,                 // 包含 getFpsMetrics, stats, initialState
  replProps: REPLProps,                      // REPL 组件的属性
  renderAndRun: (root: Root, element: React.ReactNode) => Promise<void>
): Promise<void> {
  // 动态导入：只有交互式模式才加载这两个重量级组件
  const { App } = await import('./components/App.js')     // 全局上下文提供者
  const { REPL } = await import('./screens/REPL.js')      // 主 REPL 屏幕（5,005 行）

  await renderAndRun(root, <App {...appProps}>
      <REPL {...replProps} />
    </App>)
}
```

`launchRepl()` 使用 `await import()` 动态导入 `App` 和 `REPL` 组件。这确保了非交互式模式（`--print`）不会加载这些重量级 React 组件。`App` 组件是全局上下文提供者，包裹 `REPL` 提供状态管理、键绑定、FPS 追踪等基础设施。

### 5.3 showSetupScreens：信任建立的对话序列

`showSetupScreens()` 是启动流程中最复杂的函数（~200 行），它管理一系列安全相关的对话框，每一步都有严格的执行顺序要求：

```
showSetupScreens 对话序列
────────────────────────────────
1. Onboarding       — 首次使用的引导教程（仅一次）
2. TrustDialog      — 工作区信任确认（安全边界）
   ↓ setSessionTrustAccepted(true)
   ↓ resetGrowthBook() + initializeGrowthBook()
   ↓ getSystemContext()（信任后才预取系统上下文）
   ↓ handleMcpjsonServerApprovals()
   ↓ shouldShowClaudeMdExternalIncludesWarning()
3. applyConfigEnvironmentVariables()
   ↓ 信任建立后应用完整环境变量
4. setImmediate(() => initializeTelemetryAfterTrust())
   ↓ 推迟到下一 tick（不阻塞渲染）
5. GroveDialog      — Grove 策略确认（条件性）
6. ApproveApiKey    — 自定义 API 密钥确认（条件性）
7. BypassPermissionsModeDialog — 危险模式确认（条件性）
8. AutoModeOptInDialog — 自动模式同意（条件性）
9. DevChannelsDialog — 开发频道确认（条件性）
10. ClaudeInChromeOnboarding — Chrome 集成引导（条件性）
```

几个设计要点值得强调：

**快速路径优化**：TrustDialog 有一个快速路径——如果 CWD 已经被信任过，跳过动态导入和渲染循环：

```typescript
// src/interactiveHelpers.tsx:135-140
// 快速路径：当 CWD 已被信任时跳过 TrustDialog 的导入和渲染
if (!checkHasTrustDialogAccepted()) {
  const { TrustDialog } = await import('./components/TrustDialog/TrustDialog.js')
  await showSetupDialog(root, done =>
    <TrustDialog commands={commands} onDone={done} />)
}
```

**信任后的连锁操作**：信任确认后立即触发一系列操作——GrowthBook 重新初始化（确保新的认证头生效）、系统上下文预取、MCP 服务器审批等。

**遥测延迟到 setImmediate**：`initializeTelemetryAfterTrust()` 被包裹在 `setImmediate()` 中，确保 OpenTelemetry 的 ~400KB 动态导入在首次渲染**之后**才解析，不会阻塞 UI：

```typescript
// src/interactiveHelpers.tsx:190
// 推迟到下一 tick，使 OTel 动态导入在首次渲染后才解析
setImmediate(() => initializeTelemetryAfterTrust())
```

### 5.4 startDeferredPrefetches：利用用户打字时间

`startDeferredPrefetches()` 定义在 `main.tsx` 中，在 `renderAndRun()` 里首次渲染后调用。它启动的所有操作都是 `void`（即发即忘），在用户打字期间后台运行：

```typescript
// src/main.tsx:388-431

export function startDeferredPrefetches(): void {
  // 性能基准测试和 --bare 模式下跳过全部预取
  if (isEnvTruthy(process.env.CLAUDE_CODE_EXIT_AFTER_FIRST_RENDER) ||
      isBareMode()) {
    return;                                  // 脚本化调用不需要预热缓存
  }

  // === 进程级预取（首次 API 调用前消费，用户还在打字） ===
  void initUser();                           // 用户信息
  void getUserContext();                     // 用户上下文数据
  prefetchSystemContextIfSafe();             // 系统/git 上下文
  void getRelevantTips();                    // 使用提示

  // === 云提供商认证预取 ===
  if (isEnvTruthy(process.env.CLAUDE_CODE_USE_BEDROCK) &&
      !isEnvTruthy(process.env.CLAUDE_CODE_SKIP_BEDROCK_AUTH)) {
    void prefetchAwsCredentialsAndBedRockInfoIfSafe();    // AWS 凭证
  }
  if (isEnvTruthy(process.env.CLAUDE_CODE_USE_VERTEX) &&
      !isEnvTruthy(process.env.CLAUDE_CODE_SKIP_VERTEX_AUTH)) {
    void prefetchGcpCredentialsIfSafe();                  // GCP 凭证
  }

  // === 分析和功能标志 ===
  void countFilesRoundedRg(getCwd(), AbortSignal.timeout(3000), []);
  void initializeAnalyticsGates();           // 分析门控初始化
  void prefetchOfficialMcpUrls();            // 官方 MCP 服务器 URL 缓存
  void refreshModelCapabilities();           // 模型能力刷新

  // === 文件变更检测器 ===
  void settingsChangeDetector.initialize();  // 设置文件变更检测
  if (!isBareMode()) {
    void skillChangeDetector.initialize();   // 技能文件变更检测
  }

  // === 事件循环卡顿检测（仅内部版本） ===
  if ("external" === 'ant') {                // Feature Flag DCE 在外部版本中消除这段代码
    void import('./utils/eventLoopStallDetector.js')
      .then(m => m.startEventLoopStallDetector());
  }
}
```

预取操作分为四类，每类有不同的紧急程度：

| 预取类别 | 操作 | 被消费时机 | 紧急度 |
|---------|------|-----------|--------|
| 用户上下文 | `initUser()`, `getUserContext()`, `getSystemContext()` | 首次 API 调用的系统提示构建 | 高 |
| 云认证 | AWS/GCP 凭证预取 | 首次 API 调用 | 高（条件性） |
| 分析缓存 | `countFilesRoundedRg`, `initializeAnalyticsGates` | 事件日志记录 | 低 |
| 变更检测 | `settingsChangeDetector`, `skillChangeDetector` | 文件修改时的自动刷新 | 低 |

### 5.5 getRenderContext：渲染基础设施

```typescript
// src/interactiveHelpers.tsx:299-312

export function getRenderContext(exitOnCtrlC: boolean): {
  renderOptions: RenderOptions;
  getFpsMetrics: () => FpsMetrics | undefined;
  stats: StatsStore;
} {
  const baseOptions = getBaseRenderOptions(exitOnCtrlC);
  const fpsTracker = new FpsTracker();       // FPS 追踪器
  const stats = createStatsStore();          // 性能统计存储
  setStatsStore(stats);                      // 全局注册

  return {
    getFpsMetrics: () => fpsTracker.getMetrics(),
    stats,
    renderOptions: {
      ...baseOptions,
      onFrame: event => {                    // 每帧回调
        fpsTracker.record(event.durationMs); // 记录帧耗时
        stats.observe('frame_duration_ms', event.durationMs);
        // ... 帧时序日志（仅 bench 模式）
        // ... 闪烁检测（无同步输出支持时）
      }
    }
  };
}
```

`getRenderContext()` 创建了 REPL 渲染所需的基础设施：FPS 追踪器监控渲染性能，StatsStore 收集统计数据，`onFrame` 回调在每一帧渲染后执行。在 bench 模式下（通过 `CLAUDE_CODE_FRAME_TIMING_LOG` 环境变量启用），还会将每帧的 yoga 布局、屏幕缓冲、差分计算、优化、stdout 写入各阶段的耗时以 JSONL 格式同步写入日志文件，同时附带 RSS 内存和 CPU 使用率快照。同步写入确保了即使进程突然退出也不会丢失帧数据。

此外，`onFrame` 还包含闪烁检测逻辑：当终端不支持同步输出（DEC 2026 BSU/ESU 缓冲）时，检测并报告清屏+重绘导致的可见闪烁，帮助诊断终端兼容性问题。

---

## 第六章：完整启动时序图

以下是从 `claude` 命令到 REPL 就绪的完整启动时序图，标注了每个步骤涉及的源文件、近似耗时、以及并行/延迟执行关系：

```
                    Claude Code 完整启动时序图
                    ═══════════════════════════

时间    │ 事件                                 文件/函数
────────┼──────────────────────────────────────────────────────────
 0ms    │ $ claude                             终端
        │ ↓ Bun 加载 cli.tsx
        │
 ~1ms   │ cli_entry                            src/entrypoints/cli.tsx:48
        │ ├── --version 快速路径检查            零模块加载直接退出
        │ └── import('./main.js')              ──→ 开始模块求值
        │     ↓
 ~2ms   │ main_tsx_entry                       src/main.tsx:12
        │ ├── profileCheckpoint('main_tsx_entry')
        │ ├── startMdmRawRead()         ─────→ 【并行】macOS plutil 子进程
        │ └── startKeychainPrefetch()   ─────→ 【并行】两个钥匙串读取子进程
        │     ↓ 后续 ~135ms 的模块 import 求值
        │     ↓ （MDM 和钥匙串 I/O 在此期间并行完成）
        │
~136ms  │ main_tsx_imports_loaded              src/main.tsx:209
        │ └── run() 开始执行
        │     ├── program.parseAsync()         Commander.js 参数解析
        │     └── 进入 preAction 钩子
        │
~141ms  │ preAction_start                      src/main.tsx:908
        │ ├── ensureKeychainPrefetchCompleted() 确认钥匙串读取完成
        │ ├── waitForMdmRawReadToComplete()     确认 MDM 读取完成
        │ └── await init()              ──────→ src/entrypoints/init.ts
        │
        │     ┌─── init() 同步关键路径 ───────────────────────┐
        │     │                                                │
        │     │  enableConfigs()              配置系统启用       │
        │     │  applySafeConfigEnvironmentVariables()          │
        │     │                               安全环境变量      │
        │     │  applyExtraCACertsFromConfig() CA 证书注入      │
        │     │  setupGracefulShutdown()       退出清理注册      │
        │     │                                                │
        │     │  ┌── 异步即发即忘 ──────────────────────┐      │
        │     │  │ void Promise.all([                   │      │
        │     │  │   1P事件日志, GrowthBook              │      │
        │     │  │ ])                                   │      │
        │     │  │ void populateOAuthAccountInfoIfNeeded │      │
        │     │  │ void initJetBrainsDetection          │      │
        │     │  │ void detectCurrentRepository         │      │
        │     │  │ initializeRemoteManagedSettings...   │      │
        │     │  │ initializePolicyLimitsLoading...     │      │
        │     │  └──────────────────────────────────────┘      │
        │     │                                                │
        │     │  configureGlobalMTLS()        mTLS 配置         │
        │     │  configureGlobalAgents()      HTTP 代理配置     │
        │     │  preconnectAnthropicApi()  ──→ 【并行】TCP+TLS  │
        │     │                               握手 ~100-200ms  │
        │     │  registerCleanup(...)         清理回调注册       │
        │     │                                                │
        │     └────────────────────────────────────────────────┘
        │
~160ms  │ preAction_after_init
        │ ├── initSinks()                      日志接收器初始化
        │ ├── runMigrations()                  配置格式迁移
        │ └── void loadRemoteManagedSettings() 【并行】远程设置加载
        │
~175ms  │ preAction_complete
        │
        │ ═══ action 处理器 ═══════════════════════════════════
        │
~176ms  │ action_handler_start                 src/main.tsx:1007
        │ ├── 参数解析和验证               ~800 行参数处理代码
        │ ├── 交互式 vs 非交互式判定
        │ └── getTools()                       工具列表加载
        │
~200ms  │ action_tools_loaded
        │ ├── initBuiltinPlugins()             <1ms, 纯内存注册
        │ ├── initBundledSkills()              <1ms, 纯内存注册
        │ │
        │ ├── setup()          ─────┐
        │ │   ├── UDS socket 初始化  │         【三者并行执行】
        │ │   ├── hooks 快照         │
        │ │   └── 后台任务启动       │
        │ ├── getCommands()    ─────┤
        │ └── getAgentDefs()   ─────┘
        │
~230ms  │ action_after_setup
        │ ├── MCP 连接配置加载
        │ ├── 插件加载
        │ └── 模型解析
        │
~280ms  │ action_mcp_configs_loaded
        │
        │ ═══ 交互式路径（非交互式在此分叉） ══════════════════
        │
        │ showSetupScreens()                   src/interactiveHelpers.tsx
        │ ├── [1] Onboarding                   首次引导（条件性）
        │ ├── [2] TrustDialog                  信任确认
        │ │       ↓ setSessionTrustAccepted(true)
        │ │       ↓ resetGrowthBook()
        │ │       ↓ getSystemContext()
        │ │       ↓ handleMcpjsonServerApprovals()
        │ ├── [3] applyConfigEnvironmentVariables()  完整环境变量
        │ ├── [4] setImmediate(initializeTelemetryAfterTrust)
        │ │       ↓ 推迟到下一 tick
        │ ├── [5] GroveDialog                  策略确认（条件性）
        │ ├── [6] ApproveApiKey                API 密钥确认（条件性）
        │ ├── [7] BypassPermissionsModeDialog   危险模式确认（条件性）
        │ ├── [8] AutoModeOptInDialog           自动模式同意（条件性）
        │ └── [9] DevChannelsDialog             开发频道确认（条件性）
        │
        │ launchRepl()                         src/replLauncher.tsx
        │ ├── await import('./components/App.js')    动态加载
        │ └── await import('./screens/REPL.js')      动态加载
        │
        │ renderAndRun()                       src/interactiveHelpers.tsx:98
        │ ├── root.render(<App><REPL/></App>)  ──→ 首次绘制！
        │ │
        │ ├── startDeferredPrefetches()        src/main.tsx:388
        │ │   ├── void initUser()              【后台】用户信息
        │ │   ├── void getUserContext()         【后台】用户上下文
        │ │   ├── prefetchSystemContextIfSafe() 【后台】系统上下文
        │ │   ├── void getRelevantTips()       【后台】使用提示
        │ │   ├── void AWS/GCP 凭证预取        【后台】条件性
        │ │   ├── void countFilesRoundedRg()   【后台】文件计数
        │ │   ├── void initializeAnalyticsGates 【后台】分析门控
        │ │   ├── void prefetchOfficialMcpUrls  【后台】MCP URL
        │ │   ├── void refreshModelCapabilities 【后台】模型能力
        │ │   ├── void settingsChangeDetector   【后台】变更检测
        │ │   └── void skillChangeDetector      【后台】变更检测
        │ │
~300ms  │ └── REPL 就绪 ★                     等待用户输入
        │
        │     ┌──────────────────────────────────────────────────┐
        │     │  用户打字期间（~2-10秒）：                        │
        │     │  • 所有后台预取任务并行完成                        │
        │     │  • API 预连接已建立                               │
        │     │  • 遥测异步初始化                                  │
        │     │  • 当用户按下 Enter 时，大部分数据已就绪            │
        │     └──────────────────────────────────────────────────┘
```

整个启动流程从 0ms 到 REPL 就绪约 ~300ms，其中**纯等待时间接近于零**——每一个延迟窗口都被利用来做并行 I/O 或后台预取。

### 时序图解读：五个并行窗口

上图中有五个关键的并行窗口值得特别关注：

**窗口一：模块求值 + I/O 并行（0-136ms）。** `startMdmRawRead()` 和 `startKeychainPrefetch()` 在模块 `import` 链开始前启动。这两个操作都是子进程级 I/O（`plutil` 读取 MDM 配置、`security find-generic-password` 读取钥匙串），它们在操作系统层面并行，不受 JavaScript 单线程限制。当 ~135ms 的模块求值完成时，I/O 通常也已完成，`ensureKeychainPrefetchCompleted()` 和 `waitForMdmRawReadToComplete()` 可以立即返回。

**窗口二：init() 即发即忘 + 同步路径并行（141-160ms）。** `init()` 中的 `void` 操作（分析日志、OAuth、IDE 检测等）与同步关键路径（mTLS、代理配置）并行执行。即使某个 `void` 操作因为网络原因延迟了几百毫秒，也完全不影响启动速度。

**窗口三：API 预连接 + action 处理器并行（160-280ms）。** `preconnectAnthropicApi()` 启动的 TCP + TLS 握手需要 ~100-200ms，恰好与 action 处理器的参数解析、工具加载、setup 等工作并行。这是一个经典的"把网络延迟藏在 CPU 工作后面"的优化。

**窗口四：setup/commands/agentDefs 三路并行（200-230ms）。** `setup()`（UDS socket、hooks 快照、后台任务）、`getCommands()`（命令列表）、`getAgentDefinitionsWithOverrides()`（Agent 定义）三个独立操作同时执行。但如果 `--worktree` 启用，`setup()` 必须先完成（因为 worktree 会通过 `process.chdir()` 改变工作目录），此时三路并行降级为串行。

**窗口五：用户打字 + 后台预取（300ms+）。** 这是最大的并行窗口，也是最巧妙的。`startDeferredPrefetches()` 启动 12+ 个后台任务，它们在用户思考和打字的几秒钟内完成。当用户按下 Enter 发送第一条消息时，用户上下文、系统上下文、模型能力等数据都已准备就绪，API 调用可以立即包含完整的上下文信息。

---

## 设计哲学分析

启动流程是 Claude Code 设计哲学最集中的体现。在短短 ~300ms 的启动窗口中，我们可以观察到至少六种设计哲学的交织运作。

### Performance-Conscious Startup（性能敏感启动）

整个启动流程最核心的哲学就是**性能敏感启动**。这不是简单的"让启动变快"，而是一个系统性的多维优化策略，体现在四个层次：

**第一层：利用不可避免的延迟做并行 I/O。** 模块求值的 ~135ms 不可避免，但 `main.tsx` 在这段时间内并行启动了 MDM 子进程和钥匙串读取。这不是简单的"多线程"思维——而是对 JavaScript 单线程模型的深刻理解：模块求值是同步的 CPU 工作，I/O 操作是异步的等待，两者可以完美重叠。

**第二层：将初始化分为阻塞和非阻塞两类。** `init()` 中的同步关键路径（配置、CA 证书、网络代理）必须完成才能继续，因为后续操作依赖它们。但分析日志、OAuth 缓存、IDE 检测等操作以 `void` 方式异步启动，完全不阻塞主流程。这种分类需要对每个操作的依赖关系有精确理解。

**第三层：将用户行为转化为预取窗口。** `startDeferredPrefetches()` 在首次渲染**之后**启动，利用用户阅读界面和打字的几秒钟做后台工作。这个设计基于一个行为洞察：用户总是需要时间思考和输入，而这段时间对程序来说就是免费的 I/O 窗口。`--bare` 模式跳过所有预取，因为脚本化调用没有"用户打字"窗口。

**第四层：API 预连接与工作并行。** `preconnectAnthropicApi()` 在 `init()` 末尾启动 TCP + TLS 握手，与 action 处理器的参数解析 ~100ms 并行。当第一个 API 请求发出时，连接已经建立，节省了一次完整的握手延迟。

### Graceful Degradation（优雅降级）

启动流程在多个层面展示了优雅降级的思想：

**系统在完全就绪之前就可用。** 首次渲染在所有预取完成之前就发生了——用户可以立即看到 REPL 界面并开始打字。预取任务在后台异步完成，如果某个预取失败（例如网络不可用），系统仍然可以正常运行，只是首次响应可能稍慢。

**即发即忘操作的容错设计。** `init()` 中的 `void` 操作全部是非阻塞的，它们的失败不会影响启动流程。例如 `detectCurrentRepository()` 失败只意味着 PR 链接功能不可用，但核心功能完全不受影响。

**上游代理的 fail-open 策略。** CCR 上游代理初始化被 `try/catch` 包裹，失败时记录日志并继续——宁可在没有代理的情况下运行（可能某些功能受限），也不让代理故障阻止整个系统启动。

**配置错误的分级处理。** `ConfigParseError` 根据运行模式（交互式 vs 非交互式）采取不同策略：交互式显示修复对话框，非交互式写入 stderr 并退出。这确保了 CI/CD 管道中的 JSON 消费者不会因为一个配置修复对话框而卡住。

### Isolation & Containment（隔离与遏制）

**两阶段环境变量是安全隔离的典型实现。** 第一阶段只应用白名单中的安全变量，将潜在危险的环境变量隔离在信任屏障之后。这不是技术限制——完全可以一次性加载全部环境变量——而是刻意的安全设计：不信任的代码库可能通过 `.claude/settings.json` 注入恶意环境变量，例如将 API 端点重定向到攻击者控制的服务器。

**每个子系统独立初始化。** `init()` 中的异步操作互不依赖：OAuth 缓存填充不依赖 JetBrains 检测结果，GrowthBook 初始化不依赖 GitHub 仓库检测。这种隔离确保了一个子系统的延迟或失败不会传播到其他子系统。远程管理设置的 Promise 初始化带有超时保护，明确防止了死锁——如果 `loadRemoteManagedSettings()` 从未被调用（例如在 Agent SDK 测试中），超时保证了等待者不会永远阻塞。

### Extensibility Without Modification（无需修改的可扩展性）

**`registerCleanup()` 模式是开放-封闭原则的启动时体现。** 新的子系统只需注册自己的清理回调，不需要修改 `gracefulShutdown()` 的实现。`init()` 通过 `registerCleanup()` 注册了 LSP 管理器关闭和会话团队清理，但清理注册表对这些具体实现一无所知。

**条件初始化通过环境变量和 Feature Flag 扩展。** CCR 上游代理仅在 `CLAUDE_CODE_REMOTE` 环境下初始化，Bedrock/Vertex 凭证预取仅在相应环境变量启用时执行。添加新的云提供商支持只需在 `startDeferredPrefetches()` 中添加新的条件块，不需要修改现有代码。

**延迟导入实现了零成本抽象。** 团队清理的 `teamHelpers.js` 和 CCR 上游代理的 `upstreamproxy.js` 都通过 `await import()` 延迟加载。这意味着即使这些功能存在于代码库中，不使用它们的会话**完全不会为它们付出模块加载代价**。

### Human-in-the-Loop（人在回路）

**`showSetupScreens()` 是信任的人机接口。** 从 Onboarding 到 TrustDialog 到 AutoModeOptInDialog，每一步都是在用户参与下建立信任层级。特别是 TrustDialog——它不是一个可以跳过的警告框，而是一个**安全边界**：信任对话之前和之后，系统的行为是不同的（环境变量、GrowthBook 认证、遥测等都在信任之后才完全启用）。

**非交互式模式有意跳过全部对话。** `showSetupScreens()` 在非交互式模式下根本不会被调用，因为 CI/CD 管道没有人来点击"接受"。这种模式下，环境变量、API 密钥等安全决策由配置文件和环境变量预先做出，而不是运行时交互。

### Defensive Programming（防御性编程）

**Memoize 防止重复初始化。** `init()` 被 `memoize()` 包装，`doInitializeTelemetry()` 使用 `telemetryInitialized` 标志——两种不同的防御策略确保初始化代码不会因为多个调用者而执行多次。遥测的标志管理甚至考虑了失败场景：初始化失败后重置标志允许重试。

**CA 证书在 TLS 之前注入。** `applyExtraCACertsFromConfig()` 在 `preconnectAnthropicApi()` 之前调用，因为 Bun（通过 BoringSSL）在启动时缓存 TLS 证书库。如果顺序反了，自定义 CA 证书将不会被使用，导致企业环境中的 TLS 连接失败。注释中解释了"为什么顺序重要"——这种文档化的顺序依赖是防御性编程的重要实践。

**超时保护远程设置加载。** `initializeRemoteManagedSettingsLoadingPromise()` 含超时，防止网络问题导致整个系统卡住等待远程设置。这是"不信任外部系统的可用性"原则的体现。

---

## 关键要点总结

1. **`init()` 的 memoize 保护**：lodash-es `memoize()` 确保多个调用者不会触发重复初始化，返回同一个缓存 Promise
2. **两阶段环境变量**：`applySafeConfigEnvironmentVariables()`（信任前，白名单变量）→ `applyConfigEnvironmentVariables()`（信任后，全部变量），安全隔离潜在危险配置
3. **即发即忘并行模式**：`void Promise.all([...])` 和 `void asyncFn()` 启动非阻塞操作，将 12+ 个初始化任务的延迟从串行变并行
4. **`showSetupScreens()` 的信任阶梯**：10 步对话序列建立安全上下文，每一步都有严格的执行顺序——信任是**渐进的**，不是全有或全无
5. **`renderAndRun()` 的四行精确编排**：渲染 → 预取 → 等待 → 清理，确保用户最快看到界面，预取不阻塞首次绘制
6. **`startDeferredPrefetches()` 利用用户行为**：12+ 个后台预取任务在用户打字期间完成，`--bare` 模式智能跳过（脚本调用没有打字窗口）
7. **完整启动时序图**：从 cli.tsx 到 REPL 就绪 ~300ms，每一个延迟窗口都被利用做并行 I/O——纯等待时间接近于零
8. **六大设计哲学交织**：性能敏感启动（四层优化）、优雅降级（部分就绪即可用）、隔离与遏制（两阶段安全）、无需修改的可扩展性（registerCleanup）、人在回路（信任对话）、防御性编程（memoize + 超时保护）

## 下一篇预览

**Doc 4：终端 UI 系统** 将深入 React + Ink 框架如何在终端中渲染界面。我们将分析 Ink 框架的核心机制（与 React DOM 的对比）、~140 个组件的分类体系、输入处理流程（PromptInput 和 typeahead 自动补全）、5,005 行的 REPL 主屏幕深度解析、消息渲染系统、以及 Hook 与权限系统的集成。特别关注 React 组件模型如何体现"可组合性"设计哲学，以及 Ink 终端约束如何迫使"防御性编程"（处理终端 resize、颜色支持检测等）。
