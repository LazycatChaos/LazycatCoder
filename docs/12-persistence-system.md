# Doc 12：持久化系统

> **前置阅读：** Doc 0 ~ Doc 11
> **核心问题：** Claude Code 如何在本地和跨机器间持久化会话状态、记忆知识和文件缓存——实现崩溃恢复、会话续接和上下文迁移？
> **设计哲学重点：** 无需修改的可扩展性、优雅降级、上下文窗口经济学、安全优先设计、可组合性、防御性编程

---

## 第一章：会话存储 `src/utils/sessionStorage.ts`

### 1.1 会话存储的核心角色

`sessionStorage.ts` 是 Claude Code 最大的单文件之一（5000+ 行），承担着整个系统的"记忆中枢"角色。每一次用户与 Claude 的对话——包括用户消息、AI 回复、工具调用结果、子代理交互——都通过这个模块持久化到磁盘，使得崩溃恢复、会话续接和历史回溯成为可能。

### 1.2 存储位置与目录结构

所有会话数据存储在用户主目录下的 `.claude/projects/` 目录中：

```
~/.claude/projects/
├── -Users-alice-myproject/           # 项目目录（路径转义后作为目录名）
│   ├── abc123-def456.jsonl           # 主会话文件（sessionId.jsonl）
│   ├── abc123-def456/                # 会话子目录
│   │   └── subagents/               # 子代理转录
│   │       ├── agent-xyz.jsonl       # 子代理会话记录
│   │       ├── agent-xyz.meta.json   # 子代理元数据（JSON sidecar）
│   │       └── workflows/            # 工作流子目录
│   │           └── run-001/
│   │               └── agent-w.jsonl
│   └── remote-agents/                # 远程代理
│       └── remote-agent-task1.meta.json
```

关键路径计算函数：

```typescript
// src/utils/sessionStorage.ts, 第 198-204 行
export function getProjectsDir(): string {
  // 返回 ~/.claude/projects —— 所有项目会话的根目录
  return join(getClaudeConfigHomeDir(), 'projects')
}

export function getTranscriptPath(): string {
  // 获取当前会话的 JSONL 文件路径
  // projectDir 优先使用 sessionProjectDir（resume 场景），否则从 originalCwd 计算
  const projectDir = getSessionProjectDir() ?? getProjectDir(getOriginalCwd())
  return join(projectDir, `${getSessionId()}.jsonl`)
}
```

子代理的转录路径同样遵循层级结构：

```typescript
// src/utils/sessionStorage.ts, 第 247-254 行
export function getAgentTranscriptPath(agentId: AgentId): string {
  // 子代理转录存储在主会话的 subagents/ 子目录下
  const projectDir = getSessionProjectDir() ?? getProjectDir(getOriginalCwd())
  const sessionId = getSessionId()
  const subdir = agentTranscriptSubdirs.get(agentId)  // 可选的工作流子目录
  const base = subdir
    ? join(projectDir, sessionId, 'subagents', subdir)  // workflows/runId/ 分组
    : join(projectDir, sessionId, 'subagents')           // 默认扁平
  return join(base, `agent-${agentId}.jsonl`)
}
```

### 1.3 JSONL 格式与条目类型

会话文件使用 **JSONL（JSON Lines）** 格式——每行一个独立的 JSON 对象。这是一种 append-only（只追加）的日志格式，具有天然的崩溃安全性：即使写入过程中断电，最多丢失最后一个不完整的行，之前的所有数据都完整保留。

每个消息条目（`TranscriptMessage`）包含以下关键字段：

```typescript
// TranscriptMessage 核心字段
{
  "parentUuid": "abc-123-...",  // 父消息 UUID，构成链式结构
  "uuid": "def-456-...",        // 本条目唯一标识
  "timestamp": "2026-04-01...", // ISO 时间戳
  "sessionId": "abc123-def456", // 所属会话 ID
  "type": "user",               // 消息类型：user | assistant | attachment | system
  "message": { ... },           // 实际消息内容（遵循 API 消息格式）
  "isSidechain": false          // 是否为侧链（子代理产生的消息）
}
```

除了对话消息外，JSONL 文件还存储多种**元数据条目**：

| 条目类型 | 用途 |
|---------|------|
| `summary` | 会话摘要（上下文压缩后生成） |
| `custom-title` | 用户自定义的会话标题 |
| `ai-title` | AI 自动生成的会话标题 |
| `tag` | 会话标签 |
| `agent-name` / `agent-color` | 代理名称和颜色标识 |
| `mode` | 运行模式（coordinator / normal） |
| `worktree-state` | Worktree 隔离状态 |
| `pr-link` | 关联的 Pull Request 信息 |
| `file-history-snapshot` | 文件修改历史快照 |
| `attribution-snapshot` | 代码归属快照（用于 git blame） |
| `content-replacement` | 上下文压缩时的内容替换记录 |
| `queue-operation` | 队列操作记录 |

### 1.4 `Project` 类：写入引擎

所有会话写入操作由 `Project` 类（第 532 行）统一管理。它的设计体现了几个关键的工程决策：

