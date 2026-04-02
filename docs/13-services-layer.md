# Doc 13：服务层

> **前置阅读：** Doc 0 ~ Doc 12
> **核心问题：** Claude Code 的 API 客户端如何与 Anthropic 后端通信、处理认证、重试失败请求，以及如何收集和导出遥测数据——保证生产环境的可靠性与可观测性？
> **设计哲学重点：** 安全优先设计、优雅降级、防御性编程、可组合性、渐进式信任

---

## 第一章：API 服务 `src/services/api/`

### 1.1 API 服务的整体架构

`src/services/api/` 目录是 Claude Code 与 Anthropic 后端通信的核心枢纽，包含约 20 个文件、总计超过 30 万字节的代码。这个目录负责：消息的发送与接收（流式和非流式）、认证与重试逻辑、错误分类与恢复、文件上传下载、以及 API 调用的遥测记录。

目录结构概览：

```
src/services/api/
├── claude.ts              # 核心客户端（125KB），queryModel 入口
├── errors.ts              # 错误处理与用户友好消息（41KB）
├── errorUtils.ts          # 错误提取工具函数（8KB）
├── withRetry.ts           # 指数退避重试引擎（17KB）
├── bootstrap.ts           # 启动时引导数据拉取（4KB）
├── filesApi.ts            # 文件上传/下载客户端（21KB）
├── logging.ts             # API 调用遥测日志（24KB）
├── client.ts              # Anthropic SDK 客户端构建（16KB）
├── grove.ts               # 消息历史剪枝/压缩
├── sessionIngress.ts      # 会话入口数据
├── emptyUsage.ts          # 空 token 使用量常量
├── firstTokenDate.ts      # 首 token 时间追踪
├── metricsOptOut.ts        # 指标收集退出逻辑
├── overageCreditGrant.ts   # 超额信用管理
├── promptCacheBreakDetection.ts # 提示缓存中断检测（26KB）
├── referral.ts            # 推荐系统
├── adminRequests.ts       # 管理员请求
└── dumpPrompts.ts         # 调试用提示词转储
```

### 1.2 核心客户端 `claude.ts`

`claude.ts` 是整个 API 层的核心，3400+ 行代码，导出了与 Anthropic API 通信的所有关键函数。在 Doc 7（查询引擎）中我们已经了解了 `queryModel` 的高层流程，本章将补充其底层实现细节。

**主要导出函数：**

| 函数名 | 行号 | 职责 |
|--------|------|------|
| `queryModelWithStreaming()` | 752 | 流式查询入口，生成 StreamEvent 异步迭代器 |
| `queryModelWithoutStreaming()` | 709 | 非流式查询入口，返回完整 BetaMessage |
| `executeNonStreamingRequest()` | 818 | 非流式请求的通用辅助生成器 |
| `verifyApiKey()` | 530 | 验证 API 密钥有效性 |
| `updateUsage()` | 2924 | 更新 token 使用量统计 |
| `buildSystemPromptBlocks()` | 3213 | 构建系统提示词块 |
| `getMaxOutputTokensForModel()` | 3399 | 获取模型最大输出 token 数 |

**流式查询的核心入口：**

```typescript
// src/services/api/claude.ts, 第 752-779 行
export async function* queryModelWithStreaming({
  messages,         // 完整的对话历史消息数组
  systemPrompt,     // 系统提示词配置
  thinkingConfig,   // 思考模式配置（enabled/disabled/adaptive）
  tools,            // 可用工具列表
  signal,           // AbortSignal，用于取消请求
  options,          // 模型、温度、查询来源等选项
}: {
  messages: Message[]
  systemPrompt: SystemPrompt
  thinkingConfig: ThinkingConfig
  tools: Tools
  signal: AbortSignal
  options: Options
}): AsyncGenerator<
  StreamEvent | AssistantMessage | SystemAPIErrorMessage, // 产出类型
  void                                                     // 返回类型
> {
  // withStreamingVCR 包装器支持录制/回放（VCR = Video Cassette Recorder），
  // 用于测试和调试场景——录制真实 API 响应以便后续回放
  return yield* withStreamingVCR(messages, async function* () {
    yield* queryModel(
      messages,
      systemPrompt,
      thinkingConfig,
      tools,
      signal,
      options,
    )
  })
}
```

**流空闲看门狗（Stream Idle Watchdog）：**

Claude Code 必须应对一种特别棘手的场景：与 API 的 HTTP 连接已建立，但流式响应"静默断开"——不发送任何数据，也不关闭连接。SDK 的请求超时只覆盖初始 `fetch()` 调用，无法检测流传输过程中的挂起。

```typescript
// src/services/api/claude.ts, 第 1877-1928 行
const STREAM_IDLE_TIMEOUT_MS =
  // 允许通过环境变量覆盖超时值（用于调试和特殊场景）
  parseInt(process.env.CLAUDE_STREAM_IDLE_TIMEOUT_MS || '', 10) || 90_000
  // 默认 90 秒——足够长以容忍偶发的网络延迟，
  // 又足够短以避免用户无限等待

const STREAM_IDLE_WARNING_MS = STREAM_IDLE_TIMEOUT_MS / 2
  // 45 秒时发出警告，让调试日志有早期信号

let streamIdleTimer: ReturnType<typeof setTimeout> | null = null

function resetStreamIdleTimer(): void {
  clearStreamIdleTimers()          // 清除旧的定时器
  if (!streamWatchdogEnabled) {    // 通过环境变量控制开关
    return
  }
  // 设置警告定时器（半程）
  streamIdleWarningTimer = setTimeout(
    warnMs => {
      logForDebugging(
        `Streaming idle warning: no chunks received for ${warnMs / 1000}s`,
        { level: 'warn' },
      )
    },
    STREAM_IDLE_WARNING_MS,
    STREAM_IDLE_WARNING_MS,
  )
  // 设置超时定时器（终止流）
  streamIdleTimer = setTimeout(() => {
    streamIdleAborted = true
    logEvent('tengu_streaming_idle_timeout', {
      model: options.model as AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS,
      timeout_ms: STREAM_IDLE_TIMEOUT_MS,
    })
    releaseStreamResources()       // 主动释放流资源，触发中止
  }, STREAM_IDLE_TIMEOUT_MS)
}
resetStreamIdleTimer()
// 每次收到新的 chunk 时调用 resetStreamIdleTimer()，重置倒计时
```

核心设计思想是：每收到一个流式 chunk 就重置 90 秒倒计时。如果 90 秒内没有任何数据到达，看门狗认为连接已静默断开，主动释放资源并触发重试。这是一种典型的**心跳检测**模式，将"检测不到问题"转化为"检测到超时"。

**Token 使用量追踪的防御性逻辑：**

```typescript
// src/services/api/claude.ts, 第 2924 行
export function updateUsage(
  existingUsage: BetaUsage,
  newUsage: Partial<BetaUsage>,
): BetaUsage {
  // 使用 > 0 守卫条件，防止 message_delta 事件用 0 覆盖真实值
  // 流式 API 中，message_delta 事件会发送 usage 字段，
  // 但部分字段可能为 0（因为增量更新只包含变化的部分）
  return {
    input_tokens:
      newUsage.input_tokens && newUsage.input_tokens > 0
        ? newUsage.input_tokens
        : existingUsage.input_tokens,
    output_tokens:
      newUsage.output_tokens && newUsage.output_tokens > 0
        ? newUsage.output_tokens
        : existingUsage.output_tokens,
    // ... 类似逻辑应用于 cache_creation_input_tokens 等字段
  }
}
```

### 1.3 指数退避重试引擎 `withRetry.ts`

`withRetry.ts`（822 行）实现了一个高度复杂的**指数退避重试引擎**，是 Claude Code 可靠性的关键基石。它不仅处理简单的网络重试，还包含模型降级、快速模式回退、持久重试模式、以及细粒度的错误分类。

**核心常量与架构：**

```
重试策略分层：
┌─────────────────────────────────────────────┐
│  withRetry() —— 异步生成器入口                │
│  ┌─────────────────────────────────────────┐ │
│  │ 快速模式回退                              │ │
│  │ (429/529 → 标准速度降级)                  │ │
│  ├─────────────────────────────────────────┤ │
│  │ 529 错误追踪                              │ │
│  │ (3 次连续 → FallbackTriggeredError)       │ │
│  ├─────────────────────────────────────────┤ │
│  │ 指数退避延迟                              │ │
│  │ 500ms × 2^(n-1), 上限 32s, 抖动 0-25%   │ │
│  ├─────────────────────────────────────────┤ │
│  │ 持久重试模式 (无人值守)                    │ │
│  │ 上限 5min 退避, 30s 心跳, 6h 总上限       │ │
│  └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

**withRetry 的核心实现（简化版）：**

```typescript
// src/services/api/withRetry.ts, 第 52-56 行
const DEFAULT_MAX_RETRIES = 10    // 默认最多重试 10 次
const FLOOR_OUTPUT_TOKENS = 3000  // 输出 token 的最低下限
const MAX_529_RETRIES = 3         // 连续 529 错误最多 3 次
export const BASE_DELAY_MS = 500  // 基础延迟 500 毫秒

