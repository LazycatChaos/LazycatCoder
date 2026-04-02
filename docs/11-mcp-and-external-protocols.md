# Doc 11：MCP 与外部协议

> **前置阅读：** Doc 0 ~ Doc 10
> **核心问题：** Claude Code 如何通过标准化协议连接外部世界——无限扩展工具、集成 IDE、对接代码理解服务？
> **设计哲学重点：** 无需修改的可扩展性、可组合性、防御性编程、安全优先设计、优雅降级

---

## 第一章：MCP 协议介绍

### 1.1 什么是 Model Context Protocol

Model Context Protocol（MCP）是一个开放协议，定义了 AI 应用与外部工具/数据源之间的标准通信方式。在 Claude Code 的架构中，MCP 扮演着"万能适配器"的角色——通过统一的协议接口，Claude Code 可以连接任意数量的外部服务，而无需修改一行核心代码。

MCP 的核心设计理念可以用一个类比来理解：如果说 Doc 6 中介绍的 `Tool` 接口是 Claude Code 的"内置插座"，那么 MCP 就是一个"万能转接头"。任何遵循 MCP 协议的服务器都可以向 Claude Code 暴露工具（Tools）、资源（Resources）和提示词（Prompts），而 Claude Code 只需要知道如何"说 MCP 协议"。

### 1.2 MCP 的三大能力

MCP 协议定义了三种核心能力，Claude Code 全部支持：

1. **工具（Tools）**——最核心的能力。MCP 服务器向客户端暴露可调用的函数，每个函数有名称、描述和 JSON Schema 定义的输入参数。Claude 模型可以像使用内置工具一样使用 MCP 工具——它看到工具描述后决定是否调用，传入参数，获取结果。例如，一个 GitHub MCP 服务器可以暴露 `create_issue`、`list_pull_requests` 等工具。

2. **资源（Resources）**——MCP 服务器可以暴露结构化数据资源，Claude Code 通过 `resources/list` 列出可用资源，通过 `ReadMcpResourceTool` 读取具体资源内容。资源可以是文件、数据库记录、API 响应等任何结构化数据。

3. **提示词（Prompts）**——MCP 服务器可以提供预定义的提示词模板，Claude Code 通过 `prompts/list` 获取可用模板，将它们作为斜杠命令（slash commands）暴露给用户。

### 1.3 MCP 在 Claude Code 中的角色

在 Claude Code 的六层架构中，MCP 横跨了多个层次：

```
┌─────────────────────────────────────────────────┐
│               终端 UI 层                         │
│  MCPConnectionManager.tsx  /mcp 命令 UI          │
├─────────────────────────────────────────────────┤
│               工具注册层                         │
│  MCPTool ← fetchToolsForClient() 动态注册        │
├─────────────────────────────────────────────────┤
│               服务层                             │
│  src/services/mcp/client.ts   连接管理           │
│  src/services/mcp/config.ts   配置解析           │
├─────────────────────────────────────────────────┤
│               传输层                             │
│  Stdio | SSE | HTTP | WebSocket | In-Process     │
├─────────────────────────────────────────────────┤
│               外部 MCP 服务器                    │
│  GitHub | Slack | 数据库 | 自定义服务...          │
└─────────────────────────────────────────────────┘
```

### 1.4 MCP 支持的传输类型

Claude Code 支持 **八种** MCP 传输协议，这是所有 MCP 客户端中最全面的支持之一：

| 传输类型 | Zod 枚举值 | 适用场景 | 协议特点 |
|---------|-----------|---------|---------|
| **Stdio** | `stdio` | 本地进程 | 通过子进程的 stdin/stdout 通信，最常见 |
| **SSE** | `sse` | 远程 HTTP 服务 | Server-Sent Events，长连接单向推送 |
| **SSE-IDE** | `sse-ide` | IDE 扩展内部 | SSE 变体，无需认证，IDE 专用 |
| **HTTP** | `http` | 远程 HTTP 服务 | MCP Streamable HTTP，双向请求 |
| **WebSocket** | `ws` | 远程双向通信 | 全双工实时通信 |
| **WS-IDE** | `ws-ide` | IDE 扩展内部 | WebSocket 变体，IDE 专用 |
| **SDK** | `sdk` | 进程内嵌入 | Agent SDK 控制通道 |
| **Claude.ai Proxy** | `claudeai-proxy` | Claude.ai 组织连接器 | 通过 Anthropic 代理的 HTTP 传输 |

这些传输类型在 `src/services/mcp/types.ts` 中通过 Zod 联合类型定义：

```typescript
// 文件：src/services/mcp/types.ts，第 23-26 行
export const TransportSchema = lazySchema(() =>
  z.enum(['stdio', 'sse', 'sse-ide', 'http', 'ws', 'sdk']),
)
// 注：ws-ide 和 claudeai-proxy 通过独立的 Schema 定义
```

服务器配置是所有传输类型的联合：

```typescript
// 文件：src/services/mcp/types.ts，第 124-135 行
export const McpServerConfigSchema = lazySchema(() =>
  z.union([
    McpStdioServerConfigSchema(),    // 本地命令行进程
    McpSSEServerConfigSchema(),      // 远程 SSE 服务
    McpSSEIDEServerConfigSchema(),   // IDE 内部 SSE
    McpWebSocketIDEServerConfigSchema(), // IDE 内部 WebSocket
    McpHTTPServerConfigSchema(),     // MCP Streamable HTTP
    McpWebSocketServerConfigSchema(), // 通用 WebSocket
    McpSdkServerConfigSchema(),      // Agent SDK 嵌入
    McpClaudeAIProxyServerConfigSchema(), // Claude.ai 代理
  ]),
)
```

### 1.5 服务器连接状态机

每个 MCP 服务器在 Claude Code 中都有一个明确的连接状态：

```typescript
// 文件：src/services/mcp/types.ts，第 221-226 行
export type MCPServerConnection =
  | ConnectedMCPServer    // type: 'connected' — 已连接，可用
  | FailedMCPServer       // type: 'failed' — 连接失败
  | NeedsAuthMCPServer    // type: 'needs-auth' — 需要认证
  | PendingMCPServer      // type: 'pending' — 正在连接中
  | DisabledMCPServer     // type: 'disabled' — 用户禁用
```

这五个状态形成了一个清晰的生命周期：