**延迟物化（Lazy Materialization）：** 会话文件不在启动时创建，而是在第一条用户或助手消息到达时才创建。在此之前，所有条目（如元数据）都缓冲在内存中：

```typescript
// src/utils/sessionStorage.ts, 第 976-990 行
private async materializeSessionFile(): Promise<void> {
  // 防止 --no-session-persistence 模式下创建文件
  if (this.shouldSkipPersistence()) return
  this.ensureCurrentSessionFile()       // 确定文件路径
  this.reAppendSessionMetadata()         // 写入缓存的元数据
  if (this.pendingEntries.length > 0) {  // 刷写缓冲的条目
    const buffered = this.pendingEntries
    this.pendingEntries = []
    for (const entry of buffered) {
      await this.appendEntry(entry)
    }
  }
}
```

**批量异步写入：** 写操作通过每文件独立的队列进行批量处理，避免高频写入造成 I/O 瓶颈：

```typescript
// src/utils/sessionStorage.ts, 第 606-631 行
private enqueueWrite(filePath: string, entry: Entry): Promise<void> {
  return new Promise<void>(resolve => {
    let queue = this.writeQueues.get(filePath)  // 每个文件一个队列
    if (!queue) {
      queue = []
      this.writeQueues.set(filePath, queue)
    }
    queue.push({ entry, resolve })              // 入队，附带 resolve 回调
    this.scheduleDrain()                         // 调度排空
  })
}

private scheduleDrain(): void {
  if (this.flushTimer) return                    // 已有定时器，跳过
  this.flushTimer = setTimeout(async () => {
    this.flushTimer = null
    this.activeDrain = this.drainWriteQueue()    // 批量写入磁盘
    await this.activeDrain
    this.activeDrain = null
    if (this.writeQueues.size > 0) {
      this.scheduleDrain()                       // 排空期间有新条目，再次调度
    }
  }, this.FLUSH_INTERVAL_MS)                     // 100ms 间隔批次
}
```

关键参数：
- `FLUSH_INTERVAL_MS = 100`：每 100ms 刷写一次
- `MAX_CHUNK_BYTES = 100 * 1024 * 1024`：单批次最大 100MB
- 文件权限 `0o600`：仅当前用户可读写

### 1.5 链式消息与对话树

Claude Code 的会话不是简单的线性列表，而是一棵**有向无环图（DAG）**。每条消息通过 `parentUuid` 指向其父消息，支持分叉（用户撤回并重新提问）和并行工具调用。

加载会话时，`buildConversationChain()` 从最新的叶子节点向根节点回溯，构建当前活跃的对话链：

```typescript
// src/utils/sessionStorage.ts, 第 2069-2094 行
export function buildConversationChain(
  messages: Map<UUID, TranscriptMessage>,
  leafMessage: TranscriptMessage,
): TranscriptMessage[] {
  const transcript: TranscriptMessage[] = []
  const seen = new Set<UUID>()           // 环检测
  let currentMsg: TranscriptMessage | undefined = leafMessage
  while (currentMsg) {
    if (seen.has(currentMsg.uuid)) {     // 发现循环——防御性编程
      logError(new Error(`Cycle detected in parentUuid chain...`))
      break
    }
    seen.add(currentMsg.uuid)
    transcript.push(currentMsg)
    currentMsg = currentMsg.parentUuid
      ? messages.get(currentMsg.parentUuid)  // 沿 parentUuid 向上追溯
      : undefined
  }
  transcript.reverse()                   // 反转为从根到叶的顺序
  // 恢复并行工具调用中被单链遍历遗漏的兄弟节点
  return recoverOrphanedParallelToolResults(messages, transcript, seen)
}
```

这个设计意味着"撤回"操作是零成本的——用户的新输入只需指向更早的父节点，旧的分支永远留在文件中但不会被加载。

### 1.6 大文件优化：字节级预扫描

当会话文件增长到 5MB 以上时，逐行 JSON 解析变得昂贵。`walkChainBeforeParse()`（第 3306 行）在 JSON 解析之前直接扫描原始字节缓冲区，提取 `uuid` 和 `parentUuid` 字段，快速构建消息关系图，然后只解析活跃链条上的消息：

```typescript
// src/utils/sessionStorage.ts, 第 3306-3315 行
function walkChainBeforeParse(buf: Buffer): Buffer {
  const NEWLINE = 0x0a              // '\n' 的字节值
  const OPEN_BRACE = 0x7b           // '{' 的字节值
  const PARENT_PREFIX = Buffer.from('{"parentUuid":')  // 消息行的固定前缀
  const UUID_KEY = Buffer.from('"uuid":"')
  const UUID_LEN = 36               // UUID 的固定长度
  // ... 逐字节扫描，跳过非活跃分支的行
}
```

这种字节级优化在分叉密集的会话中可以减少 80-93% 的解析工作量。

### 1.7 读取限制与安全边界