// src/services/api/withRetry.ts, 第 170-178 行
export async function* withRetry<T>(
  getClient: () => Promise<Anthropic>,   // 惰性获取客户端（支持重建）
  operation: (                           // 要重试的实际操作
    client: Anthropic,
    attempt: number,
    context: RetryContext,
  ) => Promise<T>,
  options: RetryOptions,                 // 重试配置
): AsyncGenerator<SystemAPIErrorMessage, T> {
  // 返回值是 AsyncGenerator：在等待重试时 yield 系统消息
  // 通知用户当前的重试状态，同时保持 UI 响应
  const maxRetries = getMaxRetries(options)
  let consecutive529Errors = options.initialConsecutive529Errors ?? 0

  for (let attempt = 1; attempt <= maxRetries + 1; attempt++) {
    if (options.signal?.aborted) {
      throw new APIUserAbortError()    // 尊重取消信号
    }
    try {
      return await operation(client, attempt, retryContext)
    } catch (error) {
      // ... 错误分类与重试逻辑 ...
    }
  }
}
```

**指数退避延迟计算：**

```typescript
// src/services/api/withRetry.ts, 第 530-548 行
export function getRetryDelay(
  attempt: number,
  retryAfterHeader?: string | null,  // 服务器返回的 Retry-After 头
  maxDelayMs = 32000,                // 最大延迟上限 32 秒
): number {
  // 优先使用服务器指定的重试延迟
  if (retryAfterHeader) {
    const seconds = parseInt(retryAfterHeader, 10)
    if (!isNaN(seconds)) {
      return seconds * 1000          // 服务器说多久就等多久
    }
  }

  // 指数退避：500ms → 1s → 2s → 4s → 8s → 16s → 32s（封顶）
  const baseDelay = Math.min(
    BASE_DELAY_MS * Math.pow(2, attempt - 1),  // 2 的幂次递增
    maxDelayMs,                                  // 不超过 32 秒
  )
  // 添加 0-25% 随机抖动，防止"惊群效应"
  // （多个客户端在完全相同的时刻重试，导致服务器再次过载）
  const jitter = Math.random() * 0.25 * baseDelay
  return baseDelay + jitter
}
```

### 1.4 错误分类体系

Claude Code 的错误分类体系是其可靠性设计的核心——不同类型的错误需要完全不同的处理策略。

**529 过载错误检测：**

```typescript
// src/services/api/withRetry.ts, 第 610-621 行
export function is529Error(error: unknown): boolean {
  if (!(error instanceof APIError)) {
    return false
  }
  // 双重检测策略：状态码 + 消息内容
  return (
    error.status === 529 ||
    // SDK 在流式传输中有时无法正确传递 529 状态码，
    // 所以需要检查错误消息中的 "overloaded_error" 字符串
    (error.message?.includes('"type":"overloaded_error"') ?? false)
  )
}
```

这个看似简单的函数隐藏了一个重要的工程教训：Anthropic SDK 在流式传输过程中遇到 529 错误时，有时无法正确传递 HTTP 状态码（因为流式连接已经建立，529 到达时通过错误事件而非 HTTP 响应），所以必须同时检查消息内容作为后备。

**OAuth Token 吊销检测：**

```typescript
// src/services/api/withRetry.ts, 第 623-629 行
function isOAuthTokenRevokedError(error: unknown): boolean {
  return (
    error instanceof APIError &&
    error.status === 403 &&                                    // 403 Forbidden
    (error.message?.includes('OAuth token has been revoked') ?? false)
    // 当另一个进程刷新了 token，旧 token 被吊销
    // 此时需要重新获取 token 而不是放弃
  )
}
```

**连接过期检测：**

```typescript
// src/services/api/withRetry.ts, 第 112-118 行
function isStaleConnectionError(error: unknown): boolean {
  if (!(error instanceof APIConnectionError)) {
    return false
  }
  // 检查底层 TCP 错误码
  const details = extractConnectionErrorDetails(error)
  return details?.code === 'ECONNRESET'    // 对端重置连接
      || details?.code === 'EPIPE'          // 向已关闭的连接写入
  // 这些通常发生在 HTTP Keep-Alive 连接被服务器静默关闭后
  // 解决方案：禁用连接池并重新连接
}
```

**错误恢复策略对照表：**

| 错误类型 | 状态码 | 处理策略 |
|----------|--------|----------|
| 529 过载 | 529 / 消息匹配 | 最多 3 次重试，然后降级到备用模型 |
| 429 限速 | 429 | 遵守 Retry-After 头，订阅用户不重试 |
| 401 认证失败 | 401 | 刷新 OAuth token 或清除 API key 缓存 |
| 403 Token 吊销 | 403 | 强制 token 刷新后重试 |
| 连接重置 | ECONNRESET | 禁用 Keep-Alive，重建连接 |
| 上下文溢出 | 400 | 自动调整 max_tokens 后重试 |
| 连接超时 | 408 | 标准指数退避重试 |
| 服务器内部错误 | 5xx | 标准指数退避重试 |

**前台/后台查询源区分：**

```typescript
// src/services/api/withRetry.ts, 第 62-88 行
// 前台查询源（用户正在等待结果）—— 允许 529 重试
const FOREGROUND_529_RETRY_SOURCES = new Set<QuerySource>([
  'repl_main_thread',       // 主对话线程
  'sdk',                    // SDK 调用
  'agent:custom',           // 自定义代理
  'agent:default',          // 默认代理
  'compact',                // 上下文压缩
  'auto_mode',              // 安全分类器（自动模式正确性依赖）
  // ... 更多前台源
])

function shouldRetry529(querySource: QuerySource | undefined): boolean {
  // 非前台源（如摘要生成、标题建议、分类器）在 529 时立即放弃
  // 原因：容量级联时每次重试产生 3-10 倍的网关放大效应
  // 且用户不会看到这些后台任务的失败
  return (
    querySource === undefined ||                    // 未标记的调用路径保守重试
    FOREGROUND_529_RETRY_SOURCES.has(querySource)   // 前台源允许重试
  )
}
```

### 1.5 引导数据拉取 `bootstrap.ts`

`bootstrap.ts`（142 行）在应用启动时从 Anthropic API 拉取客户端配置数据，如额外的模型选项。

```typescript
// src/services/api/bootstrap.ts, 第 19-38 行
const bootstrapResponseSchema = lazySchema(() =>
  z.object({
    client_data: z.record(z.unknown()).nullish(),
    additional_model_options: z
      .array(
        z.object({
          model: z.string(),        // 模型 ID
          name: z.string(),         // 显示名称
          description: z.string(),  // 描述文字
        })
        .transform(({ model, name, description }) => ({
          value: model,             // 转换为下拉框友好的格式
          label: name,
          description,
        })),
      )
      .nullish(),
  }),
)

// src/services/api/bootstrap.ts, 第 114-141 行
export async function fetchBootstrapData(): Promise<void> {
  try {
    const response = await fetchBootstrapAPI()
    if (!response) return

    // 只有数据实际变化时才持久化——避免每次启动都写入配置文件
    const config = getGlobalConfig()
    if (
      isEqual(config.clientDataCache, clientData) &&
      isEqual(config.additionalModelOptionsCache, additionalModelOptions)
    ) {
      return  // 缓存未变，跳过写入
    }

    saveGlobalConfig(current => ({
      ...current,
      clientDataCache: clientData,
      additionalModelOptionsCache: additionalModelOptions,
    }))
  } catch (error) {
    logError(error)  // 引导数据拉取失败不阻塞启动——优雅降级
  }
}
```

关键设计点：`fetchBootstrapData` 在三种情况下跳过请求——隐私模式（`isEssentialTrafficOnly()`）、第三方 API 提供商（非 Anthropic 直连）、以及无可用认证凭据。这是**渐进式信任**和**最小权限**原则的体现。

### 1.6 文件 API 客户端 `filesApi.ts`

`filesApi.ts`（500+ 行）处理文件的上传和下载，主要用于 Claude Code 会话中的文件附件。

```typescript
// src/services/api/filesApi.ts, 第 47-66 行
export type File = {
  fileId: string          // 服务端文件 ID
  relativePath: string    // 相对于工作目录的路径
}

export type FilesApiConfig = {
  oauthToken: string      // OAuth token（来自会话 JWT）
  baseUrl?: string        // API 基础 URL
  sessionId: string       // 会话 ID，用于创建会话专属目录
}

export type DownloadResult = {
  fileId: string
  path: string
  success: boolean
  error?: string
  bytesWritten?: number
}
```

文件 API 使用 Beta 头 `files-api-2025-04-14,oauth-2025-04-20` 来启用文件 API 和 OAuth 认证，并包含最多 3 次的重试逻辑。

### 1.7 API 调用日志 `logging.ts`

`logging.ts`（600+ 行）负责记录每次 API 调用的遥测数据，用于性能监控和问题诊断。一个巧妙的功能是**网关检测**——自动识别用户是否通过 LiteLLM、Helicone、Portkey、Cloudflare AI Gateway、Kong 或 Braintrust 等中间代理访问 API：

```typescript
// src/services/api/logging.ts, 第 65-93 行
const GATEWAY_FINGERPRINTS: Partial<
  Record<KnownGateway, { prefixes: string[] }>
> = {
  litellm: { prefixes: ['x-litellm-'] },          // LiteLLM 代理
  helicone: { prefixes: ['helicone-'] },           // Helicone 可观测性
  portkey: { prefixes: ['x-portkey-'] },           // Portkey AI 网关
  'cloudflare-ai-gateway': { prefixes: ['cf-aig-'] },
  kong: { prefixes: ['x-kong-'] },                 // Kong API 网关
  braintrust: { prefixes: ['x-bt-'] },             // Braintrust 代理
}

function detectGateway({
  headers,
  baseUrl,
}: {
  headers?: globalThis.Headers
  baseUrl?: string
}): KnownGateway | undefined {
  // 方法一：通过响应头前缀检测
  if (headers) {
    const headerNames: string[] = []
    headers.forEach((_, key) => headerNames.push(key))
    for (const [gw, { prefixes }] of Object.entries(GATEWAY_FINGERPRINTS)) {
      if (prefixes.some(p => headerNames.some(h => h.startsWith(p)))) {
        return gw as KnownGateway
      }
    }
  }
  // 方法二：通过 URL 域名后缀检测（针对 Databricks 等托管服务）
  // ...
}
```

网关检测的目的是在遥测数据中标记请求经过的中间层，帮助诊断"为什么我的 API 调用很慢"——答案可能是用户配置的网关引入了额外延迟。

---

## 第二章：OAuth 认证 `src/services/oauth/`

### 2.1 OAuth 服务的架构

`src/services/oauth/` 目录实现了完整的 OAuth 2.0 授权码流程（Authorization Code Flow）加上 PKCE（Proof Key for Code Exchange）扩展，这是目前公认最安全的 OAuth 公共客户端认证方式。

```
src/services/oauth/
├── index.ts              # OAuthService 类——流程编排（199 行）
├── client.ts             # HTTP 客户端——API 调用（450+ 行）
├── auth-code-listener.ts # 本地 HTTP 服务器——捕获回调（190+ 行）
├── crypto.ts             # PKCE 密码学工具（24 行）
└── getOauthProfile.ts    # 用户资料获取（60+ 行）
```

### 2.2 OAuth 2.0 + PKCE 流程详解

标准 OAuth 2.0 授权码流程需要 client_secret，但 CLI 工具无法安全存储密钥（用户可以反编译查看）。PKCE 扩展解决了这个问题：客户端在每次认证时生成一次性密码学证明，无需长期密钥。

```
完整 OAuth + PKCE 流程：

1. 生成 PKCE 参数
   ┌─────────────────────────────────┐
   │ code_verifier = random(32字节)   │ ←── 随机生成
   │ code_challenge = SHA256(verifier)│ ←── 单向哈希
   │ state = random(32字节)           │ ←── CSRF 防护
   └─────────────────────────────────┘

2. 启动本地 HTTP 服务器
   ┌─────────────────────────────────┐
   │ localhost:[随机端口]/callback    │ ←── 监听授权回调
   └─────────────────────────────────┘

3. 打开浏览器 → 授权页面
   ┌─────────────────────────────────┐
   │ URL 包含:                        │
   │  - client_id                     │
   │  - code_challenge (SHA256 哈希)  │
   │  - redirect_uri (localhost:port) │
   │  - scope (user:inference 等)     │
   │  - state (CSRF 令牌)            │
   └─────────────────────────────────┘

4. 用户在浏览器中授权 → 重定向到 localhost
   ┌─────────────────────────────────┐
   │ localhost:port/callback          │
   │  ?code=AUTH_CODE                 │
   │  &state=STATE                    │ ←── 验证 state 匹配
   └─────────────────────────────────┘