```
                    ┌──────────┐
                    │ disabled │←── 用户 /mcp disable
                    └──────────┘
                         ↑
  ┌─────────┐      ┌─────────┐      ┌───────────┐
  │ pending │─────→│connected│─────→│  failed   │
  └─────────┘      └─────────┘      └───────────┘
       │                                   │
       │           ┌───────────┐           │
       └──────────→│needs-auth │←──────────┘
                   └───────────┘
                        │
                   用户 /mcp 认证
                        │
                   ┌─────────┐
                   │ pending │ (重连)
                   └─────────┘
```

---

## 第二章：MCP 客户端 src/services/mcp/

MCP 客户端是 Claude Code 与外部 MCP 服务器通信的核心模块。整个目录包含 25 个文件，总计约 340KB 代码，是 Claude Code 中体量最大的服务模块之一。

### 2.1 核心文件概览

| 文件 | 大小 | 职责 |
|-----|------|-----|
| `client.ts` | 119KB | 连接管理、工具获取、结果处理、MCP 调用 |
| `config.ts` | 51KB | 多层级配置解析（local/user/project/enterprise/claudeai） |
| `types.ts` | ~10KB | 所有 MCP 类型定义（传输、配置、连接状态） |
| `auth.ts` | 89KB | OAuth 认证、令牌管理、步进认证（step-up） |
| `elicitationHandler.ts` | 10KB | MCP 引出（elicitation）处理——交互式 OAuth 重认证 |
| `normalization.ts` | ~1KB | 服务器名称标准化 |
| `mcpStringUtils.ts` | ~4KB | MCP 工具名称解析/构建 |
| `officialRegistry.ts` | ~2KB | Anthropic 官方 MCP 注册表预取 |
| `claudeai.ts` | ~6KB | Claude.ai 组织 MCP 服务器获取 |
| `InProcessTransport.ts` | ~2KB | 进程内链接传输对 |
| `MCPConnectionManager.tsx` | ~8KB | React 连接管理上下文 |
| `headersHelper.ts` | ~5KB | 动态 HTTP 头生成 |

### 2.2 连接建立流程

`connectToServer()` 是整个连接系统的核心，它是一个 **memoized** 异步函数——同一个服务器配置只会建立一次连接：

```typescript
// 文件：src/services/mcp/client.ts，第 595-607 行
export const connectToServer = memoize(
  async (
    name: string,                    // 服务器名称
    serverRef: ScopedMcpServerConfig, // 带作用域的服务器配置
    serverStats?: {                   // 连接统计（用于日志）
      totalServers: number
      stdioCount: number
      sseCount: number
      httpCount: number
      sseIdeCount: number
      wsIdeCount: number
    },
  ): Promise<MCPServerConnection> => {
    // ... 根据 serverRef.type 选择传输层
```

连接建立后，客户端声明两个关键能力——**roots**（工作目录）和 **elicitation**（交互式认证）：

```typescript
// 文件：src/services/mcp/client.ts，第 985-1001 行
const client = new Client(
  {
    name: 'claude-code',          // 客户端标识
    title: 'Claude Code',
    version: MACRO.VERSION ?? 'unknown',
  },
  {
    capabilities: {
      roots: {},        // 声明支持 ListRoots 请求
      elicitation: {},  // 声明支持交互式认证引出
    },
  },
)
```

### 2.3 批量连接策略

`getMcpToolsCommandsAndResources()` 负责批量连接所有配置的 MCP 服务器，它使用了**差异化并发策略**——本地服务器（Stdio/SDK）和远程服务器分开处理：

```typescript
// 文件：src/services/mcp/client.ts，第 2264-2271 行
// 本地服务器需要较低并发（受进程 spawn 限制）
const localServers = configEntries.filter(([_, config]) =>
  isLocalMcpServer(config),                   // stdio 或 sdk 类型
)
// 远程服务器可以更高并发连接
const remoteServers = configEntries.filter(
  ([_, config]) => !isLocalMcpServer(config), // sse/http/ws 等
)
```

默认本地并发数为 3（`getMcpServerConnectionBatchSize()`），远程并发数为 20（`getRemoteMcpServerConnectionBatchSize()`）。这种差异化处理体现了对系统资源的精确把控——本地进程创建是重量级操作，而远程 HTTP 连接则轻量得多。

### 2.4 工具名称标准化

MCP 工具名称遵循严格的命名规范，通过 `buildMcpToolName()` 构建完全限定名：

```typescript
// 文件：src/services/mcp/mcpStringUtils.ts，第 50-52 行
export function buildMcpToolName(
  serverName: string, toolName: string
): string {
  return `${getMcpPrefix(serverName)}${normalizeNameForMCP(toolName)}`
  // 结果格式：mcp__serverName__toolName
}
```

名称标准化函数确保所有字符都符合 API 要求的 `^[a-zA-Z0-9_-]{1,64}$` 模式：

```typescript
// 文件：src/services/mcp/normalization.ts，第 17-23 行
export function normalizeNameForMCP(name: string): string {
  let normalized = name.replace(/[^a-zA-Z0-9_-]/g, '_') // 非法字符替换
  if (name.startsWith(CLAUDEAI_SERVER_PREFIX)) {
    // Claude.ai 服务器额外清理连续下划线，防止干扰 __ 分隔符
    normalized = normalized.replace(/_+/g, '_').replace(/^_|_$/g, '')
  }
  return normalized
}
```

这种 `mcp__` 前缀设计防止了 MCP 工具与内置工具的命名冲突——例如，一个 MCP 服务器暴露的 `Write` 工具会变成 `mcp__myserver__Write`，不会与内置的 `Write` 工具冲突。

### 2.5 多层级配置系统

MCP 配置从多个来源聚合，由 `getAllMcpConfigs()` 统一管理：

```typescript
// 文件：src/services/mcp/config.ts，第 1258 行
export async function getAllMcpConfigs(): Promise<{
  servers: Record<string, ScopedMcpServerConfig>
  // ...
}>
```

配置优先级（从高到低）：

| 优先级 | 作用域 | 来源 | 说明 |
|-------|--------|------|------|
| 1 | `dynamic` | SDK/IDE 动态注入 | 运行时通过 API 传入 |
| 2 | `enterprise` | MDM 企业管理 | `managed-mcp.json` |
| 3 | `local` | 项目级配置 | `.claude/settings.local.json` |
| 4 | `project` | 项目共享配置 | `.claude/settings.json` |
| 5 | `user` | 用户全局配置 | `~/.claude/settings.json` |
| 6 | `claudeai` | Claude.ai 组织 | 通过 API 获取 |
| 7 | `managed` | 插件提供 | 插件注册的 MCP 服务器 |