```typescript
// src/utils/sessionStorage.ts, 第 227-229 行
// 50 MB —— 会话 JSONL 可以增长到数 GB（inc-3930）。
// 读取原始转录的调用方必须在此阈值以上中止以避免 OOM。
export const MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024
```

---

## 第二章：记忆系统 `src/memdir/`

### 2.1 记忆系统概述

如果说会话存储是 Claude Code 的"短期记忆"（当前对话的完整记录），那么记忆系统（`src/memdir/`）就是它的"长期记忆"——跨会话持久化的知识库，让 Claude 在新的对话中能够回忆起用户偏好、项目背景和历史决策。

记忆系统由 8 个核心模块组成：

| 模块 | 职责 |
|------|------|
| `memdir.ts` | 记忆提示词构建与注入 |
| `memoryTypes.ts` | 记忆类型分类（user / feedback / project / reference） |
| `paths.ts` | 记忆目录路径解析与验证 |
| `memoryScan.ts` | 扫描记忆文件、解析 frontmatter |
| `findRelevantMemories.ts` | 通过 Sonnet 分类器选择相关记忆 |
| `memoryAge.ts` | 记忆新鲜度追踪 |
| `teamMemPaths.ts` | 团队记忆路径与安全验证 |
| `teamMemPrompts.ts` | 团队+个人记忆联合提示词构建 |

### 2.2 记忆文件格式

每个记忆是一个独立的 Markdown 文件，使用 YAML frontmatter 标注元数据：

```markdown
---
name: user_preferences
description: User prefers terse responses, is a senior Go developer
type: user
---

用户是一名资深 Go 开发者，偏好简洁的回复风格。
第一次接触 React 前端代码。
```

入口文件 `MEMORY.md` 作为索引，指向各个记忆文件：

```markdown
- [User Preferences](user_preferences.md) — senior Go dev, prefers terse
- [Project Context](project_context.md) — auth rewrite for compliance
```

### 2.3 记忆目录路径解析

记忆目录的位置通过三级优先级链确定：

```typescript
// src/memdir/paths.ts, 第 223-235 行
export const getAutoMemPath = memoize(
  (): string => {
    // 1. 环境变量覆盖（Cowork 协作模式使用）
    // 2. settings.json 中的 autoMemoryDirectory 设置
    const override = getAutoMemPathOverride() ?? getAutoMemPathSetting()
    if (override) return override

    // 3. 默认路径：~/.claude/projects/<sanitized-git-root>/memory/
    const projectsDir = join(getMemoryBaseDir(), 'projects')
    return (
      join(projectsDir, sanitizePath(getAutoMemBase()), AUTO_MEM_DIRNAME) + sep
    ).normalize('NFC')
  },
  () => getProjectRoot(),  // 以项目根目录为缓存键
)
```

关键设计：**同一仓库的所有 worktree 共享同一个记忆目录**。`getAutoMemBase()`（第 203 行）使用 `findCanonicalGitRoot()` 获取规范的 Git 根路径，确保主仓库和 worktree 指向相同的记忆。

### 2.4 记忆扫描与发现

`scanMemoryFiles()`（`memoryScan.ts` 第 35 行）递归扫描记忆目录中的所有 `.md` 文件（排除 `MEMORY.md` 本身），提取 frontmatter 中的描述和类型信息：

```typescript
// src/memdir/memoryScan.ts, 第 35-63 行
export async function scanMemoryFiles(
  memoryDir: string,
  signal: AbortSignal,      // 支持取消信号
): Promise<MemoryHeader[]> {
  const entries = await readdir(memoryDir, { recursive: true })
  const mdFiles = entries.filter(
    f => f.endsWith('.md') && basename(f) !== 'MEMORY.md'
  )
  // 并行读取所有文件的前 30 行（frontmatter 通常在此范围内）
  const headerResults = await Promise.allSettled(
    mdFiles.map(async (relativePath): Promise<MemoryHeader> => {
      const filePath = join(memoryDir, relativePath)
      const { content, mtimeMs } = await readFileInRange(
        filePath, 0, FRONTMATTER_MAX_LINES, undefined, signal
      )
      const { frontmatter } = parseFrontmatter(content, filePath)
      return {
        filename: relativePath,
        filePath,
        mtimeMs,                    // 修改时间，用于新鲜度排序
        description: frontmatter.description || null,
        type: parseMemoryType(frontmatter.type),
      }
    })
  )
  // 按修改时间降序排列，限制最多 200 个文件
}
```

### 2.5 记忆注入流程

记忆通过两个注入点进入 AI 上下文：

**系统提示词注入**（`loadMemoryPrompt()`，第 419 行）：在系统提示词中包含记忆类型定义、使用指南和 `MEMORY.md` 索引内容。`MEMORY.md` 的内容被截断至最多 200 行 / 25KB：

```typescript
// src/memdir/memdir.ts, 第 34-38 行
export const ENTRYPOINT_NAME = 'MEMORY.md'
export const MAX_ENTRYPOINT_LINES = 200   // 索引文件最多显示 200 行
export const MAX_ENTRYPOINT_BYTES = 25_000 // 约 25KB 字节上限
```