5. 用 AUTH_CODE + code_verifier 换取 token
   ┌─────────────────────────────────┐
   │ POST /oauth/token                │
   │  grant_type=authorization_code   │
   │  code=AUTH_CODE                  │
   │  code_verifier=原始随机值         │ ←── 证明持有者身份
   └─────────────────────────────────┘

6. 返回 access_token + refresh_token
```

**PKCE 密码学工具的实现：**

```typescript
// src/services/oauth/crypto.ts, 第 1-23 行
import { createHash, randomBytes } from 'crypto'

function base64URLEncode(buffer: Buffer): string {
  return buffer
    .toString('base64')
    .replace(/\+/g, '-')    // + → -（URL 安全替换）
    .replace(/\//g, '_')    // / → _（URL 安全替换）
    .replace(/=/g, '')      // 移除填充字符
}

export function generateCodeVerifier(): string {
  return base64URLEncode(randomBytes(32))    // 32 字节随机值
}

export function generateCodeChallenge(verifier: string): string {
  const hash = createHash('sha256')          // SHA-256 单向哈希
  hash.update(verifier)
  return base64URLEncode(hash.digest())      // 只发送哈希，不发送原值
}

export function generateState(): string {
  return base64URLEncode(randomBytes(32))    // CSRF 防护令牌
}
```

PKCE 的安全性在于：即使攻击者截获了 `code_challenge`（公开发送给服务器），也无法通过它反推出 `code_verifier`（SHA-256 是不可逆的）。而在第 5 步换取 token 时，必须提供原始的 `code_verifier`，服务器会重新计算哈希并与之前收到的 `code_challenge` 比对。

### 2.3 OAuthService 流程编排

`OAuthService` 类是 OAuth 流程的顶层编排器，协调自动流程（浏览器重定向）和手动流程（用户粘贴授权码）的竞争：

```typescript
// src/services/oauth/index.ts, 第 21-132 行
export class OAuthService {
  private codeVerifier: string                    // PKCE 验证器
  private authCodeListener: AuthCodeListener | null = null
  private manualAuthCodeResolver: ((authorizationCode: string) => void) | null = null

  constructor() {
    this.codeVerifier = crypto.generateCodeVerifier()  // 每次认证生成新的
  }

  async startOAuthFlow(
    authURLHandler: (url: string, automaticUrl?: string) => Promise<void>,
    options?: { /* ... */ },
  ): Promise<OAuthTokens> {
    // 步骤 1：创建本地回调服务器
    this.authCodeListener = new AuthCodeListener()
    this.port = await this.authCodeListener.start()

    // 步骤 2：生成 PKCE 参数
    const codeChallenge = crypto.generateCodeChallenge(this.codeVerifier)
    const state = crypto.generateState()

    // 步骤 3：构建两种 URL（自动 + 手动）
    const manualFlowUrl = client.buildAuthUrl({ ...opts, isManual: true })
    const automaticFlowUrl = client.buildAuthUrl({ ...opts, isManual: false })

    // 步骤 4：竞争等待——自动回调 vs 手动粘贴，谁先到谁赢
    const authorizationCode = await this.waitForAuthorizationCode(
      state,
      async () => {
        await authURLHandler(manualFlowUrl)    // 显示手动 URL 给用户
        await openBrowser(automaticFlowUrl)    // 同时尝试自动打开浏览器
      },
    )

    // 步骤 5：用授权码换取 token
    const tokenResponse = await client.exchangeCodeForTokens(
      authorizationCode,
      state,
      this.codeVerifier,
      this.port!,
      !isAutomaticFlow,
    )

    // 步骤 6：获取用户资料（订阅类型、限速层级）
    const profileInfo = await client.fetchProfileInfo(
      tokenResponse.access_token,
    )

    return this.formatTokens(tokenResponse, profileInfo.subscriptionType, ...)
  }
}
```

### 2.4 Token 刷新机制

`refreshOAuthToken()` 处理 token 过期后的自动刷新，包含一个精妙的优化——跳过不必要的 profile 请求：

```typescript
// src/services/oauth/client.ts, 第 146-274 行
export async function refreshOAuthToken(
  refreshToken: string,
  { scopes: requestedScopes }: { scopes?: string[] } = {},
): Promise<OAuthTokens> {
  const requestBody = {
    grant_type: 'refresh_token',       // 使用刷新令牌
    refresh_token: refreshToken,
    client_id: getOauthConfig().CLIENT_ID,
    scope: (requestedScopes?.length
      ? requestedScopes
      : CLAUDE_AI_OAUTH_SCOPES
    ).join(' '),
  }

  const response = await axios.post(getOauthConfig().TOKEN_URL, requestBody, {
    headers: { 'Content-Type': 'application/json' },
    timeout: 15000,
  })

  const data = response.data as OAuthTokenExchangeResponse
  const expiresAt = Date.now() + data.expires_in * 1000

  // 优化：如果已有完整的 profile 数据（配置文件 + 安全存储），
  // 跳过额外的 /api/oauth/profile 网络请求。
  // 这个优化在全量部署后可减少约 700 万次/天的请求。
  const haveProfileAlready =
    config.oauthAccount?.billingType !== undefined &&
    config.oauthAccount?.accountCreatedAt !== undefined &&
    existing?.subscriptionType != null &&
    existing?.rateLimitTier != null

  const profileInfo = haveProfileAlready
    ? null                                     // 跳过——数据已完整
    : await fetchProfileInfo(accessToken)      // 需要获取

  return {
    accessToken,
    refreshToken: newRefreshToken,
    expiresAt,
    scopes,
    // 三级回退链：新获取 → 安全存储中的现有值 → null
    subscriptionType:
      profileInfo?.subscriptionType ?? existing?.subscriptionType ?? null,
    rateLimitTier:
      profileInfo?.rateLimitTier ?? existing?.rateLimitTier ?? null,
  }
}
```

注意 `subscriptionType` 的三级回退链 (`profileInfo → existing → null`)。代码注释中解释了一个微妙的 bug 防护：在 `CLAUDE_CODE_OAUTH_REFRESH_TOKEN` 重新登录路径中，`installOAuthTokens` 会在返回后调用 `performLogout()` 清空安全存储。如果这里返回 `null`，后续保存时会读到已清空的安全存储，导致订阅类型永久丢失。通过传递现有值，避免了这个竞态条件。

### 2.5 本地回调服务器 `AuthCodeListener`

`AuthCodeListener` 创建一个临时的本地 HTTP 服务器来捕获 OAuth 回调：

```typescript
// src/services/oauth/auth-code-listener.ts, 第 17-51 行
export class AuthCodeListener {
  private localServer: Server
  private port: number = 0
  private expectedState: string | null = null   // CSRF 防护
  private pendingResponse: ServerResponse | null = null

  constructor(callbackPath: string = '/callback') {
    this.localServer = createServer()
    this.callbackPath = callbackPath
  }

  async start(port?: number): Promise<number> {
    return new Promise((resolve, reject) => {
      // 监听 0 端口——让操作系统分配一个可用端口
      // 避免端口冲突（用户可能同时运行多个 Claude Code 实例）
      this.localServer.listen(port ?? 0, 'localhost', () => {
        const address = this.localServer.address() as AddressInfo
        this.port = address.port    // 读取 OS 分配的实际端口
        resolve(this.port)
      })
    })
  }
}
```

关键安全措施：
- 只监听 `localhost`（不对外暴露）
- 使用随机端口避免冲突
- 验证 `state` 参数防止 CSRF 攻击
- 使用完毕后立即关闭服务器

---

## 第三章：分析系统 `src/services/analytics/`

### 3.1 分析系统的整体架构

`src/services/analytics/` 实现了一个**多层事件收集与分发系统**，负责将 Claude Code 的运行时遥测数据路由到多个后端。

```
事件生命周期：
┌────────────────────────────────────────────────────────┐
│  logEvent('tengu_api_query', { model, tokens })        │  ← 调用点（1000+ 处）
└────────────────────┬───────────────────────────────────┘
                     │
         ┌───────────▼───────────┐
         │   index.ts (入口)      │  ← 零依赖，事件排队
         │   ┌─────────────────┐ │
         │   │ eventQueue[]    │ │  ← 启动前的事件暂存
         │   └────────┬────────┘ │
         │            │ attachAnalyticsSink()
         └────────────┼──────────┘
                      │
         ┌────────────▼──────────┐
         │    sink.ts (路由器)    │  ← 采样 + 分发
         │    ┌────────────────┐ │
         │    │ shouldSample() │ │  ← GrowthBook 动态采样率
         │    └───┬────────┬───┘ │
         └────────┼────────┼─────┘
                  │        │
      ┌───────────▼──┐  ┌──▼───────────────────────┐
      │  Datadog      │  │  1P Event Logging         │
      │  datadog.ts   │  │  firstPartyEventLogger.ts │
      │  (HTTP 日志)   │  │  (OpenTelemetry 管道)      │
      └──────────────┘  └──────────────┬────────────┘
                                       │
                        ┌──────────────▼────────────┐
                        │ firstPartyEventLogging     │
                        │ Exporter.ts                │
                        │ (gRPC/HTTP Proto 批量导出)  │
                        │ → /api/event_logging/batch │
                        └───────────────────────────┘
```

目录文件一览：

```
src/services/analytics/
├── index.ts                         # 公共 API：logEvent/logEventAsync（174 行）
├── sink.ts                          # 事件路由器：Datadog + 1P 分发（115 行）
├── config.ts                        # 分析禁用条件判断（39 行）
├── growthbook.ts                    # GrowthBook Feature Flag 管理（1150+ 行）
├── firstPartyEventLogger.ts         # 1P 事件记录器（400+ 行）
├── firstPartyEventLoggingExporter.ts# 1P 事件导出器（800+ 行）
├── datadog.ts                       # Datadog 日志客户端（230+ 行）
├── metadata.ts                      # 事件元数据enrichment（800+ 行）
├── sink.ts                          # 事件路由分发
└── sinkKillswitch.ts                # 远程紧急熔断开关（26 行）
```

### 3.2 零依赖的事件入口 `index.ts`

`index.ts` 是分析系统的公共 API，设计上刻意**零内部依赖**，这是一个关键的架构决策：

```typescript
// src/services/analytics/index.ts, 第 1-9 行
/**
 * Analytics service - public API for event logging
 *
 * DESIGN: This module has NO dependencies to avoid import cycles.
 * Events are queued until attachAnalyticsSink() is called during app initialization.
 * The sink handles routing to Datadog and 1P event logging.
 */
```

**安全标记类型系统：**

```typescript
// src/services/analytics/index.ts, 第 19 行
export type AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS = never
```

这个长名称不是随意取的——它是一个**编译时安全护栏**。`never` 类型意味着没有任何值可以直接赋值给它，开发者必须显式使用 `as` 类型断言才能传入字符串。这迫使开发者在每个日志调用点停下来思考："我传入的这个字符串是否包含代码片段或文件路径？"

```typescript
// 错误用法——编译错误
logEvent('my_event', { path: '/Users/alice/secret.key' })

// 正确用法——显式确认安全
logEvent('my_event', {
  path: sanitizedValue as AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS
})
```

同样的模式还有一个 PII 变体：

```typescript
// src/services/analytics/index.ts, 第 33 行
export type AnalyticsMetadata_I_VERIFIED_THIS_IS_PII_TAGGED = never
```

带 `_PROTO_` 前缀的元数据键会被路由到 PII 标记的 Protobuf 列（有特殊访问控制），`stripProtoFields()` 确保这些敏感字段不会泄露到 Datadog 等通用后端。

**事件排队与 Sink 注入：**

```typescript
// src/services/analytics/index.ts, 第 80-123 行
const eventQueue: QueuedEvent[] = []  // 启动前的事件暂存
let sink: AnalyticsSink | null = null

export function attachAnalyticsSink(newSink: AnalyticsSink): void {
  if (sink !== null) {
    return              // 幂等：重复调用无害
  }
  sink = newSink

  // 异步排空队列——避免阻塞启动关键路径
  if (eventQueue.length > 0) {
    const queuedEvents = [...eventQueue]  // 快照
    eventQueue.length = 0                 // 清空原队列

    queueMicrotask(() => {
      // 微任务中处理排队事件——不阻塞当前执行
      for (const event of queuedEvents) {
        if (event.async) {
          void sink!.logEventAsync(event.eventName, event.metadata)
        } else {
          sink!.logEvent(event.eventName, event.metadata)
        }
      }
    })
  }
}

export function logEvent(
  eventName: string,
  metadata: LogEventMetadata,  // 注意：不允许 string 值！
): void {
  if (sink === null) {
    eventQueue.push({ eventName, metadata, async: false })  // 排队等待
    return
  }
  sink.logEvent(eventName, metadata)
}
```

### 3.3 事件路由器 `sink.ts`

`sink.ts` 是事件分发的中枢，决定每个事件应该发送到哪些后端：

```typescript
// src/services/analytics/sink.ts, 第 48-72 行
function logEventImpl(eventName: string, metadata: LogEventMetadata): void {
  // 第一步：采样检查
  const sampleResult = shouldSampleEvent(eventName)
  if (sampleResult === 0) {
    return          // 被采样策略丢弃——不记录
  }

  // 第二步：注入采样率元数据
  const metadataWithSampleRate =
    sampleResult !== null
      ? { ...metadata, sample_rate: sampleResult }  // 附加采样率供后端还原
      : metadata

  // 第三步：Datadog 分发（通用后端——去除 PII 标记字段）
  if (shouldTrackDatadog()) {
    void trackDatadogEvent(eventName, stripProtoFields(metadataWithSampleRate))
  }

  // 第四步：1P 分发（特权后端——保留 _PROTO_* 字段）
  logEventTo1P(eventName, metadataWithSampleRate)
}
```

**远程紧急熔断开关：**

```typescript
// src/services/analytics/sinkKillswitch.ts, 第 1-25 行
const SINK_KILLSWITCH_CONFIG_NAME = 'tengu_frond_boric'
  // 故意使用混淆名称——防止轻易猜测和误操作

export type SinkName = 'datadog' | 'firstParty'

// GrowthBook 动态配置：{ datadog?: boolean, firstParty?: boolean }
// true = 停止向该 sink 发送所有事件
// 默认 {} = 所有 sink 正常运行（fail-open 设计）
export function isSinkKilled(sink: SinkName): boolean {
  const config = getDynamicConfig_CACHED_MAY_BE_STALE<
    Partial<Record<SinkName, boolean>>
  >(SINK_KILLSWITCH_CONFIG_NAME, {})
  return config?.[sink] === true
}
```

这是一种**远程熔断器**模式：如果某个分析后端出现问题（如 Datadog 端点宕机导致请求堆积），运维人员可以通过 GrowthBook 远程配置立即关闭该 sink，无需发布新版本。

### 3.4 GrowthBook Feature Flag 管理

`growthbook.ts`（1150+ 行）是 Claude Code 中最大的分析组件，实现了完整的 Feature Flag 生命周期管理。

**四层值解析优先级：**

```
┌─────────────────────────────────────────────┐
│  1. 环境变量覆盖 (CLAUDE_INTERNAL_FC_OVERRIDES) │  ← 最高优先级
│  2. /config 界面覆盖 (ant-only)                │
│  3. 内存缓存 (remoteEvalFeatureValues Map)      │
│  4. 磁盘缓存 (cachedGrowthBookFeatures)        │  ← 跨进程持久化
│  5. 默认值 (defaultValue 参数)                  │  ← 最低优先级
└─────────────────────────────────────────────┘
```

**核心读取函数：**

```typescript
// src/services/analytics/growthbook.ts, 第 734-775 行
export function getFeatureValue_CACHED_MAY_BE_STALE<T>(
  feature: string,
  defaultValue: T,
): T {
  // 第 1 层：环境变量覆盖（评估工具用，确保测试确定性）
  const overrides = getEnvOverrides()
  if (overrides && feature in overrides) {
    return overrides[feature] as T
  }

  // 第 2 层：/config 界面覆盖（ant 员工运行时调试用）
  const configOverrides = getConfigOverrides()
  if (configOverrides && feature in configOverrides) {
    return configOverrides[feature] as T
  }

  if (!isGrowthBookEnabled()) {
    return defaultValue
  }

  // 记录实验曝光（A/B 测试追踪）
  if (experimentDataByFeature.has(feature)) {
    logExposureForFeature(feature)
  } else {
    pendingExposures.add(feature)   // 初始化前的访问，延迟记录
  }

  // 第 3 层：内存缓存（最新的远程评估结果）
  if (remoteEvalFeatureValues.has(feature)) {
    return remoteEvalFeatureValues.get(feature) as T
  }

  // 第 4 层：磁盘缓存（跨进程持久化，上次成功加载的值）
  try {
    const cached = getGlobalConfig().cachedGrowthBookFeatures?.[feature]
    return cached !== undefined ? (cached as T) : defaultValue
  } catch {
    return defaultValue   // 第 5 层：默认值（配置读取失败时的终极回退）
  }
}
```

函数名中的 `_CACHED_MAY_BE_STALE` 后缀是一种自文档化的命名约定——它明确告诉调用者："你拿到的值可能不是最新的，不要用于安全关键决策。" 对于安全关键的场景，有一个阻塞版本：

```typescript
// src/services/analytics/growthbook.ts, 第 851 行
export async function checkSecurityRestrictionGate(gate: string): Promise<boolean>
// 这个函数会等待 GrowthBook 初始化完成，确保返回最新值
```

**周期性刷新机制：**

```typescript
// src/services/analytics/growthbook.ts, 第 1087 行
export function setupPeriodicGrowthBookRefresh(): void
// 内部员工：每 20 分钟刷新（更快获得新配置）
// 外部用户：每 6 小时刷新（减少请求频率）
```

**订阅者通知模式：**

```typescript
// src/services/analytics/growthbook.ts, 第 139-157 行
export function onGrowthBookRefresh(
  listener: GrowthBookRefreshListener,
): () => void {
  let subscribed = true
  const unsubscribe = refreshed.subscribe(() => callSafe(listener))
  // 补发通知：如果注册时 GrowthBook 已经初始化完成，
  // 在下一个微任务中触发一次回调。
  // 这处理了一个竞态条件：外部构建中 GB 网络响应可能在
  // ~100ms 内完成，而 REPL 挂载需要 ~600ms。
  if (remoteEvalFeatureValues.size > 0) {
    queueMicrotask(() => {
      if (subscribed && remoteEvalFeatureValues.size > 0) {
        callSafe(listener)
      }
    })
  }
  return () => {
    subscribed = false
    unsubscribe()
  }
}
```

### 3.5 OpenTelemetry 集成 —— 1P 事件管道

Claude Code 使用 OpenTelemetry SDK（而非自研方案）作为 1P（第一方）事件日志的传输层。这是一个务实的架构选择——复用成熟的批处理和导出基础设施，而不是重新发明轮子。

```typescript
// src/services/analytics/firstPartyEventLogger.ts, 第 0-9 行
import type { AnyValueMap, Logger, logs } from '@opentelemetry/api-logs'
import { resourceFromAttributes } from '@opentelemetry/resources'
import {
  BatchLogRecordProcessor,   // OTel 批处理器——控制导出节奏
  LoggerProvider,
} from '@opentelemetry/sdk-logs'
import {
  ATTR_SERVICE_NAME,
  ATTR_SERVICE_VERSION,
} from '@opentelemetry/semantic-conventions'
```

**事件记录流程：**

```typescript
// src/services/analytics/firstPartyEventLogger.ts, 第 156-207 行
async function logEventTo1PAsync(
  firstPartyEventLogger: Logger,
  eventName: string,
  metadata: Record<string, number | boolean | undefined> = {},
): Promise<void> {
  // 1. 用核心元数据enrichment（模型、会话、环境上下文）
  const coreMetadata = await getEventMetadata({
    model: metadata.model,
    betas: metadata.betas,
  })

  // 2. 构建 OTel 属性——直接传递对象，无需 JSON 序列化
  const attributes = {
    event_name: eventName,
    event_id: randomUUID(),
    core_metadata: coreMetadata,
    user_metadata: getCoreUserData(true),
    event_metadata: metadata,
  } as unknown as AnyValueMap

  // 3. 添加用户 ID（如果可用）
  const userId = getOrCreateUserID()
  if (userId) {
    attributes.user_id = userId
  }

  // 4. 通过 OTel Logger 发射日志记录
  firstPartyEventLogger.emit({
    body: eventName,
    attributes,
  })
  // OTel 的 BatchLogRecordProcessor 会自动：
  // - 在 5 秒间隔或 200 条事件时触发批量导出
  // - 调用 FirstPartyEventLoggingExporter.export()
}
```

**事件采样配置：**

```typescript
// src/services/analytics/firstPartyEventLogger.ts, 第 31-84 行
export type EventSamplingConfig = {
  [eventName: string]: {
    sample_rate: number    // 0-1 之间：0=全部丢弃, 1=全部保留
  }
}

export function shouldSampleEvent(eventName: string): number | null {
  const config = getEventSamplingConfig()  // 从 GrowthBook 动态获取
  const eventConfig = config[eventName]

  if (!eventConfig) {
    return null           // 未配置=100% 保留（不添加采样率元数据）
  }

  const sampleRate = eventConfig.sample_rate

  if (sampleRate >= 1) return null    // 100% 保留
  if (sampleRate <= 0) return 0       // 0% 保留（全部丢弃）

  // 随机采样——返回采样率供后端进行统计还原
  return Math.random() < sampleRate ? sampleRate : 0
}
```

采样率不是硬编码的，而是通过 GrowthBook 动态配置（`tengu_event_sampling_config`），运维人员可以在不发布新版本的情况下调整任意事件的采样率——例如在排查问题时临时将某个事件设为 100%，或在高负载期间降低全局采样率。

### 3.6 1P 事件导出器 `FirstPartyEventLoggingExporter`

`FirstPartyEventLoggingExporter`（800+ 行）实现了 OpenTelemetry 的 `LogRecordExporter` 接口，是事件数据离开客户端的最后一站：

```typescript
// src/services/analytics/firstPartyEventLoggingExporter.ts, 第 58-79 行
/**
 * Exporter for 1st-party event logging to /api/event_logging/batch.
 *
 * Export cycles are controlled by OpenTelemetry's BatchLogRecordProcessor:
 * - Time interval: default 5 seconds (scheduledDelayMillis)
 * - Batch size: default 200 events (maxExportBatchSize)
 *
 * This exporter adds resilience:
 * - Append-only log for failed events (concurrency-safe)
 * - Quadratic backoff retry for failed events
 * - Immediate retry when endpoint becomes healthy
 * - Auth fallback: retries without auth on 401
 */
export class FirstPartyEventLoggingExporter implements LogRecordExporter {
  private readonly endpoint: string         // /api/event_logging/batch
  private readonly timeout: number          // 网络超时
  private readonly maxBatchSize: number     // 批量大小上限
  private readonly skipAuth: boolean        // 跳过认证标志
  // ...
}
```

该导出器使用 Protobuf 格式（通过 `ClaudeCodeInternalEvent` 和 `GrowthbookExperimentEvent` 类型）序列化事件，然后通过 HTTP POST 批量发送到 Anthropic 后端。失败的事件会被写入本地磁盘（`~/.claude/telemetry/1p_failed_events.*`），并在后续导出成功时自动重试。

### 3.7 Datadog 客户端 `datadog.ts`

Datadog 作为第二个分析后端，使用**白名单机制**控制哪些事件可以发送：

```typescript
// src/services/analytics/datadog.ts, 第 18-63 行
const DATADOG_ALLOWED_EVENTS = new Set([
  'tengu_api_error',                // API 错误
  'tengu_api_success',              // API 成功
  'tengu_init',                     // 初始化
  'tengu_exit',                     // 退出
  'tengu_cancel',                   // 取消操作
  'tengu_compact_failed',           // 上下文压缩失败
  'tengu_oauth_error',              // OAuth 错误
  'tengu_oauth_success',            // OAuth 成功
  'tengu_tool_use_error',           // 工具使用错误
  'tengu_tool_use_success',         // 工具使用成功
  'tengu_uncaught_exception',       // 未捕获异常
  'tengu_unhandled_rejection',      // 未处理 Promise 拒绝
  // ... 更多白名单事件（约 40 个）
])
```

白名单设计确保只有运维监控必需的事件到达 Datadog，避免高频事件（如每个流式 chunk）淹没日志系统。Datadog 客户端还使用 15 秒的批量刷新间隔和最大 100 条的批量大小来控制网络开销。

### 3.8 分析系统禁用条件

```typescript
// src/services/analytics/config.ts, 第 19-27 行
export function isAnalyticsDisabled(): boolean {
  return (
    process.env.NODE_ENV === 'test' ||           // 测试环境
    isEnvTruthy(process.env.CLAUDE_CODE_USE_BEDROCK) ||  // AWS Bedrock
    isEnvTruthy(process.env.CLAUDE_CODE_USE_VERTEX) ||   // GCP Vertex
    isEnvTruthy(process.env.CLAUDE_CODE_USE_FOUNDRY) ||  // Foundry
    isTelemetryDisabled()                        // 用户选择退出遥测
  )
}
```

第三方云提供商（Bedrock/Vertex/Foundry）的用户数据不会发送到 Anthropic 的分析后端——这既是隐私保护，也是合规需求。但注意 `isFeedbackSurveyDisabled()` 并**不**阻止第三方提供商用户看到反馈调查弹窗，因为调查是本地 UI 提示，企业客户通过 OpenTelemetry 自行捕获响应。

---

## 设计哲学分析

### 安全优先设计

整个服务层贯穿着"默认安全"的设计理念：

1. **编译时日志安全**：`AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS` 类型强制开发者在每个日志点显式确认数据安全性，从类型系统层面防止敏感数据泄露。
2. **PII 路由隔离**：`_PROTO_*` 前缀键通过 `stripProtoFields()` 在 Datadog 路径上被自动移除，只有 1P 特权后端能看到 PII 标记数据。
3. **PKCE 无密钥认证**：CLI 工具无法安全存储 client_secret，PKCE 用一次性密码学证明替代长期密钥。
4. **CSRF 防护**：OAuth 回调验证 `state` 参数，防止跨站请求伪造。

### 优雅降级

每个服务层组件都设计了明确的降级路径：

- **分析系统**：sink 注入前的事件排队 → sink 故障时的远程熔断 → 采样率动态调整 → 事件本地磁盘缓存
- **API 重试**：快速模式降级 → 模型降级 → 指数退避 → 持久重试模式
- **Bootstrap**：隐私模式跳过 → 第三方提供商跳过 → 无认证跳过 → 缓存未变跳过写入 → 拉取失败静默降级
- **GrowthBook**：环境变量覆盖 → 配置界面覆盖 → 内存缓存 → 磁盘缓存 → 默认值

### 可组合性

服务层的模块化设计允许灵活组合：

- `withRetry` 是独立的异步生成器，可以包装任意 API 操作
- `AnalyticsSink` 接口允许插入任意后端实现
- Feature Flag 的四层覆盖机制允许在不同场景下精确控制行为
- OAuth 服务的双流程（自动+手动）设计适应不同终端环境

### 防御性编程

- **529 双重检测**：状态码 + 消息内容匹配，防御 SDK 状态码丢失的边缘情况
- **Token 使用量 > 0 守卫**：防止流式增量更新用零值覆盖真实统计
- **Token 刷新三级回退链**：防止竞态条件导致订阅类型永久丢失
- **采样率有效性验证**：非数字或超范围的采样率回退到 100%
- **sink 熔断名称混淆**：`tengu_frond_boric` 防止轻易猜测和误操作

---

## 第四章：插件系统 `src/services/plugins/` + `src/plugins/`

### 4.1 插件架构设计

Claude Code 的插件系统是一个多层架构，允许第三方开发者通过标准化接口扩展 CLI 的功能。插件可以提供技能（skills）、钩子（hooks）、MCP 服务器和 LSP 服务器等多种组件，形成一个完整的可扩展生态系统。

核心目录结构：

```
src/services/plugins/
├── pluginOperations.ts          # 核心安装/卸载/启用/禁用逻辑（35KB）
├── pluginCliCommands.ts         # CLI 命令封装（11KB）
└── PluginInstallationManager.ts # 后台安装管理（5.9KB）

src/plugins/
├── builtinPlugins.ts            # 内置插件注册表（4.9KB）
└── bundled/
    └── index.ts                 # 内置插件初始化脚手架

src/utils/plugins/
├── pluginLoader.ts              # 双层加载器（缓存+网络）
├── installedPluginsManager.ts   # V2 元数据管理
├── cacheUtils.ts                # 版本化缓存与孤儿清理
├── reconciler.ts                # 市场协调
└── pluginDirectories.ts         # 目录管理
```

### 4.2 `registerBuiltinPlugin()` 模式

内置插件（Built-in Plugins）是 Claude Code 自带的、用户可通过 `/plugin` UI 切换启用/禁用的插件。它们使用 `{name}@builtin` 格式的 ID 以区分于市场插件。

```typescript
// src/plugins/builtinPlugins.ts, 第 21-32 行
const BUILTIN_PLUGINS: Map<string, BuiltinPluginDefinition> = new Map()

export const BUILTIN_MARKETPLACE_NAME = 'builtin'

// 注册一个内置插件到全局 Map
// 在 initBuiltinPlugins() 启动时调用
export function registerBuiltinPlugin(
  definition: BuiltinPluginDefinition,  // 插件定义对象
): void {
  BUILTIN_PLUGINS.set(definition.name, definition)  // 以名称为键存入注册表
}
```

内置插件的类型定义包含丰富的元数据字段：

```typescript
// src/skills/bundledSkills.ts, 第 15-41 行
export type BundledSkillDefinition = {
  name: string                  // 插件名称
  description: string           // 描述（显示在 UI 中）
  aliases?: string[]            // 别名（可选的替代调用名）
  whenToUse?: string            // 何时使用的提示（给模型的指导）
  allowedTools?: string[]       // 允许使用的工具列表
  model?: string                // 模型覆盖（如 'haiku', 'sonnet'）
  isEnabled?: () => boolean     // 动态启用检查
  hooks?: HooksSettings         // 事件钩子配置
  context?: 'inline' | 'fork'  // 执行模式：内联或分叉
  files?: Record<string, string> // 需要提取到磁盘的引用文件
  getPromptForCommand: (        // 生成提示词的函数
    args: string,
    context: ToolUseContext,
  ) => Promise<ContentBlockParam[]>
}
```

### 4.3 插件发现与双层加载

插件来自三个来源（按优先级）：

1. **市场插件**：从 `installed_plugins.json` + `~/.claude/plugins/cache/` 加载
2. **会话插件**：通过 `--plugin-dir` 命令行参数内联指定
3. **内置插件**：从 `BUILTIN_PLUGINS` 注册表加载

加载器采用**双层设计**以优化启动性能：

| 加载层 | 函数 | 网络访问 | 使用场景 |
|--------|------|---------|---------|
| 缓存层 | `loadAllPluginsCacheOnly()` | 否 | 交互式启动（快速） |
| 完整层 | `loadAllPlugins()` | 是 | 后台刷新、首次安装 |

### 4.4 `/plugin install` 安装流程

插件安装是一个多步骤的异步流程：

```typescript
// src/services/plugins/pluginOperations.ts, installPluginOp() 第 321-418 行
// 简化的安装流程：
async function installPluginOp(identifier: string) {
  // 1. 解析插件标识符（名称@市场）
  const parsed = parsePluginIdentifier(identifier)

  // 2. 搜索已知市场，解析插件元数据
  const resolved = await resolveFromMarketplace(parsed)

  // 3. 调用 installResolvedPlugin()：
  //    a. 验证组织策略（是否允许此插件）
  //    b. 解析依赖关系
  //    c. 缓存插件到: ~/.claude/plugins/cache/{marketplace}/{plugin}/{version}/
  //    d. 更新 V2 元数据到 installed_plugins.json
  //    e. 在 settings 中设置 enabledPlugins[pluginId] = true
}
```

### 4.5 版本管理与缓存

插件使用版本化缓存结构，每个版本独立存储：

```
~/.claude/plugins/
├── known_marketplaces.json          # 已知市场列表
├── installed_plugins.json           # V2 元数据（追踪安装状态）
├── marketplaces/{name}/             # 市场定义
│   └── marketplace.json
├── cache/{marketplace}/{plugin}/{version}/  # 版本化缓存
├── data/{sanitized-plugin-id}/      # 持久数据（跨版本保留）
└── npm-cache/node_modules/          # npm 依赖缓存
```

孤儿版本清理机制（`src/utils/plugins/cacheUtils.ts`）：
- 卸载或更新时标记旧版本为孤儿：`markPluginVersionOrphaned()`
- 后台清理（7 天宽限期）：`cleanupOrphanedPluginVersionsInBackground()`
- ZIP 缓存模式下跳过清理

### 4.6 ManagePlugins.tsx 管理界面

`src/commands/plugin/ManagePlugins.tsx`（2214 行，314KB）提供了完整的插件管理 UI，支持多种视图状态：主列表、插件详情、配置、卸载/数据清理、标记插件、MCP 详情等。用户通过 `/plugin` 命令进入此界面，可以浏览、安装、启用/禁用和卸载插件。

---

## 第五章：技能系统 `src/skills/`

### 5.1 技能定义格式

技能（Skills）是 Claude Code 中最灵活的扩展机制。每个技能是一个包含 YAML 前置元数据的 Markdown 文件（`SKILL.md`），定义了技能的行为、触发条件和执行模式。

```yaml
# SKILL.md 示例
---
description: 审查和整理自动记忆条目      # 必填：技能描述
name: Memory Review                       # 可选：显示名称
when-to-use: 当用户想要审查/整理记忆时    # 可选：何时触发
model: opus                               # 可选：模型覆盖
user-invocable: true                      # 是否可被用户通过 /name 调用
context: inline                           # 执行模式：inline 或 fork
allowed-tools:                            # 允许的工具
  - Read
  - Edit
paths:                                    # 条件激活：匹配的文件路径
  - "CLAUDE*.md"
---
# 技能的提示词内容...
```

支持的前置元数据字段完整列表：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `description` | string | （必填） | 技能描述，显示在选择器中 |
| `name` | string | （可选） | 覆盖默认名称 |
| `when-to-use` | string | （可选） | 指导模型何时调用此技能 |
| `model` | string | （可选） | 使用的模型 |
| `context` | `'inline'` \| `'fork'` | `'inline'` | 执行模式 |
| `user-invocable` | boolean | `true` | 用户可否通过斜杠命令调用 |
| `allowed-tools` | string[] | `[]` | 技能可使用的工具列表 |
| `paths` | string \| string[] | （可选） | 条件激活的文件 glob 模式 |
| `hooks` | object | （可选） | 事件处理器 |
| `effort` | string | （可选） | 努力程度 |
| `shell` | string | `'bash'` | 内联 shell 类型 |

### 5.2 技能发现机制

Claude Code 从**六个来源**发现技能，形成一个层次化的发现体系：

```
┌─────────────────────────────────────────────────┐
│ 1. 受管技能 (Managed)                             │
│    <managed-path>/.claude/skills/                │  ← 组织策略控制
├─────────────────────────────────────────────────┤
│ 2. 用户技能                                       │
│    ~/.claude/skills/skill-name/SKILL.md          │  ← 跨项目共享
├─────────────────────────────────────────────────┤
│ 3. 项目技能                                       │
│    .claude/skills/skill-name/SKILL.md            │  ← 项目专属
│    (向上搜索到 Home 目录)                          │
├─────────────────────────────────────────────────┤
│ 4. 附加目录 (--add-dir)                           │
│    <dir>/.claude/skills/                         │  ← 命令行指定
├─────────────────────────────────────────────────┤
│ 5. 动态发现                                       │
│    文件操作时发现的 .claude/skills/ 目录            │  ← 运行时发现
├─────────────────────────────────────────────────┤
│ 6. 旧版 /commands/（已弃用）                       │
│    ~/.claude/commands/ 和 .claude/commands/       │  ← 向后兼容
└─────────────────────────────────────────────────┘
```

### 5.3 `loadSkillsDir.ts` 加载逻辑

`src/skills/loadSkillsDir.ts`（1087 行）是技能加载的核心，包含复杂的缓存、去重和动态发现机制。

**核心加载函数** `getSkillDirCommands()`：

```typescript
// src/skills/loadSkillsDir.ts, 第 638-675 行
export const getSkillDirCommands = memoize(
  async (cwd: string): Promise<Command[]> => {
    const userSkillsDir = join(getClaudeConfigHomeDir(), 'skills')
    const managedSkillsDir = join(getManagedFilePath(), '.claude', 'skills')
    const projectSkillsDirs = getProjectDirsUpToHome('skills', cwd)

    // --bare 模式：跳过自动发现，只加载 --add-dir 路径
    if (isBareMode()) {
      // ... 简化加载逻辑
      return []
    }

    // 从所有来源并行加载技能
    const [managedSkills, userSkills, projectSkills, ...additionalSkills] =
      await Promise.all([
        loadSkillsFromSkillsDir(managedSkillsDir, 'managed'),
        loadSkillsFromSkillsDir(userSkillsDir, 'user'),
        // ... 项目和附加目录
      ])

    // 通过 realpath() 去重（处理符号链接）
    // 分离条件技能（有 paths 字段）和无条件技能
    return unconditionalSkills.map(s => s.skill)
  }
)
```

**动态发现**——当用户读写文件时，系统自动在文件路径的父目录中发现新技能：

```typescript
// src/skills/loadSkillsDir.ts, 第 861-915 行
export async function discoverSkillDirsForPaths(
  filePaths: string[],     // 触发发现的文件路径列表
  cwd: string,
): Promise<string[]> {
  // 从文件位置向上遍历到 cwd（不包含 cwd 本身）
  // 在每一层查找 .claude/skills/ 目录
  // 检查 gitignore 状态（阻止 node_modules/.claude/skills/）
  // 返回新发现的目录（按最深优先排序）
}
```

**条件激活**——具有 `paths` 前置元数据的技能只在匹配文件被访问时激活：

```typescript
// src/skills/loadSkillsDir.ts, 第 997-1058 行
export function activateConditionalSkillsForPaths(
  filePaths: string[],
  cwd: string,
): string[] {
  // 使用 ignore 库（gitignore 风格匹配）检查 paths 模式
  // 将匹配的技能从 conditionalSkills → dynamicSkills
  // 发出 skillsLoaded 信号 → 清除缓存
}
```

### 5.4 SkillTool 执行流程

`src/tools/SkillTool/SkillTool.ts`（1200+ 行）实现了技能的执行逻辑，支持三种执行路径：

**A. 内联执行（`context: 'inline'`，默认）：**

内联技能直接将提示词文本展开到当前对话中，模型看到完整的技能内容：

1. `validateInput()` → 检查技能存在且未禁用
2. `checkPermissions()` → 权限对话/规则
3. `call()` → 调用 `processPromptSlashCommand()`，将技能内容内联到对话

**B. 分叉执行（`context: 'fork'`）：**

分叉技能创建独立的子代理来执行，拥有自己的 token 预算：

1. 检测到 `context === 'fork'`
2. 调用 `executeForkedSkill()`
3. 创建子代理，运行 `runAgent()` 并传入修改后的 `getAppState()`
4. 收集子代理的所有消息
5. 返回 `{ success, commandName, status: 'forked', agentId, result }`

**C. 远程执行（实验性 `EXPERIMENTAL_SKILL_SEARCH`）：**

仅限 Anthropic 内部用户，支持从远程 URL 加载技能：

```typescript
// src/tools/SkillTool/SkillTool.ts, 第 108-115 行
const remoteSkillModules = feature('EXPERIMENTAL_SKILL_SEARCH')
  ? {
      // 条件 require（非静态 import），防止树摇动问题
      ...(require('../../services/skillSearch/remoteSkillState.js')),
      ...(require('../../services/skillSearch/remoteSkillLoader.js')),
      ...(require('../../services/skillSearch/telemetry.js')),
      ...(require('../../services/skillSearch/featureCheck.js')),
    }
  : null  // Feature Flag 关闭时为 null，死代码消除
```

### 5.5 内置技能注册

`src/skills/bundled/index.ts` 在启动时注册所有内置技能：

```typescript
// src/skills/bundled/index.ts, 第 24-79 行
export function initBundledSkills(): void {
  // 始终注册的技能
  registerUpdateConfigSkill()    // 配置管理
  registerKeybindingsSkill()     // 快捷键帮助
  registerVerifySkill()          // 代码验证
  registerDebugSkill()           // 调试辅助
  registerSimplifySkill()        // 代码简化
  registerBatchSkill()           // 批量操作
  registerStuckSkill()           // 卡住时求助
  // ...

  // Feature Flag 门控的技能——编译时死代码消除
  if (feature('KAIROS') || feature('KAIROS_DREAM')) {
    const { registerDreamSkill } = require('./dream.js')
    registerDreamSkill()
  }
  if (feature('AGENT_TRIGGERS')) {
    const { registerLoopSkill } = require('./loop.js')
    registerLoopSkill()  // isEnabled 在运行时决定可见性
  }
  if (feature('BUILDING_CLAUDE_APPS')) {
    const { registerClaudeApiSkill } = require('./claudeApi.js')
    registerClaudeApiSkill()
  }
  // ...
}
```

### 5.6 嵌套技能触发

技能可以在执行过程中触发其他技能，通过 `queryDepth` 追踪嵌套深度：

```typescript
// src/tools/SkillTool/SkillTool.ts, 第 162-164 行
invocation_trigger: (queryDepth > 0 ? 'nested-skill' : 'claude-proactive')
// queryDepth = 0：用户直接调用 /foo
// queryDepth > 0：模型在另一个技能/代理中调用了此技能
```

---

## 第六章：上下文压缩服务 `src/services/compact/`

### 6.1 上下文压缩的整体架构

上下文窗口是 Claude Code 最稀缺的资源。当对话消息累积到接近模型上下文窗口极限时，系统必须自动压缩历史消息以释放空间。`src/services/compact/` 目录实现了一个多层压缩系统：

```
src/services/compact/
├── autoCompact.ts           # 自动触发逻辑与阈值计算（352 行）
├── compact.ts               # 主编排：摘要生成与文件恢复（1700+ 行）
├── grouping.ts              # 按 API 轮次分组消息（64 行）
├── microCompact.ts          # 缓存感知的工具结果删除（400+ 行）
├── postCompactCleanup.ts    # 压缩后状态清理（78 行）
├── prompt.ts                # 摘要生成提示词（375 行）
├── sessionMemoryCompact.ts  # 基于会话记忆的压缩（500+ 行）
├── snipCompact.ts           # 基于边界的裁剪压缩（Feature Flag 门控）
└── compactWarningState.ts   # 警告抑制追踪
```

压缩的五个层次按照从轻量到重量的顺序执行：

```
┌─────────────────────────────────────────────────┐
│ 1. Snip（裁剪）        HISTORY_SNIP Feature Flag  │
│    基于边界标记的消息移除                           │
├─────────────────────────────────────────────────┤
│ 2. Microcompact（微压缩）  缓存感知               │
│    删除工具结果，不破坏缓存前缀                     │
├─────────────────────────────────────────────────┤
│ 3. Session Memory（会话记忆）  替代路径            │
│    轻量级基于记忆的摘要                             │
├─────────────────────────────────────────────────┤
│ 4. Autocompact（自动压缩）   主路径               │
│    完整的文本摘要，替换历史消息                      │
├─────────────────────────────────────────────────┤
│ 5. Post-Compact Restoration（压缩后恢复）          │
│    重新注入最近文件 + 计划 + 技能                   │
└─────────────────────────────────────────────────┘
```

### 6.2 自动压缩触发条件

`autoCompact.ts` 定义了精确的触发阈值：

```typescript
// src/services/compact/autoCompact.ts, 第 62-91 行
export const AUTOCOMPACT_BUFFER_TOKENS = 13_000     // 自动压缩缓冲区
export const WARNING_THRESHOLD_BUFFER_TOKENS = 20_000 // 警告阈值缓冲区
export const ERROR_THRESHOLD_BUFFER_TOKENS = 20_000   // 错误阈值缓冲区

// 计算自动压缩触发阈值
export function getAutoCompactThreshold(model: string): number {
  // 有效上下文窗口 = 模型窗口 - 20K（输出预留）
  const effectiveContextWindow = getEffectiveContextWindowSize(model)
  // 自动压缩阈值 = 有效窗口 - 13K 缓冲
  // 对于 200K 窗口：阈值 ≈ 200K - 20K - 13K = 167K（约占全窗口的 83%）
  const autocompactThreshold =
    effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS

  // 支持通过环境变量覆盖（用于测试）
  const envPercent = process.env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
  if (envPercent) {
    const parsed = parseFloat(envPercent)
    if (!isNaN(parsed) && parsed > 0 && parsed <= 100) {
      return Math.min(
        Math.floor(effectiveContextWindow * (parsed / 100)),
        autocompactThreshold,
      )
    }
  }
  return autocompactThreshold
}
```

**断路器**——连续失败 3 次后停止重试，防止浪费 API 调用：

```typescript
// src/services/compact/autoCompact.ts, 第 67-70 行
// BQ 2026-03-10: 1,279 个会话有 50+ 次连续失败（最多 3,272 次），
// 每天全球浪费约 250K API 调用
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
```

### 6.3 消息分组策略 `groupMessagesByApiRound()`

压缩需要在安全的边界点裁剪消息。`grouping.ts` 按 API 轮次（round-trip）将消息分组：

```typescript
// src/services/compact/grouping.ts, 第 22-63 行
export function groupMessagesByApiRound(messages: Message[]): Message[][] {
  const groups: Message[][] = []
  let current: Message[] = []
  // 唯一的边界门控：最近看到的 assistant message.id
  // 流式分块共享同一个 id，所以边界只在新一轮真正开始时触发
  let lastAssistantId: string | undefined

  for (const msg of messages) {
    if (
      msg.type === 'assistant' &&
      msg.message.id !== lastAssistantId &&  // 新的 assistant 轮次
      current.length > 0
    ) {
      groups.push(current)   // 前一组结束
      current = [msg]        // 新组开始
    } else {
      current.push(msg)
    }
    if (msg.type === 'assistant') {
      lastAssistantId = msg.message.id
    }
  }
  if (current.length > 0) {
    groups.push(current)
  }
  return groups
}
```

这个函数从旧的"人类轮次分组"升级为更细粒度的"API 轮次分组"，使得在单提示的长代理会话（SDK/CCR/评估场景）中也能进行反应式压缩。

### 6.4 摘要生成策略

`prompt.ts` 定义了三种摘要提示词模板：

| 模板 | 用途 | 行号 |
|------|------|------|
| `BASE_COMPACT_PROMPT` | 完整对话摘要 | 61-143 |
| `PARTIAL_COMPACT_PROMPT` | 只摘要最近部分 | 145-204 |
| `PARTIAL_COMPACT_UP_TO_PROMPT` | 前缀保留摘要 | 208-267 |

摘要包含 9 个标准章节：主要请求与意图、关键技术概念、文件与代码段、错误与修复、问题解决、所有用户消息、待处理任务、当前工作、可选下一步。

**分析草稿纸模式**——摘要使用 `<analysis>` 标签让模型先推理再总结，推理过程在格式化时被移除：

```typescript
// src/services/compact/prompt.ts, 第 311-335 行
function formatCompactSummary(text: string): string {
  // 移除 <analysis>...</analysis> 标签（仅用于提升摘要质量）
  // 将 <summary> 替换为格式化标题
}
```

### 6.5 压缩后文件恢复

压缩后，系统会重新注入最近访问的文件以恢复关键上下文：

```typescript
// src/services/compact/compact.ts, 第 122-130 行
export const POST_COMPACT_MAX_FILES_TO_RESTORE = 5     // 最多恢复 5 个文件
export const POST_COMPACT_TOKEN_BUDGET = 50_000         // 总 token 预算 50K
export const POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000   // 每文件最多 5K token
export const POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000  // 每技能最多 5K token
export const POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000   // 技能总预算 25K

// src/services/compact/compact.ts, 第 1415-1464 行
export async function createPostCompactFileAttachments(
  readFileState: Record<string, { content: string; timestamp: number }>,
  toolUseContext: ToolUseContext,
  maxFiles: number,
  preservedMessages: Message[] = [],
): Promise<AttachmentMessage[]> {
  // 1. 收集保留消息中已有的文件路径（避免重复）
  const preservedReadPaths = collectReadToolFilePaths(preservedMessages)

  // 2. 过滤、按时间戳排序、取最近 N 个
  const recentFiles = Object.entries(readFileState)
    .filter(file =>
      !shouldExcludeFromPostCompactRestore(file.filename, ...) &&
      !preservedReadPaths.has(expandPath(file.filename)),
    )
    .sort((a, b) => b.timestamp - a.timestamp)  // 最近的优先
    .slice(0, maxFiles)                           // 最多 5 个

  // 3. 并行重新读取文件（获取最新内容）
  const results = await Promise.all(
    recentFiles.map(file => generateFileAttachment(file.filename, {
      ...toolUseContext,
      fileReadingLimits: { maxTokens: POST_COMPACT_MAX_TOKENS_PER_FILE },
    }))
  )

  // 4. 在 50K token 预算内过滤
  let usedTokens = 0
  return results.filter(result => {
    const tokens = roughTokenCountEstimation(jsonStringify(result))
    if (usedTokens + tokens <= POST_COMPACT_TOKEN_BUDGET) {
      usedTokens += tokens
      return true
    }
    return false
  })
}
```

### 6.6 `snipCompact.ts`：基于边界的裁剪压缩

`snipCompact.ts` 是一种轻量级的压缩替代方案，通过 `HISTORY_SNIP` Feature Flag 门控：

- 在 API 发送的消息中注入 `[id:...]` 边界标记
- 裁剪旧的完整消息轮次，而非生成摘要
- 返回 `{ messages, tokensFreed, boundaryMessage }`
- `tokensFreed` 传递给 autocompact 以提高其阈值
- UI 保留被裁剪的消息用于回滚显示（`includeSnipped: true`）

### 6.7 与查询引擎的集成

压缩系统通过依赖注入集成到查询循环中（`src/query.ts`）：

```
查询循环中的压缩流程：
┌──────────────────────┐
│ 1. Snip 阶段          │  ← HISTORY_SNIP Feature Flag
│    snipCompactIfNeeded │
├──────────────────────┤
│ 2. Microcompact 阶段  │  ← 缓存感知工具结果删除
│    microcompactMessages│
├──────────────────────┤
│ 3. Context Collapse   │  ← CONTEXT_COLLAPSE Feature Flag
│    applyCollapsesIfNeeded│
├──────────────────────┤
│ 4. Autocompact 阶段   │  ← 主压缩路径
│    autoCompactIfNeeded │
│    (传入 snipTokensFreed)│
└──────────────────────┘
```

---

## 第七章：企业管理

### 7.1 远程受管设置 `src/services/remoteManagedSettings/`

企业客户需要在组织层面统一控制 Claude Code 的行为。远程受管设置服务通过 API 拉取、缓存和验证管理员配置的设置。

```typescript
// src/services/remoteManagedSettings/index.ts, 第 1-13 行
// 资格规则：
// - Console 用户（API 密钥）：全部有资格
// - OAuth 用户（Claude.ai）：仅 Enterprise/C4E 和 Team 订阅者
// - API 失败时开放（非阻塞）——拉取失败则继续运行
// - 对没有受管设置的用户返回空设置
```

关键常量和轮询策略：

| 常量 | 值 | 说明 |
|------|---|------|
| `SETTINGS_TIMEOUT_MS` | 10,000 ms | 拉取超时 |
| `DEFAULT_MAX_RETRIES` | 5 | 最大重试次数 |
| `POLLING_INTERVAL_MS` | 3,600,000 ms | 后台轮询间隔（1 小时） |

设置拉取使用 **ETag/校验和缓存** 减少网络流量：服务端返回 SHA256 校验和，客户端在后续请求中作为 `If-None-Match` 头发送，服务端返回 304 Not Modified 时跳过更新。

**安全检查流程**（`securityCheck.tsx`）：当危险设置变更时，显示阻塞对话框要求用户确认；非交互模式下跳过对话框。

### 7.2 策略限制 `src/services/policyLimits/`

策略限制服务从组织后端拉取功能限制，允许管理员在组织层面禁用特定 CLI 功能：

```typescript
// src/services/policyLimits/index.ts
// 核心函数：
isPolicyAllowed(policyName)     // 同步检查：策略是否允许
waitForPolicyLimitsToLoad()     // 在功能访问前等待加载完成
loadPolicyLimits()              // CLI 启动钩子
refreshPolicyLimits()           // 认证状态变更时刷新
```

**安全流量模式的特殊处理**：当缓存不可用且处于"安全流量"模式时，`ESSENTIAL_TRAFFIC_DENY_ON_MISS` 列表中的策略默认为拒绝（如 `allow_product_feedback`），确保在降级场景下仍能执行关键安全策略。

### 7.3 MDM（移动设备管理）设置 `src/utils/settings/mdm/`

MDM 设置允许企业 IT 部门通过操作系统级别的配置管理机制控制 Claude Code：

| 平台 | 管理员源 | 用户源 | 工具 |
|------|---------|--------|------|
| **macOS** | `/Library/Managed Preferences/{user}/com.anthropic.claudecode.plist` | `~/Library/Preferences/*.plist`（仅内部） | `plutil` |
| **Windows** | `HKLM\SOFTWARE\Policies\ClaudeCode` | `HKCU\SOFTWARE\Policies\ClaudeCode` | `reg query` |
| **Linux** | `/etc/claude-code/managed-settings.json` | — | 文件读取 |

**设置优先级**（第一个来源获胜）：

```
1. 远程设置（remoteManagedSettings API）
2. HKLM/plist（OS 管理员级别）
3. 基于文件的受管设置（/etc/claude-code/）
4. HKCU（Windows 用户级别，最低优先级）
```

MDM 原始读取在 `main.tsx` 模块评估阶段就已启动（`startMdmRawRead()`），通过并行子进程读取系统配置，确保启动时不增加额外延迟。

---

## 第八章：安全存储 `src/utils/secureStorage/`

### 8.1 平台选择策略

`src/utils/secureStorage/index.ts` 根据运行平台选择合适的凭据存储后端：

```typescript
// src/utils/secureStorage/index.ts, 第 9-17 行
export function getSecureStorage(): SecureStorage {
  if (process.platform === 'darwin') {
    // macOS：主存储为 Keychain，回退到明文
    return createFallbackStorage(macOsKeychainStorage, plainTextStorage)
  }
  // Linux/Windows：TODO 支持 libsecret/Credential Manager
  return plainTextStorage
}
```

### 8.2 macOS Keychain 集成

`macOsKeychainStorage.ts` 通过 macOS `security` 命令行工具与系统 Keychain 交互：

**读取**使用 30 秒 TTL 缓存 + "过期时返回旧值"模式：

```
security find-generic-password -a "{username}" -w -s "Claude Code-credentials"
```

**写入**使用十六进制编码防止 shell 转义问题（INC-3028），并优先使用 stdin 隐藏载荷：

```typescript
// src/utils/secureStorage/macOsKeychainStorage.ts, 第 97-158 行
update(data: SecureStorageData): { success: boolean; warning?: string } {
  clearKeychainCache()  // 写入前清除缓存

  const hexValue = Buffer.from(jsonStringify(data), 'utf-8').toString('hex')

  // 优先使用 stdin（隐藏载荷，防止 CrowdStrike 等进程监控器看到）
  // 当载荷超过 4032 字节时回退到 argv
  const command = `add-generic-password -U -a "${username}" ...`

  if (command.length <= SECURITY_STDIN_LINE_LIMIT) {
    // 通过 stdin 传入（安全）
    result = execaSync('security', ['-i'], { input: command })
  } else {
    // 超长载荷通过 argv 传入（十六进制编码仍能阻止明文 grep）
    result = execaSync('security', ['add-generic-password', '-U', ...])
  }
}
```

### 8.3 回退存储模式

`fallbackStorage.ts` 实现了优雅的主/备切换逻辑，处理了从明文迁移到 Keychain 的场景：

```typescript
// src/utils/secureStorage/fallbackStorage.ts, 第 7-70 行
export function createFallbackStorage(
  primary: SecureStorage,     // macOS Keychain
  secondary: SecureStorage,   // 明文文件
): SecureStorage {
  return {
    update(data) {
      const primaryDataBefore = primary.read()
      const result = primary.update(data)

      if (result.success) {
        // 首次成功写入主存储时，删除备存储（迁移完成）
        if (primaryDataBefore === null) {
          secondary.delete()
        }
        return result
      }
      // 主存储失败 → 回退到备存储
      const fallbackResult = secondary.update(data)
      if (fallbackResult.success && primaryDataBefore !== null) {
        // 删除主存储中的过期数据，防止它"遮蔽"备存储的新数据
        // （避免 token 刷新循环 #30337）
        primary.delete()
      }
      return fallbackResult
    },
    // ...
  }
}
```

### 8.4 启动预取优化

`keychainPrefetch.ts` 在 `main.tsx` 模块评估阶段并行启动两个 Keychain 读取：

```
                时间轴
                ────────────────────────────────>
main.tsx:      |← 模块加载（~135ms）→|
prefetch:      |← security 命令 #1 (~32ms) →|
               |← security 命令 #2 (~33ms) →|
               合计额外耗时: ~0ms（完全重叠）
```

- OAuth 凭据（`-credentials` 后缀）和旧版 API 密钥并行预取
- 无预取时顺序执行约需 65ms 额外启动时间
- 通过预取完全隐藏在模块加载时间中

---

## 设计哲学分析

### 无需修改的可扩展性（Extensibility Without Modification）

插件系统的 `registerBuiltinPlugin()` 模式是**开闭原则**（Open-Closed Principle）的教科书实现。新插件的添加不需要修改 Claude Code 核心代码——只需创建一个新文件，在 `initBuiltinPlugins()` 中添加一行注册调用，全局 `BUILTIN_PLUGINS` Map 即可自动收录。更深层次地，外部插件通过市场发现和缓存机制实现了完全解耦：第三方开发者可以发布插件到市场，用户通过 `/plugin install` 安装，整个流程中 Claude Code 核心代码没有任何变化。

这种模式的三层设计——内置注册、市场发现、动态加载——形成了从编译时到运行时的完整扩展谱系。编译时注册保证了核心功能的可靠性和性能（零运行时查找开销）；市场机制提供了标准化的分发渠道（版本管理、依赖解析、策略验证）；运行时加载允许用户在不重启的情况下扩展功能。三层相互独立又相互补充，任何一层的扩展不会影响其他层的稳定性。

### 可组合性（Composability）

技能系统的**发现 + 绑定**架构展示了精妙的可组合性。技能可以来自六个不同来源（受管、用户、项目、附加目录、动态发现、旧版命令），通过统一的 YAML 前置元数据格式和 `Command` 类型接口组合在一起。条件激活（`paths` 字段）使技能可以根据上下文自动组合——当用户编辑 `CLAUDE.md` 文件时，与 CLAUDE.md 相关的技能自动激活，无需用户显式选择。

更重要的是，内联与分叉两种执行模式的设计让技能可以在**深度**上组合：内联技能直接将提示词注入当前对话（零开销组合），分叉技能创建独立子代理（隔离组合）。嵌套技能触发（`queryDepth > 0`）使得技能可以在执行中调用其他技能，形成工作流。

### 上下文窗口经济学（Context Window Economics）

上下文压缩服务是"上下文窗口经济学"从理念到实现的完整转化。它不仅仅在上下文窗口满时被动清理，而是**主动管理**这一最稀缺的资源：

五层压缩策略（裁剪 → 微压缩 → 会话记忆 → 自动压缩 → 文件恢复）构成了一个精确的资源管理管线。轻量级操作优先执行（微压缩只删除工具结果，不触发 API 调用），重量级操作作为最后手段（完整摘要需要额外的 API 调用）。断路器（连续 3 次失败后停止）防止在不可恢复的情况下浪费资源。

最精妙的是 `POST_COMPACT_TOKEN_BUDGET`（50K）与 `POST_COMPACT_MAX_FILES_TO_RESTORE`（5）的设计——压缩不是简单地丢弃所有历史，而是智能地保留最可能需要的上下文（最近编辑的文件），在释放空间和保留工作状态之间找到平衡。

### 优雅降级的智能恢复（Graceful Degradation）

压缩后文件恢复机制展示了**智能降级**——不是简单地降低功能，而是在降级中保留最重要的信息。系统通过时间戳排序确保最近的文件优先恢复，通过 token 预算确保恢复不会立即重新触发压缩，通过排除已保留消息中的文件避免重复。

回退存储模式（`createFallbackStorage`）同样体现了这一原则：Keychain 不可用时自动回退到明文存储，首次 Keychain 写入成功时自动迁移旧数据，Keychain 写入失败时删除过期条目防止"遮蔽"问题。每一步降级都经过精心设计，确保部分功能优于完全失败。

### 安全优先设计的组织级延伸（Safety-First Design）

企业 MDM 集成将安全优先设计从个人层面扩展到组织层面。优先级顺序（远程设置 > HKLM/plist > 文件配置 > HKCU）确保**管理员策略总是覆盖用户偏好**——这不是限制用户自由，而是在企业环境中建立可预测的安全边界。

安全检查对话框（`securityCheck.tsx`）在危险设置变更时要求用户确认，体现了"人在回路"与"安全优先"的融合。策略限制的"安全流量模式"（`ESSENTIAL_TRAFFIC_DENY_ON_MISS`）则确保在网络不可用时仍能执行关键安全策略——这是"失败时安全"（fail-safe）的典型实现。

### 隔离与遏制（Isolation & Containment）

安全存储的平台抽象层将凭据处理从业务逻辑中完全隔离。`SecureStorage` 接口的 `read/update/delete` 三个方法构成了一个最小化的安全边界——业务代码永远不直接接触 Keychain 命令或文件系统凭据路径。

十六进制编码载荷的设计（INC-3028 后引入）进一步加固了这一隔离：即使进程监控器（如 CrowdStrike）捕获了命令行参数，也只能看到无法直接解读的十六进制字符串。优先使用 stdin 传输凭据（`security -i`）则将安全边界从进程参数推进到了进程内部通道。

### 渐进信任的规模化实现（Progressive Trust at Scale）

GrowthBook Feature Flag 在插件和技能系统中实现了**规模化的渐进信任**。Feature Flag 门控的技能（如 `KAIROS`、`AGENT_TRIGGERS`、`BUILDING_CLAUDE_APPS`）允许 Anthropic 逐步向用户子集推出新功能——先向内部用户（`USER_TYPE === 'ant'`），然后向特定百分比的外部用户，最后全量开放。这不是简单的开关，而是一个多维度的信任梯度：编译时消除（性能零开销）+ 运行时检查（`isEnabled` 回调）+ 远程配置（GrowthBook 动态调整）。`EXPERIMENTAL_SKILL_SEARCH` 的防护尤其严格——三重守卫（Feature Flag + 用户类型检查 + 条件 require）确保实验性功能即使在代码层面也不会泄露到外部构建中。

---

## 关键要点总结

1. **插件系统**通过 `registerBuiltinPlugin()` 模式和多源发现实现了完整的扩展生态，双层加载器（缓存+网络）优化了启动性能
2. **技能系统**是 Claude Code 最灵活的扩展机制，支持六个来源、三种执行模式（内联/分叉/远程）、条件激活和嵌套触发
3. **上下文压缩服务**实现了五层递进策略（裁剪→微压缩→会话记忆→自动压缩→文件恢复），是"上下文窗口经济学"的完整落地
4. **企业管理**将安全控制从个人扩展到组织，通过远程设置、策略限制和 MDM 三层机制实现
5. **安全存储**通过平台抽象和回退模式提供了安全且可靠的凭据管理
6. 十大设计哲学在服务层形成了密集的交叉网络——可扩展性支撑插件生态，可组合性驱动技能系统，上下文经济学引导压缩策略，安全优先贯穿企业管理，隔离保护凭据安全

---

## 下一篇预览

**Doc 14：工具子系统** 将深入 Claude Code 的"基础设施层"——Bash 命令解析器如何构建 AST 以理解命令安全性、Git 操作如何管理工作树和分支、配置系统如何合并多层优先级、用户输入如何被规范化处理、ANSI 渲染如何将终端输出转换为图片。这些看似零散的工具模块，实际上构成了支撑所有高层设计哲学的"免疫系统"。