### 2.6 官方注册表与 Claude.ai 集成

**官方注册表**（`officialRegistry.ts`）在启动时预取 Anthropic 维护的 MCP 服务器 URL 列表。这个列表用于判断一个 MCP 服务器是否是"官方"的——影响权限决策和 UI 展示：

```typescript
// 文件：src/services/mcp/officialRegistry.ts，第 33-59 行
export async function prefetchOfficialMcpUrls(): Promise<void> {
  // 从 api.anthropic.com/mcp-registry/v0/servers 获取官方列表
  const response = await axios.get<RegistryResponse>(
    'https://api.anthropic.com/mcp-registry/v0/servers?...',
    { timeout: 5000 },                    // 5 秒超时，不阻塞启动
  )
  // 标准化 URL 后存入 Set，用于 O(1) 查找
  officialUrls = urls
}

// fail-closed：注册表未加载时返回 false
export function isOfficialMcpUrl(normalizedUrl: string): boolean {
  return officialUrls?.has(normalizedUrl) ?? false  // 未知 = 非官方
}
```

**Claude.ai 集成**（`claudeai.ts`）从用户的 Claude.ai 组织获取 MCP 服务器配置。这些服务器通过 Claude.ai 的代理 URL 连接，使用 OAuth 令牌认证：

```typescript
// 文件：src/services/mcp/claudeai.ts，第 39-133 行
export const fetchClaudeAIMcpConfigsIfEligible = memoize(
  async (): Promise<Record<string, ScopedMcpServerConfig>> => {
    // 检查 OAuth 令牌和 user:mcp_servers 权限
    const tokens = getClaudeAIOAuthTokens()
    if (!tokens?.scopes?.includes('user:mcp_servers')) {
      return {}  // 无权限，静默返回空
    }
    // 通过 API 获取组织配置的 MCP 服务器列表
    const response = await axios.get<ClaudeAIMcpServersResponse>(url, {
      headers: {
        Authorization: `Bearer ${tokens.accessToken}`,
        'anthropic-beta': MCP_SERVERS_BETA_HEADER,
      },
      timeout: FETCH_TIMEOUT_MS,  // 5 秒
    })
    // 处理名称冲突——自动添加 (2), (3) 后缀
    // ...
  },
)
```

### 2.7 认证与安全

MCP 系统有一个复杂但完善的认证层，支持多种认证方式：

**OAuth 认证**：对于远程 MCP 服务器（SSE/HTTP），`auth.ts`（89KB）实现了完整的 OAuth 2.0 流程。`ClaudeAuthProvider` 类负责令牌管理、刷新和步进认证（step-up detection）。当服务器返回 403 并要求更高级别的认证时，`wrapFetchWithStepUpDetection()` 能够检测到并触发认证升级。

**Claude.ai 代理认证**：`createClaudeAiProxyFetch()` 实现了一个智能的 401 重试机制——在首次 401 后尝试刷新令牌，但只有在令牌确实发生了变化时才进行重试，避免无意义的双倍网络往返：

```typescript
// 文件：src/services/mcp/client.ts，第 372-422 行
export function createClaudeAiProxyFetch(innerFetch: FetchLike): FetchLike {
  return async (url, init) => {
    const { response, sentToken } = await doRequest()
    if (response.status !== 401) return response
    // 只有令牌确实变了才重试——避免对真正需要认证的服务器双倍 RTT
    const tokenChanged = await handleOAuth401Error(sentToken).catch(() => false)
    if (!tokenChanged) {
      // 检查是否有其他并发连接器已经刷新了令牌
      const now = getClaudeAIOAuthTokens()?.accessToken
      if (!now || now === sentToken) return response  // 没变，放弃重试
    }
    return (await doRequest()).response  // 用新令牌重试
  }
}
```

**认证缓存**：��避免重复探测已知需要认证的服务器，系统维护了��个 15 分钟 TTL 的认证缓存（`mcp-needs-auth-cache.json`）。写操作通过 Promise 链串行化，防止并发 read-modify-write 竞争。

**错误分类**：MCP 系统定义了三种专用错误类型，每种都有明确的语义和处理路径：
- `McpAuthError`——认证���败，触发 `needs-auth` 状态转换
- `McpSessionExpiredError`——会话过期（HTTP 404 + JSON-RPC -32001），触发自动重连
- `McpToolCallError`——工具调用返回 `isError: true`，携带 `_meta` 元数据供 SDK 消费

### 2.8 进程内传输

对于某些特殊的 MCP 服务器（如 Chrome 集成、Computer Use），Claude Code 选择**在进程内运行**而非 spawn 子进程，以避免约 325MB 的额外内存开销：

```typescript
// 文件：src/services/mcp/InProcessTransport.ts，第 57-63 行
export function createLinkedTransportPair(): [Transport, Transport] {
  const a = new InProcessTransport() // 客户端传输
  const b = new InProcessTransport() // 服务器传输
  a._setPeer(b)                      // 双向链接
  b._setPeer(a)
  return [a, b]
}
```

消息通过 `queueMicrotask()` 异步投递到对端的 `onmessage` 回调，避免同步请求/响应循环导致的栈溢出。

使用场景示例：当用户安装了 Chrome 集成 MCP 服务器时，Claude Code 检测到 `isClaudeInChromeMCPServer(name)` 为 true，就会创建一个链接传输对，在进程内启动 Chrome MCP 服务器，而不是 spawn 一个约 325MB 的 Node.js 子进程：

```typescript
// 文件：src/services/mcp/client.ts，第 910-924 行
// 在进程内运行 Chrome MCP 服务器以避免 spawn 约 325MB 子进程
const { createChromeContext } = await import(
  '../../utils/claudeInChrome/mcpServer.js'
)
const { createClaudeForChromeMcpServer } = await import(
  '@ant/claude-for-chrome-mcp'
)
const { createLinkedTransportPair } = await import(
  './InProcessTransport.js'
)
const context = createChromeContext(serverRef.env)
inProcessServer = createClaudeForChromeMcpServer(context)
const [clientTransport, serverTransport] = createLinkedTransportPair()
await inProcessServer.connect(serverTransport)
transport = clientTransport
```

---

## 第三章：MCPTool src/tools/MCPTool/

MCPTool 是 MCP 工具在 Claude Code 工具系统中的"代理壳"。每个来自 MCP 服务器的工具都被包装成一个 MCPTool 实例。