**用户上下文注入**：通过 `getClaudeMds()` 加载层级化的 CLAUDE.md 文件，在每轮对话前提供项目指令。

### 2.6 CLAUDE.md 层级系统

CLAUDE.md 是一个独立于 auto-memory 的**层级指令系统**，提供从全局到项目的多层配置：

```
优先级（从低到高）：
1. Managed（系统级规则）
2. User（~/.claude/CLAUDE.md —— 用户全局指令）
3. Project（仓库根目录 CLAUDE.md —— 项目级指令）
4. Project .claude/rules/*.md（条件规则，按 glob 模式匹配文件路径）
5. Local（CLAUDE.local.md —— 用户私有的项目本地指令）
```

发现机制从当前工作目录向上遍历到文件系统根目录，在每一层检查 `CLAUDE.md`、`.claude/CLAUDE.md` 和 `.claude/rules/*.md`。

### 2.7 记忆类型分类

记忆被分为四种类型，每种有不同的存储和召回策略：

| 类型 | 描述 | 典型内容 |
|------|------|---------|
| `user` | 用户画像 | 角色、技能水平、偏好 |
| `feedback` | 行为反馈 | 应做/不应做的事项 |
| `project` | 项目上下文 | 目标、截止日期、架构决策 |
| `reference` | 外部资源指针 | 文档链接、工具位置 |

---

## 第三章：附件处理 `src/utils/attachments.ts`

### 3.1 附件系统概述

附件系统是连接记忆系统和 AI 上下文的桥梁。当用户通过 `@` 引用文件、系统自动注入 CLAUDE.md 记忆、或团队协作传递上下文时，都通过附件机制将外部数据统一格式化后注入到对话中。

### 3.2 附件类型

系统定义了多种附件类型，每种服务于不同的上下文注入场景：

| 附件类型 | 触发方式 | 用途 |
|---------|---------|------|
| `FileAttachment` | 用户 `@` 引用文件 | 将文件内容注入对话 |
| `AlreadyReadFileAttachment` | 自动检测 | 标记已读文件，避免重复读取 |
| `NestedMemoryAttachment` | 规则匹配 | 目录层级中的 CLAUDE.md 条件注入 |
| `RelevantMemoriesAttachment` | 智能召回 | 基于上下文自动选择相关记忆 |
| `TeamContextAttachment` | 团队协作 | Swarm 模式下的代理间上下文传递 |

### 3.3 大小限制与截断策略

附件系统实施严格的大小控制，防止上下文窗口被单个大文件占满：

```
MAX_MEMORY_LINES = 200      # 单个记忆文件最多 200 行
MAX_MEMORY_BYTES = 4096      # 单个记忆文件最多 4KB
MAX_SESSION_BYTES = 60KB     # 单轮会话中所有附件的累积上限
```

截断策略优先保留文件的 frontmatter（包含描述和类型信息），即使内容被截断，AI 仍能从元数据理解文件的用途。

### 3.4 智能记忆召回

`RelevantMemoriesAttachment` 使用 Sonnet 分类器（`findRelevantMemories.ts`）根据当前对话上下文选择最相关的记忆文件。每个被召回的记忆附件包含预计算的 `header` 字段（文件路径 + 年龄信息），确保提示词缓存（prompt cache）的稳定性——相同的记忆在不同请求中产生完全相同的文本。

---

## 第四章：文件状态缓存

### 4.1 缓存架构

Claude Code 维护一个 LRU（Least Recently Used）缓存来跟踪 AI 已经"看过"的文件内容。这个缓存不仅提升性能（避免重复读取），更是上下文一致性的关键保障——它让系统知道 AI 的"文件视图"是否与磁盘上的实际内容一致。

```typescript
// src/utils/fileStateCache.ts, 第 4-15 行
export type FileState = {
  content: string           // 文件内容
  timestamp: number         // 读取时间戳
  offset: number | undefined    // 部分读取的起始偏移
  limit: number | undefined     // 部分读取的行数限制
  // 当此条目由自动注入填充（如 CLAUDE.md）且注入内容与磁盘不匹配时为 true
  // （已剥离 HTML 注释、frontmatter，或截断了 MEMORY.md）
  // 模型只看到了部分视图；Edit/Write 必须要求先显式 Read
  // content 存储的是 RAW 磁盘字节（用于 getChangedFiles 差异计算）
  isPartialView?: boolean
}
```

### 4.2 LRU 双限制淘汰

缓存使用双限制淘汰策略，同时控制条目数量和总内存占用：

```typescript
// src/utils/fileStateCache.ts, 第 30-38 行
export class FileStateCache {
  private cache: LRUCache<string, FileState>

  constructor(maxEntries: number, maxSizeBytes: number) {
    this.cache = new LRUCache<string, FileState>({
      max: maxEntries,            // 最多 100 个条目
      maxSize: maxSizeBytes,      // 总大小不超过 25MB
      // 每个条目的大小按 content 字节数计算
      sizeCalculation: value => Math.max(1, Buffer.byteLength(value.content)),
    })
  }
}
```