### 3.1 工具定义

MCPTool 的基础定义极为精简——因为大部分属性会在 `fetchToolsForClient()` 中被覆盖：

```typescript
// 文件：src/tools/MCPTool/MCPTool.ts，第 27-77 行
export const MCPTool = buildTool({
  isMcp: true,            // 标记为 MCP 工具
  name: 'mcp',            // 默认名称，实际会被覆盖
  maxResultSizeChars: 100_000,  // 结果最大 100K 字符

  get inputSchema(): InputSchema {
    return inputSchema()  // z.object({}).passthrough() — 接受任意输入
  },

  async checkPermissions(): Promise<PermissionResult> {
    return {
      behavior: 'passthrough',  // 交由权限系统统一处理
      message: 'MCPTool requires permission.',
    }
  },
  // ... renderToolUseMessage, renderToolResultMessage 等 UI 方法
} satisfies ToolDef<InputSchema, Output>)
```

### 3.2 动态工具注册

真正的魔法发生在 `fetchToolsForClient()` 中，它将每个 MCP 服务器返回的工具转换为 Claude Code 的 `Tool` 接口：

```typescript
// 文件：src/services/mcp/client.ts，第 1743-1830 行
export const fetchToolsForClient = memoizeWithLRU(
  async (client: MCPServerConnection): Promise<Tool[]> => {
    // 向 MCP 服务器发送 tools/list 请求
    const result = await client.client.request(
      { method: 'tools/list' },
      ListToolsResultSchema,
    )

    // 将每个 MCP 工具转换为 Claude Code Tool 格式
    return toolsToProcess.map((tool): Tool => {
      const fullyQualifiedName = buildMcpToolName(client.name, tool.name)
      return {
        ...MCPTool,                           // 继承基础 MCPTool
        name: fullyQualifiedName,              // mcp__server__tool
        mcpInfo: { serverName: client.name, toolName: tool.name },
        isMcp: true,
        // 利用 MCP 工具注解（annotations）提供语义信息
        isConcurrencySafe() {
          return tool.annotations?.readOnlyHint ?? false  // 只读 = 可并发
        },
        isDestructive() {
          return tool.annotations?.destructiveHint ?? false
        },
        isOpenWorld() {
          return tool.annotations?.openWorldHint ?? false
        },
        inputJSONSchema: tool.inputSchema,     // 使用 MCP 工具的原始 schema
        async checkPermissions() {
          return {
            behavior: 'passthrough' as const,
            // 权限建议：允许此 MCP 服务器的工具
            suggestions: [{
              type: 'addRules' as const,
              rules: [{ /* 该服务器全部工具的允许规则 */ }],
            }],
          }
        },
      }
    })
  },
)
```

### 3.3 引出处理器（Elicitation Handler）

MCP 协议支持"引出"（elicitation）——服务器在工具调用过程中可以要求客户端进行交互式操作（如 OAuth 认证、用户确认）。`elicitationHandler.ts` 处理这些请求：

```typescript
// 文件：src/services/mcp/elicitationHandler.ts，第 68-80 行
export function registerElicitationHandler(
  client: Client,
  serverName: string,
  setAppState: (f: (prevState: AppState) => AppState) => void,
): void {
  // 注册 MCP 引出请求处理器
  client.setRequestHandler(ElicitRequestSchema, async (request, extra) => {
    // 将引出请求推入 AppState 队列
    // UI 层（ElicitationModal）从队列中读取并显示给用户
    // 用户操作后调用 respond() 将结果返回给 MCP 服务器
  })
}
```

引出请求支持两种模式：
- **表单模式**（`form`）：向用户展示输入表单
- **URL 模式**（`url`）：打开浏览器进行 OAuth 重认证

### 3.4 结果处理与持久化

MCP 工具的结果处理涉及多个步骤。`transformMCPResult()`（`client.ts:2662`）将 MCP 服务器返回的原始结果转换为 Claude Code 的标准格式，支持三种结果类型：

- **toolResult**：标准工具结果（文本 + 图片 + 资源链接）
- **structuredContent**：MCP 结构化内容（带 schema 的 JSON 数据）
- **contentArray**：内容数组（多个内容块组合）

对于大型结果，`processMCPResult()`（`client.ts:2720`）会将内容持久化到磁盘，在消息中注入引用标签——这与 Doc 7 中介绍的工具结果存储机制一致，是"上下文窗口经济学"在 MCP 层面的应用。

图片类型的结果会经过 `maybeResizeAndDownsampleImageBuffer()` 处理，确保不会因为一张超大图片耗尽上下文窗口的 token 预算。二进制内容（PDF、音频等）则通过 `persistBinaryContent()` 保存到本地文件，并向模型返回文件路径描述。

### 3.5 工具描述长度限制

外部 MCP 服务器（特别是 OpenAPI 自动生成的）经常产生超长的工具描述（15-60KB），这会浪费宝贵的上下文窗口空间。Claude Code 对此做了严格限制：

```typescript
// 文件：src/services/mcp/client.ts，第 218 行
const MAX_MCP_DESCRIPTION_LENGTH = 2048  // 最大 2KB
```

这个限制同时应用于工具描述和服务器指令（instructions）。超过限制的内容会被截断并添加 `… [truncated]` 后缀，同时写入调试日志。这种设计确保了即使连接了行为不良的 MCP 服务器，也不会对系统的整体性能造成严重影响。

---

## 第四章：LSP 集成 src/services/lsp/

Language Server Protocol（LSP）是微软开发的标准协议，用于编辑器与语言服务器之间的通信。Claude Code 通过 LSP 获得代码理解能力——跳转到定义、查找引用、获取类型信息等。

### 4.1 架构概览

LSP 集成由以下核心文件组成：

| 文件 | 大小 | 职责 |
|-----|------|------|
| `manager.ts` | 10KB | 全局单例管理器，管理初始化状态 |
| `LSPServerManager.ts` | 13KB | 多服务器管理，根据文件扩展名路由请求 |
| `LSPClient.ts` | 14KB | LSP 客户端封装，通过 vscode-jsonrpc 通信 |
| `LSPServerInstance.ts` | 17KB | 单个 LSP 服务器实例的生命周期管理 |
| `LSPDiagnosticRegistry.ts` | 12KB | 诊断信息注册与管理 |
| `config.ts` | 3KB | LSP 服务器配置加载 |
| `passiveFeedback.ts` | 11KB | 被动反馈——将诊断信息传递给 Claude |

### 4.2 LSPClient 封装

`createLSPClient()` 创建了一个通过 stdio 与 LSP 服务器进程通信的客户端：

```typescript
// 文件：src/services/lsp/LSPClient.ts，第 51-54 行
export function createLSPClient(
  serverName: string,
  onCrash?: (error: Error) => void, // 崩溃回调，允许外层重启
): LSPClient {
  // 状态变量在闭包中管理
  let process: ChildProcess | undefined
  let connection: MessageConnection | undefined
  let capabilities: ServerCapabilities | undefined
  let isInitialized = false
  // ...
```

LSP 客户端使用 `vscode-jsonrpc` 库实现 JSON-RPC 通信——与 MCP 使用独立的 MCP SDK 不同，LSP 复用了 VS Code 生态的标准库。

### 4.3 LSPTool 工具

LSPTool 将 LSP 能力暴露给 Claude，支持九种操作：

```typescript
// 文件：src/tools/LSPTool/LSPTool.ts，第 61-73 行
operation: z.enum([
  'goToDefinition',        // 跳转到定义
  'findReferences',        // 查找所有引用
  'hover',                 // 获取类型/文档悬停信息
  'documentSymbol',        // 获取文件中所有符号
  'workspaceSymbol',       // 搜索工作区符号
  'goToImplementation',    // 跳转到实现
  'prepareCallHierarchy',  // 准备调用层次
  'incomingCalls',         // 获取入向调用
  'outgoingCalls',         // 获取出向调用
])
```

### 4.4 服务器管理器

`LSPServerManager` 基于文件扩展名将请求路由到正确的 LSP 服务器：

```typescript
// 文件：src/services/lsp/LSPServerManager.ts，第 16-43 行
export type LSPServerManager = {
  initialize(): Promise<void>       // 加载所有配置的 LSP 服务器
  shutdown(): Promise<void>          // 关闭所有服务器
  getServerForFile(filePath: string): LSPServerInstance | undefined
  ensureServerStarted(filePath: string): Promise<LSPServerInstance | undefined>
  sendRequest<T>(filePath, method, params): Promise<T | undefined>
  openFile(filePath, content): Promise<void>   // didOpen 通知
  changeFile(filePath, content): Promise<void> // didChange 通知
  saveFile(filePath): Promise<void>            // didSave 通知
  closeFile(filePath): Promise<void>           // didClose 通知
  isFileOpen(filePath): boolean
}
```

这里有一个关键设计——LSP 管理器使用**懒初始化**：只有当需要对某种语言的文件进行操作时，才会启动对应的 LSP 服务器。这避免了启动时预加载所有语言服务器的开销。

### 4.5 全局单例与初始化状态机

LSP 管理器作为全局单例存在，通过 `manager.ts` 中的四状态机管理初始化过程：

```typescript
// 文件：src/services/lsp/manager.ts，第 14 行
type InitializationState = 'not-started' | 'pending' | 'success' | 'failed'
```

`getLspServerManager()` 在初始化失败时返回 `undefined` 而非抛出错误，让调用者能够优雅降级——如果 LSP 不可用，Claude 仍然可以通过其他方式（grep、read）理解代码。`initializationGeneration` 计数器防止过期的初始化 Promise 更新状态，确保在快速重初始化场景下的数据一致性。

### 4.6 被动反馈机制

`passiveFeedback.ts`（11KB）实现了一个创新的功能——将 LSP 诊断信息（类型错误、未使用变量等）作为"被动反馈"传递给 Claude。当 Claude 编辑文件后，LSP 服务器会推送诊断更新，`passiveFeedback.ts` 将这些信息注入到对话上下文中，让 Claude 能够发现并修复自己引入的错误。这是一个典型的"工具即反馈"模式——LSP 不仅是 Claude 主动查询的工具，更是持续监控代码质量的哨兵。

### 4.7 MCP 与 LSP 的对比

| 维度 | MCP | LSP |
|------|-----|-----|
| 设计目的 | AI 应用与外部工具通信 | 编辑器与语言服务通信 |
| 通信模型 | 请求/响应 + 通知 | 请求/响应 + 通知 |
| 传输方式 | 多种（stdio/SSE/HTTP/WS） | stdio（通过子进程） |
| 能力范围 | 任意工具/资源/提示词 | 代码分析（定义/引用/诊断等） |
| Claude Code 中的角色 | 扩展工具能力 | 增强代码理解 |
| SDK 库 | @modelcontextprotocol/sdk | vscode-jsonrpc |

---

## 第五章：IDE 桥接 src/bridge/

IDE 桥接（Bridge）系统使 Claude Code 能够嵌入到 VS Code、JetBrains 等 IDE 中运行，也支持通过 Claude.ai 网页远程控制（Remote Control）。整个 `src/bridge/` 目录包含 33 个文件，总计约 450KB 代码。

### 5.1 核心文件概览

| 文件 | 大小 | 职责 |
|-----|------|------|
| `bridgeMain.ts` | 116KB | Remote Control 主入口，环境注册/轮询/会话管理 |
| `replBridge.ts` | 100KB | REPL 模式桥接——将 IDE 消息桥接到 REPL |
| `bridgeMessaging.ts` | 16KB | 消息协议——类型守卫、消息过滤、控制请求处理 |
| `bridgeApi.ts` | 18KB | API 客户端——与 Claude Cloud Relay (CCR) 通信 |
| `jwtUtils.ts` | 9KB | JWT 令牌解码与主动刷新调度 |
| `types.ts` | 10KB | 类型定义——WorkSecret、SpawnMode 等 |
| `bridgeEnabled.ts` | 8KB | 特性门控——检查 Remote Control 是否可用 |
| `initReplBridge.ts` | 24KB | REPL 桥接初始化（双协议版本支持） |
| `remoteBridgeCore.ts` | 39KB | 远程桥接核心逻辑 |

### 5.2 WebSocket 通信

IDE 桥接使用 WebSocket 进行双向实时通信。消息遵循 SDK 消息协议（`SDKMessage`），这是一个基于 `type` 字段的可辨识联合类型：

```typescript
// 文件：src/bridge/bridgeMessaging.ts，第 36-43 行
export function isSDKMessage(value: unknown): value is SDKMessage {
  return (
    value !== null &&
    typeof value === 'object' &&
    'type' in value &&              // 必须有 type 字段
    typeof value.type === 'string'  // type 必须是字符串
  )
}
```