默认参数：
- `READ_FILE_STATE_CACHE_SIZE = 100`：最多缓存 100 个文件
- `DEFAULT_MAX_CACHE_SIZE_BYTES = 25MB`：总内存上限

### 4.3 路径标准化

所有缓存操作都先通过 `path.normalize()` 标准化路径，确保 `/foo/../bar/file.ts` 和 `/bar/file.ts` 命中同一个缓存条目：

```typescript
// src/utils/fileStateCache.ts, 第 41-56 行
get(key: string): FileState | undefined {
  return this.cache.get(normalize(key))   // 标准化后查找
}

set(key: string, value: FileState): this {
  this.cache.set(normalize(key), value)   // 标准化后存储
  return this
}
```

### 4.4 缓存合并

`mergeFileStateCaches()` 在会话恢复时合并两个缓存，使用**时间戳优先**策略——较新的条目覆盖较旧的：

```typescript
// src/utils/fileStateCache.ts, 第 129-142 行
export function mergeFileStateCaches(
  first: FileStateCache,
  second: FileStateCache,
): FileStateCache {
  const merged = cloneFileStateCache(first)
  for (const [filePath, fileState] of second.entries()) {
    const existing = merged.get(filePath)
    // 仅当新条目更新时才覆盖
    if (!existing || fileState.timestamp > existing.timestamp) {
      merged.set(filePath, fileState)
    }
  }
  return merged
}
```

### 4.5 文件读取缓存

除了文件状态缓存外，`src/utils/fileReadCache.ts` 提供了基于文件修改时间（mtime）的读取缓存：

- 缓存命中时验证 `mtime` 是否变化，变化则自动失效
- FIFO 淘汰策略，上限 1000 个条目
- 存储文件内容、检测到的编码和修改时间

---

## 第五章：会话恢复 `/resume`

### 5.1 会话恢复流程

`/resume` 命令（或 `claude --resume`）让用户回到之前的对话。恢复流程分为三个阶段：发现 → 选择 → 重建。

**阶段一：会话发现**

`loadAllProjectsMessageLogs()`（第 3963 行）扫描所有项目目录，使用两阶段渐进式加载：

1. **快速扫描阶段**：只读取文件的 stat 信息（大小、修改时间），不解析内容
2. **选择性充实阶段**：对用户可能选择的会话加载消息摘要和元数据

会话按修改时间降序排列，通过 `sessionId + leafUuid` 去重。

**阶段二：用户选择**

`LogSelector.tsx` 组件展示可恢复的会话列表，支持：
- 按时间排序的会话列表
- 按标题的深度搜索（Fuse.js 模糊匹配，阈值 0.3）
- 深度搜索限制：每个会话最多扫描 2000 条消息 / 50000 字符

```typescript
// src/components/LogSelector.tsx, 第 72-77 行
const DEEP_SEARCH_MAX_MESSAGES = 2000
const DEEP_SEARCH_MAX_TEXT_LENGTH = 50000
```

**阶段三：上下文重建**

选择会话后，系统重建完整的运行状态：

- **对话链重建**：`buildConversationChain()` 从叶子节点回溯完整对话
- **文件历史恢复**：从 `file-history-snapshot` 条目恢复 AI 已知的文件修改记录
- **代码归属恢复**：从 `attribution-snapshot` 恢复 git blame 信息
- **上下文折叠恢复**：恢复之前的压缩操作日志
- **Todo 状态恢复**：从转录中提取 TodoWrite 工具的最终状态
- **文件缓存合并**：`mergeFileStateCaches()` 合并历史缓存与当前缓存

### 5.2 Worktree 感知恢复

`loadSameRepoMessageLogs()`（第 4073 行）支持 worktree 感知的会话过滤——同一仓库下不同 worktree 的会话分别显示，通过路径前缀匹配（Windows 上不区分大小写）实现。

### 5.3 一致性检查

恢复后，`checkResumeConsistency()`（第 2224 行）验证加载的消息数量与保存时的检查点一致，不一致时发送分析事件用于诊断。

---

## 第六章：Teleport 跨机器迁移

### 6.1 Teleport 概述

Teleport 是 Claude Code 的跨机器会话迁移系统——它允许在一台机器上开始的工作无缝转移到另一台机器（例如从本地开发环境迁移到云端沙箱）。这一能力由 `src/utils/teleport/` 目录下的 4 个文件实现。

### 6.2 核心数据类型

```typescript
// src/utils/teleport/api.ts, 第 84 行
export type SessionStatus = 'requires_action' | 'running' | 'idle' | 'archived'

// src/utils/teleport/api.ts, 第 114-125 行
export type SessionContext = {
  sources: SessionContextSource[]   // Git 仓库或知识库来源
  cwd: string                       // 工作目录
  outcomes: Outcome[] | null        // 执行结果（如 GitHub PR 分支）
  custom_system_prompt: string | null
  append_system_prompt: string | null
  model: string | null
  seed_bundle_file_id?: string      // Git bundle 文件 ID（Files API）
  github_pr?: { owner: string; repo: string; number: number }
  reuse_outcome_branches?: boolean
}

// src/utils/teleport/api.ts, 第 127-136 行
export type SessionResource = {
  type: 'session'
  id: string
  title: string | null
  session_status: SessionStatus
  environment_id: string            // 运行环境 ID
  created_at: string
  updated_at: string
  session_context: SessionContext
}
```