桥接层对消息进行了严格过滤——只有"合格"的消息才会被转发给 IDE：

```typescript
// 文件：src/bridge/bridgeMessaging.ts，第 77-80 行
export function isEligibleBridgeMessage(m: Message): boolean {
  // 虚拟消息（REPL 内部调用）仅用于显示
  // Bridge/SDK 消费者通过 REPL tool_use/result 获取摘要
  if ((m.type === 'user' || m.type === 'assistant') && m.isVirtual) {
    // ... 过滤虚拟消息
  }
```

### 5.3 JWT 认证

桥接系统使用 JWT（JSON Web Token）进行会话认证。`jwtUtils.ts` 实现了一个**主动令牌刷新调度器**——在令牌过期前 5 分钟自动刷新，而不是等到过期后才处理：

```typescript
// 文件：src/bridge/jwtUtils.ts，第 21-32 行
export function decodeJwtPayload(token: string): unknown | null {
  // 去除 sk-ant-si- 前缀（session ingress 专用前缀）
  const jwt = token.startsWith('sk-ant-si-')
    ? token.slice('sk-ant-si-'.length)
    : token
  const parts = jwt.split('.')
  if (parts.length !== 3 || !parts[1]) return null
  return jsonParse(Buffer.from(parts[1], 'base64url').toString('utf8'))
}
```

刷新调度器的设计：

```typescript
// 文件：src/bridge/jwtUtils.ts，第 52-58 行
const TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000  // 过期前 5 分钟刷新
const FALLBACK_REFRESH_INTERVAL_MS = 30 * 60 * 1000  // 未知过期时间时 30 分钟刷新
const MAX_REFRESH_FAILURES = 3  // 最大连续失败次数
```

### 5.4 支持的 IDE 和运行模式

Claude Code 支持两大 IDE 家族：

- **VS Code**：通过 `sse-ide` 或 `ws-ide` MCP 传输连接
- **JetBrains**：通过相同的 MCP 传输协议

IDE 桥接支持三种会话管理模式（`SpawnMode`）：

```typescript
// 文件：src/bridge/types.ts，第 68-69 行
export type SpawnMode = 'single-session' | 'worktree' | 'same-dir'
// single-session：一个会话在 cwd 中，结束后桥接拆除
// worktree：持久服务器，每个会话获得隔离的 git worktree
// same-dir：持久服务器，所有会话共享 cwd（可能互相干扰）
```

### 5.5 权限回调

当 Claude Code 通过桥接运行时，权限决策需要传回 IDE 端。`bridgePermissionCallbacks.ts` 负责这个双向传递：

```typescript
// 文件：src/bridge/bridgePermissionCallbacks.ts
// 当工具需要权限时，通过 WebSocket 发送权限请求给 IDE
// IDE 显示权限对话框，用户做出决定
// 决定通过 WebSocket 返回给 Claude Code
```

这确保了即使 Claude Code 在远程执行，"人在回路"（Human-in-the-Loop）原则仍然得到遵守。

### 5.6 Remote Control（远程控制��

`bridgeMain.ts`（116KB）是 Remote Control 功能的主入口——用户可以通过 Claude.ai 网页远程控制本地的 Claude Code 实例。其工作原理是：

1. **环境注册**：本地 Claude Code 向 Claude Cloud Relay (CCR) 注册自己为一个"环境"（environment）
2. **工作轮询**：通过 HTTP 长轮询等待 CCR 分发的"工作"（work）
3. **会话创建**：收到工作后，根据 `SpawnMode` 创建新的 Claude Code 会话
4. **双向通信**：通过 WebSocket 将用户消息从 Claude.ai 转发到本地会话，将响应回传

`bridgeMain.ts` 使用了一套完善的自愈机制——指数退避重连、令牌主动刷新、容量信号感知（`capacityWake.ts`）。当远端有新工作到达时，即使本地正在退避等待，容量信号也会立即唤醒轮询循环。

### 5.7 工作密文（WorkSecret）

每个远程会话都携带一个"工作密文"（WorkSecret），它是 base64url ���码的 JSON 结构，包��会话运行所需的全部配置：

```typescript
// 文件：src/bridge/types.ts，第 33-51 行
export type WorkSecret = {
  version: number                    // 密文版本
  session_ingress_token: string      // 会话入口令牌
  api_base_url: string               // API 基础 URL
  sources: Array<{                   // 代码来源（git 仓库信息）
    type: string
    git_info?: { type: string; repo: string; ref?: string; token?: string }
  }>
  auth: Array<{ type: string; token: string }>  // 认证信息
  claude_code_args?: Record<string, string>      // CLI 参数
  mcp_config?: unknown                            // MCP 配置
  environment_variables?: Record<string, string>  // 环境变量
}
```

WorkSecret 的设计遵循了"最小权限"原则——每个会话只获得完成其任务所需的最少信息和权限。

---

## 第六章：/mcp 命令

`/mcp` 命令是用户管理 MCP 服务器的主要界面，定义在 `src/commands/mcp/index.ts` 中：

```typescript
// 文件：src/commands/mcp/index.ts，第 3-11 行
const mcp = {
  type: 'local-jsx',        // 使用 JSX 渲染（React 组件）
  name: 'mcp',
  description: 'Manage MCP servers',
  immediate: true,           // 立即执行，不需要发送给 LLM
  argumentHint: '[enable|disable [server-name]]',
  load: () => import('./mcp.js'),  // 懒加载——按需导入
} satisfies Command
```

### 6.1 功能概览

`/mcp` 命令提供以下管理功能：

1. **查看状态**：列出所有 MCP 服务器及其连接状态（connected/failed/needs-auth/disabled）
2. **启用/禁用**：`/mcp enable <server>` 和 `/mcp disable <server>`
3. **重新连接**：对 failed 或 needs-auth 状态的服务器发起重连
4. **添加服务器**：`/mcp add <url>` 通过 URL 添加新的 MCP 服务器
5. **诊断调试**：显示连接错误详情、传输类型、服务器能力

### 6.2 MCPConnectionManager React 上下文

MCP 连接管理在 UI 层通过 React 上下文（Context）暴露，`MCPConnectionManager.tsx` 提供了两个核心 Hook：

```typescript
// 文件：src/services/mcp/MCPConnectionManager.tsx
export function useMcpReconnect()       // 获取重连函数
export function useMcpToggleEnabled()   // 获取启用/禁用切换函数
```