### 6.3 Git Bundle 传输

Teleport 的核心挑战是如何将整个 Git 仓库状态传输到远程环境。解决方案是 **Git Bundle**——Git 原生的仓库打包格式：

```typescript
// src/utils/teleport/gitBundle.ts, 第 50-104 行
// 三级降级策略：--all → HEAD → squashed-root
async function _bundleWithFallback(
  gitRoot: string,
  bundlePath: string,
  maxBytes: number,       // 通常 100MB
  hasStash: boolean,      // 是否有未提交的修改
  signal: AbortSignal | undefined,
): Promise<BundleCreateResult> {
  const extra = hasStash ? ['refs/seed/stash'] : []

  // 策略 1：打包全部（所有分支、标签、历史）
  const allResult = await mkBundle('--all')
  const { size: allSize } = await stat(bundlePath)
  if (allSize <= maxBytes) return { ok: true, size: allSize, scope: 'all' }

  // 策略 2：仅打包当前分支（减少大小）
  const headResult = await mkBundle('HEAD')
  const { size: headSize } = await stat(bundlePath)
  if (headSize <= maxBytes) return { ok: true, size: headSize, scope: 'head' }

  // 策略 3：最后手段——压缩为单个无父提交的快照
  // 使用 stash 树（如果有未提交修改）以保留工作目录状态
  const treeRef = hasStash ? 'refs/seed/stash^{tree}' : 'HEAD^{tree}'
  // ... 创建 squashed-root bundle
}
```

三级降级策略确保即使是非常大的仓库也能迁移——最坏情况下只传输当前工作目录的快照（无历史记录）。

**WIP（Work In Progress）处理**：对于未提交的修改，系统使用 `git stash create` 生成一个不影响工作目录的 stash 引用，将其存储在 `refs/seed/stash` 中随 bundle 一起传输。

### 6.4 安全机制

Teleport 在多个层面确保安全：

1. **OAuth 认证**：所有 API 请求必须携带 OAuth Bearer Token（不支持 API Key）
2. **组织隔离**：每个请求携带组织 UUID，防止跨组织泄露
3. **Beta Feature 门控**：通过 `anthropic-beta` 头部控制功能可用性
4. **指数退避重试**：2s → 4s → 8s → 16s，4xx 客户端错误立即失败，仅重试网络错误和 5xx

### 6.5 环境选择

`environmentSelection.ts` 管理运行环境的选择：

| 环境类型 | 说明 |
|---------|------|
| `anthropic_cloud` | Anthropic 云端沙箱 |
| `byoc` | 自带云资源（Bring Your Own Cloud） |
| `bridge` | 桥接模式（本地-远程混合） |

选择优先级：用户配置的 `defaultEnvironmentId` > 第一个非 bridge 环境。

---

## 第七章：持久化架构图

### 7.1 数据存储位置全景

```
~/.claude/
├── CLAUDE.md                          # 用户全局指令
├── settings.json                      # 用户设置
├── credentials.json                   # 凭证存储
├── projects/
│   └── -Users-alice-myproject/        # 项目会话目录
│       ├── memory/                    # 自动记忆目录
│       │   ├── MEMORY.md              # 记忆索引（≤200 行/25KB）
│       │   ├── user_role.md           # 用户记忆
│       │   ├── feedback_testing.md    # 反馈记忆
│       │   ├── project_goals.md       # 项目记忆
│       │   └── team/                  # 团队记忆（TEAMMEM Feature Flag）
│       │       └── shared_context.md
│       ├── session-abc.jsonl          # 会话转录（append-only JSONL）
│       ├── session-abc/
│       │   └── subagents/
│       │       ├── agent-1.jsonl      # 子代理转录
│       │       └── agent-1.meta.json  # 子代理元数据
│       └── session-def.jsonl          # 另一个会话

项目根目录/
├── CLAUDE.md                          # 项目级指令（提交到版本控制）
├── CLAUDE.local.md                    # 个人本地指令（gitignored）
└── .claude/
    ├── CLAUDE.md                      # 项目 .claude 目录指令
    └── rules/
        └── *.md                       # 条件规则（glob 模式匹配）
```

### 7.2 会话生命周期

```
┌──────────────────────────────────────────────────────────────────┐
│                       会话生命周期                                │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  启动               ┌──────────────┐                             │
│  ──────────────────>│ 初始化会话 ID  │                            │
│                     │ (UUID 生成)    │                            │
│                     └───────┬──────┘                             │
│                             │                                    │
│  等待首条消息         ┌─────▼───────┐                             │
│  ──────────────────>│  缓冲元数据   │   pendingEntries[] 暂存     │
│                     │ (延迟物化)    │                             │
│                     └───────┬──────┘                             │
│                             │ 首条 user/assistant 消息            │
│                      ┌──────▼──────┐                             │
│  物化文件            │materialize  │  创建 .jsonl 文件             │
│  ──────────────────>│SessionFile() │  刷写缓冲条目                │
│                     └───────┬──────┘                             │
│                             │                                    │
│  对话进行中    ┌─────────────▼─────────────┐                      │
│  ────────────>│   appendEntry() 持续追加    │                     │
│               │   100ms 批量刷写            │                     │
│               │   parentUuid 构建 DAG       │                     │
│               └─────────────┬─────────────┘                      │
│                             │                                    │
│  上下文压缩          ┌──────▼──────┐                              │
│  ──────────────────>│  compact     │  content-replacement 记录    │
│                     │  boundary    │  reAppendSessionMetadata     │
│                     └───────┬──────┘                              │
│                             │                                    │
│  分叉 (ctrl+z)       ┌─────▼───────┐                             │
│  ──────────────────>│ 新分支起点    │  旧分支保留但不加载            │
│                     │ parentUuid   │  零成本撤回                   │
│                     └───────┬──────┘                              │
│                             │                                    │
│  结束                ┌──────▼──────┐                              │
│  ──────────────────>│  flush()     │  等待所有挂起写入完成          │
│                     │  cleanup     │                              │
│                     └─────────────┘                               │
│                                                                  │
│  恢复 (/resume)      ┌─────────────┐                              │
│  ──────────────────>│ 发现 → 选择  │  渐进式加载                   │
│                     │ → 重建状态    │  文件缓存合并                  │
│                     └─────────────┘                               │
│                                                                  │
│  跨机器 (teleport)   ┌─────────────┐                              │
│  ──────────────────>│ Git Bundle   │  OAuth 认证                   │
│                     │ → API 传输    │  三级降级打包                  │
│                     │ → 环境重建    │  WIP stash 保留               │
│                     └─────────────┘                               │
└──────────────────────────────────────────────────────────────────┘
```

### 7.3 记忆注入数据流