`useManageMCPConnections` Hook 负责监控配置变更并触发自动重连——当用户修改 settings 文件中的 MCP 配置后，系统会自动检测变更并重新建立受影响的连接。配置比较使用 `areMcpConfigsEqual()` 函数（`client.ts:1710`），通过 JSON 序列化进行深度比较。

### 6.3 安装流程

MCP 服务器可以通过命令行安装。`addCommand.ts` 处理 `claude mcp add` 子命令：

1. 解析传输类型和 URL/命令
2. 验证配置合法性（URL 格式、命令路径存在性）
3. 设置环境变量（如 API 密钥）
4. 写入用户的 settings 文件（全局或项目级）
5. 触发连接以验证配置是否正确

安装命令支持所有传输类型，例如：
- `claude mcp add github -- npx @github/mcp-server`（stdio 类型）
- `claude mcp add my-api --transport http --url https://api.example.com/mcp`（HTTP 类型）

### 6.4 通道权限控制

MCP 系统实现了精细的通道权限控制（`channelPermissions.ts`）。不同来源的 MCP 服务器有不同的信任级别——企业管理的服务器可以使用更多通道（channel），而用户添加的第三方服务器则受到更严格的限制。`channelAllowlist.ts` 维护了一个允许列表，只有被列入白名单的 MCP 工具才能访问特定的系统通道（如文件系统、网络）。这个设计确保了即使用户安装了一个恶意的 MCP 服务器，它也无法通过 MCP 工具获得不当的系统访问权限。

---

## 第七章：协议交互图

### 7.1 Claude Code ↔ MCP 服务器通信流程

```
┌──────────────┐                          ┌──────────────┐
│  Claude Code │                          │  MCP Server  │
│   (Client)   │                          │  (External)  │
└──────┬───────┘                          └──────┬───────┘
       │                                         │
       │  1. connect (transport: stdio/sse/http)  │
       │────────────────────────────────────────→│
       │                                         │
       │  2. initialize (capabilities, version)  │
       │←────────────────────────────────────────│
       │                                         │
       │  3. roots/list (request from server)    │
       │←────────────────────────────────────────│
       │     [return: file://cwd]                │
       │────────────────────────────────────────→│
       │                                         │
       │  4. tools/list                          │
       │────────────────────────────────────────→│
       │     [return: tool definitions + schemas] │
       │←────────────────────────────────────────│
       │                                         │
       │  ═══ 工具注册到 Claude Code 工具系统 ═══  │
       │  名称: mcp__serverName__toolName        │
       │                                         │
       │  5. tools/call (name, arguments)        │
       │────────────────────────────────────────→│
       │     [stream: progress notifications]    │
       │←────────────────────────────────────────│
       │     [return: tool result content]       │
       │←────────────────────────────────────────│
       │                                         │
       │  6. elicitation/request (OAuth re-auth) │
       │←────────────────────────────────────────│
       │     [用户在浏览器中完成认证]               │
       │     [return: elicitation result]        │
       │────────────────────────────────────────→│
       │                                         │
       │  7. resources/list (可选)               │
       │────────────────────────────────────────→│
       │     [return: resource definitions]      │
       │←────────────────────────────────────────│
       │                                         │
       │  8. close (session end)                 │
       │────────────────────────────────────────→│
       │                                         │
```

### 7.2 Claude Code ↔ IDE 通信流程

```
┌──────────────┐    WebSocket / SSE     ┌──────────────┐
│  Claude Code │                        │     IDE      │
│  (CLI/REPL)  │                        │ (VS Code /   │
│              │                        │  JetBrains)  │
└──────┬───────┘                        └──────┬───────┘
       │                                       │
       │  1. 建立 WebSocket 连接                │
       │←──────────────────────────────────────│
       │     [JWT 认证]                        │
       │                                       │
       │  2. IDE 发送用户消息                   │
       │←──────────────────────────────────────│
       │     { type: "user_message",           │
       │       content: "修改 foo.ts..." }     │
       │                                       │
       │  3. Claude Code 处理并流式返回         │
       │──────────────────────────────────────→│
       │     { type: "assistant_message",      │
       │       content: "我来修改..." }        │
       │                                       │
       │  4. 工具调用需要权限                    │
       │──────────────────────────────────────→│
       │     { type: "permission_request",     │
       │       tool: "Write", path: "foo.ts" } │
       │                                       │
       │  5. 用户在 IDE 中批准/拒绝             │
       │←──────────────────────────────────────│
       │     { type: "permission_response",    │
       │       approved: true }                │
       │                                       │
       │  6. MCP 工具通过 IDE MCP 服务器         │
       │←──────────────────────────────────────│
       │     [mcp__ide__executeCode,           │
       │      mcp__ide__getDiagnostics]        │
       │                                       │
       │  7. 控制请求（SDK Control Protocol）    │
       │←──────────────────────────────────────│
       │     { type: "control_request",        │
       │       request: { type: "set_config" } │
       │     }                                 │
       │──────────────────────────────────────→│
       │     { type: "control_response",       │
       │       response: { ... } }             │
       │                                       │
```

---

## 设计哲学分析

MCP 与外部协议子系统是 Claude Code 设计哲学最集中的体现之一。在这个子系统中，多个设计原则交织并相互强化。

### 无需修改的可扩展性（Extensibility Without Modification）

MCP 协议是 Claude Code 中"开闭原则"（Open-Closed Principle）的**极致表达**。通过 MCP，Claude Code 可以连接任意数量的外部工具——GitHub、Slack、数据库、自定义 API——而无需修改核心代码中的一行。

这种可扩展性在多个层面体现：
- **工具扩展**：任何 MCP 服务器暴露的工具自动变成 Claude Code 的工具
- **配置扩展**：七层配置优先级系统允许不同层级注入 MCP 服务器
- **传输扩展**：八种传输类型通过 Zod 联合类型组合，新传输类型只需添加一个 Schema 变体

IDE 桥接系统同样体现了这个原则——Claude Code 不需要为每个 IDE 编写专门的集成代码，而是通过标准的 WebSocket + SDK 消息协议连接，任何遵循此协议的 IDE 扩展都可以接入。

### 可组合性（Composability）

多传输类型支持是"可组合性"的典型案例。`connectToServer()` 函数内部通过 `serverRef.type` 分派到不同的传输实现，但对外呈现统一的 `MCPServerConnection` 接口。这意味着上层代码（工具获取、调用、结果处理）完全不关心底层使用的是 Stdio 还是 WebSocket。