```
┌─────────────────────────────────────────────────────────┐
│                    记忆注入数据流                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  系统提示词注入路径：                                     │
│  ┌────────────┐    ┌──────────────┐    ┌────────────┐  │
│  │ MEMORY.md  │───>│ truncate     │───>│ system     │  │
│  │ (≤200行)   │    │ EntryContent │    │ prompt     │  │
│  └────────────┘    └──────────────┘    └────────────┘  │
│                                                         │
│  用户上下文注入路径：                                     │
│  ┌────────────┐    ┌──────────────┐    ┌────────────┐  │
│  │ CLAUDE.md  │───>│ getClaudeMds │───>│ user       │  │
│  │ (层级发现)  │    │ ()           │    │ context    │  │
│  └────────────┘    └──────────────┘    └────────────┘  │
│                         │                               │
│  ┌────────────┐         │              ┌────────────┐  │
│  │ .claude/   │────────>│              │ attachment  │  │
│  │ rules/*.md │   ┌─────▼──────┐      │ (per turn)  │  │
│  └────────────┘   │ filterInjected│──>└────────────┘  │
│                   │ MemoryFiles() │                    │
│  ┌────────────┐   └─────────────┘                      │
│  │ 相关记忆   │                                         │
│  │ (Sonnet    │──> findRelevantMemories()               │
│  │  分类器)    │       ↓                                │
│  └────────────┘   附件注入到对话                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 设计哲学分析

### 无需修改的可扩展性（Extensibility Without Modification）

CLAUDE.md 记忆系统是"无需修改的可扩展性"最纯粹的体现。用户不需要修改任何代码，只需在项目根目录放置一个 `CLAUDE.md` 文件，就能永久改变 Claude Code 在该项目中的行为。这个系统从个人（`~/.claude/CLAUDE.md`）到项目（`CLAUDE.md`）到局部（`.claude/rules/*.md`）形成了完整的层级，每一层都是通过"添加文件"而非"修改代码"来扩展系统能力的。

自动记忆系统更进一步——用户甚至不需要手动创建文件。Claude 在对话过程中自动识别值得保存的信息（用户偏好、项目上下文、反馈意见），将其写入独立的 Markdown 文件，并更新 `MEMORY.md` 索引。下次对话时，这些记忆自动注入上下文。整个"学习→记忆→回忆"的闭环无需任何代码改动。

### 优雅降级（Graceful Degradation）

持久化系统在多个层面展现优雅降级的设计。会话存储使用 JSONL 格式——这种 append-only 的日志格式天然具备崩溃安全性。即使系统在写入过程中崩溃，最多丢失最后一个不完整的 JSON 行，之前的所有数据完整保留。`/resume` 命令利用这一特性实现会话恢复，让用户从中断的地方继续。

Teleport 的 Git Bundle 三级降级策略更是教科书式的优雅降级：首先尝试打包全部历史（`--all`），如果超过 100MB 则降级为仅当前分支（`HEAD`），如果仍然过大则最终降级为无历史的快照（`squashed-root`）。每一级降级都丢失一些信息（分支历史、完整历史），但核心功能（传输代码和工作目录状态）始终保证可用。

### 上下文窗口经济学（Context Window Economics）

在持久化系统中，上下文窗口经济学体现在对"什么被注入 AI 上下文"的精细控制上。`MEMORY.md` 被严格限制在 200 行 / 25KB——这不是技术限制，而是经济决策：索引文件占用的每个 token 都是从对话上下文中"花费"的。类似地，附件系统的 `MAX_MEMORY_BYTES = 4096` 和 `MAX_SESSION_BYTES = 60KB` 确保记忆注入不会挤占真正的对话空间。

LRU 文件状态缓存（100 条目 / 25MB）将上下文窗口经济学应用到本地状态管理：它只保留最近使用的文件状态，因为 AI 的"注意力"（即上下文窗口）也遵循时间局部性原则——最近访问的文件最可能再次被引用。

### 安全优先设计（Safety-First Design）

持久化系统在安全方面非常谨慎。会话文件使用 `0o600` 权限（仅当前用户可读写），防止其他用户或进程访问对话内容。`CLAUDE.local.md` 被设计为始终 gitignored——它存储的是用户私有的本地指令（可能包含敏感配置），决不能被意外提交到版本控制。

记忆系统的路径验证（`validateMemoryPath()` 和 `validateTeamMemWritePath()`）包含符号链接解析检查，防止通过符号链接绕过目录边界进行路径穿越攻击。Teleport 要求 OAuth 认证（不支持 API Key），并通过组织 UUID 实现会话隔离，确保不同组织的数据不会混淆。

### 可组合性（Composability）

持久化系统的层级设计体现了可组合性原则。CLAUDE.md 层级（系统 → 用户 → 项目 → 规则 → 本地）、记忆类型分类（user / feedback / project / reference）、以及配置优先级（CLI 参数 > 环境变量 > 项目配置 > 用户配置 > 默认值）都遵循相同的可组合模式：多个来源的信息通过明确的优先级规则合并为最终结果。

Teleport 将可组合性延伸到机器边界之外：本地的 Git 仓库状态、工作目录、系统提示词、模型配置被打包为一个 `SessionContext` 对象，通过 API 传输到远程环境重新组装。每个组件（代码、配置、上下文）独立传输、独立恢复，组合后重建完整的工作环境。

### 防御性编程（Defensive Programming）

`buildConversationChain()` 中的环检测（`seen` Set）是防御性编程的典型案例——理论上 `parentUuid` 链不应该出现循环，但生产环境中的并发写入可能导致不可预测的状态。检测到循环时，系统记录错误并返回部分结果，而非崩溃。

`recoverOrphanedParallelToolResults()` 函数处理了另一个防御性场景：当 AI 发起并行工具调用时，流式传输产生的多个 assistant 消息形成 DAG 而非简单链表，单链遍历会丢失兄弟节点。这个后处理步骤恢复被遗漏的并行结果，确保对话的完整性。

JSONL 格式的选择本身就是防御性设计——每行独立，不需要完整的文件结构（不像 JSON 需要配对的括号），部分写入不会破坏已有数据。字节级预扫描（`walkChainBeforeParse`）中对 UUID 固定长度（36 字符）的硬编码看似脆弱，实际上利用了 UUID v4 格式的不变性保证，是"在已知约束下优化"的防御性编程实践。

---

## 关键要点总结

1. **JSONL Append-Only 是核心存储格式**：选择这种格式不仅是为了简单，更是为了崩溃安全性——写入中断最多丢失一行，永远不会损坏已有数据。

2. **延迟物化减少无效文件**：会话文件只在收到第一条实际消息后才创建，避免了大量只有元数据的空会话文件。

3. **DAG 消息结构支持零成本撤回**：`parentUuid` 链构成有向无环图，分叉操作只需新建指向更早父节点的消息，旧分支在文件中但不被加载。

4. **记忆系统实现跨会话知识积累**：四种记忆类型（user/feedback/project/reference）覆盖了 AI 助手需要记住的所有信息类别。

5. **层级化的 CLAUDE.md 系统**：从用户全局到项目特定到文件级别的条件规则，每一层都通过"添加文件"来扩展。

6. **LRU 双限制缓存**：同时控制条目数量（100）和内存占用（25MB），防止大文件导致内存溢出。

7. **Teleport 三级降级**：`--all` → `HEAD` → `squashed-root`，确保任意大小的仓库都能跨机器迁移。

8. **安全边界无处不在**：`0o600` 文件权限、OAuth 认证、组织隔离、符号链接验证、`50MB` 读取限制。

---

## 下一篇预览

**Doc 13：服务层** 将深入 Claude Code 的服务架构——API 客户端如何与 Anthropic 后端通信、OAuth 如何管理认证令牌、GrowthBook 如何在运行时控制特性开关、插件和技能系统如何动态扩展功能、以及上下文压缩服务如何在会话变长时智能管理 token 预算。服务层是连接本地持久化和外部世界的桥梁，理解它是理解 Claude Code 如何"活在云端"的关键。