工具名称标准化（`mcp__serverName__toolName`）实现了另一种可组合性——多个 MCP 服务器的工具可以在同一个命名空间中共存，通过前缀隔离避免冲突。

### 防御性编程（Defensive Programming）

MCP 系统面对的是**不受信任的外部输入**，防御性编程无处不在：

1. **名称标准化**：`normalizeNameForMCP()` 将所有非法字符替换为下划线，防止注入攻击和命名冲突
2. **描述长度限制**：`MAX_MCP_DESCRIPTION_LENGTH = 2048` 防止恶意服务器通过超长描述耗尽上下文窗口
3. **结果大小限制**：`maxResultSizeChars: 100_000` 防止单次工具调用淹没上下文
4. **Unicode 清理**：`recursivelySanitizeUnicode()` 对 MCP 服务器返回的工具数据进行递归清理
5. **超时控制**：连接超时（30s）、请求超时（60s）、工具调用超时（~27.8h）三级超时保护
6. **官方注册表 fail-closed**：`isOfficialMcpUrl()` 在注册表未加载时返回 `false`——未知即不可信

### 安全优先设计（Safety-First Design）

IDE 桥接的 JWT 认证是安全优先设计在跨进程通信中的体现。`jwtUtils.ts` 中的主动令牌刷新调度器在令牌过期前 5 分钟自动刷新，确保会话不会因为令牌过期而突然中断，同时限制最大连续失败次数（3 次）以防止无限重试。

MCP OAuth 认证的 `hasMcpDiscoveryButNoToken()` 检查也体现了这个原则——对于已知需要认证但没有令牌的服务器，直接跳过连接尝试，而不是每次都进行无意义的网络往返。

### 优雅降级（Graceful Degradation）

MCP 系统的降级策略遍布各个环节：

- **连接失败**：服务器连接失败不会阻止 Claude Code 启动，失败的服务器被标记为 `failed` 状态，用户可以稍后手动重连
- **认证过期**：`needs-auth` 状态下生成 `McpAuthTool`（认证工具），用户可以通过 `/mcp` 重新认证
- **会话过期**：`McpSessionExpiredError` 触发自动重连（`ensureConnectedClient`），对用户透明
- **引出机制**：OAuth 令牌过期时，MCP 服务器可以通过 elicitation 请求用户重新认证，而不是直接报错

### 性能敏感启动（Performance-Conscious Startup）

MCP 延迟工具加载（deferred tool loading）是"性能敏感启动"的重要实践——初始化时只发送 `tools/list` 获取工具列表和简要描述，完整的工具 schema 在模型首次需要使用该工具时才加载。官方注册表的预取（`prefetchOfficialMcpUrls()`）使用 fire-and-forget 模式，设置 5 秒超时，不会阻塞主启动流程。

### 隔离与遏制（Isolation）

MCP 协议本身就是一层"隔离"——外部工具通过标准协议接口与 Claude Code 交互，不能直接访问内部状态。这种隔离在多个层面体现：

1. **协议隔离**：MCP 服务器只能通过 JSON-RPC 消息与客户端交互，不能直接调用客户端函数或访问内存
2. **进程隔离**：Stdio 传输通过子进程运行，MCP 服务器崩溃不会影响主进程。`InProcessTransport` 使用 `queueMicrotask()` 异步投递消息，确保即使同进程的服务器也无法通过同步调用链影响客户端
3. **命名空间隔离**：`mcp__` 前缀确保外部工具不会意外覆盖内置工具
4. **工具白名单**：IDE MCP 服务器的 `ALLOWED_IDE_TOOLS`（`client.ts:568`）将暴露面缩小到仅 `executeCode` 和 `getDiagnostics` 两个工具——即使 IDE 服务器暴露了大量工具，也只有这两个能被 Claude 使用
5. **权限隔离**：MCP 工具的权限检查使用完全限定名（`getToolNameForPermissionCheck()`），确保用户为内置 `Write` 工具设置的拒绝规则不会意外阻止某个 MCP 服务器名为 `Write` 的工具

### 渐进信任（Progressive Trust）

MCP 系统中的信任模型是渐进式的。从配置来源看：企业管理（enterprise）配置的服务器获得最高信任，用户全局配置次之，项目级配置再次之。从连接状态看：`needs-auth` 不是永久状态——用户可以通过 `/mcp` 命令完成认证，将服务器从"不可信"提升到"已认证"。从官方注册表看：`isOfficialMcpUrl()` 的 fail-closed 设计意味着未知的 MCP 服务器默认被视为非官方——信任需要主动证明。

---

## 关键要点总结

1. **MCP 是 Claude Code 的"万能适配器"**：通过标准化协议连接任意外部工具和服务，支持八种传输类型，是"无需修改的可扩展性"的极致表达
2. **客户端连接管理精细化**：memoized 连接、差异化并发策略（本地 3 / 远程 20）、三级超时保护、会话过期自动重连
3. **工具名称标准化防止冲突**：`mcp__serverName__toolName` 格式确保不同服务器的工具在统一命名空间中共存
4. **七层配置优先级系统**：从 SDK 动态注入到 Claude.ai 组织配置，每个层级都有明确的作用域和优先级
5. **引出机制实现运行时交互**：MCP 服务器可以在工具调用过程中请求用户进行 OAuth 认证或表单填写
6. **LSP 提供代码理解能力**：九种操作（定义跳转、引用查找、类型悬停等）通过 LSPTool 暴露给 Claude
7. **IDE 桥接支持嵌入式运行**：WebSocket + JWT 认证实现双向实时通信，权限决策通过桥接回传到 IDE 端
8. **防御性编程对抗不可信输入**：名称标准化、描述限制、Unicode 清理、结果大小限制、fail-closed 官方注册表

---

## 下一篇预览

**Doc 12：持久化系统**将深入分析 Claude Code 如何管理数据的持久化——从会话存储到 CLAUDE.md 记忆系统，从文件状态缓存到跨机器传送（Teleport）。会话存储不仅记录对话历史，更是崩溃恢复和费用追踪的基础设施。CLAUDE.md 记忆系统则是用户扩展 Claude Code 上下文的最直接方式——无需修改代码，只需编辑文本文件就能改变 Claude 的行为。我们将看到这些持久化机制如何体现"优雅降级"（崩溃恢复）和"上下文窗口经济学"（LRU 缓存管理）。
