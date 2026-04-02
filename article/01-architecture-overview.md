# 第一篇：从 51 万行说起

我第一次打开 Claude Code 的源码目录时，`find . -name '*.ts' | wc -l` 返回了 1903。五十一万行 TypeScript。

说实话，看到这个数字我是想关掉的。但转念一想，VS Code 是 30 万行，webpack 是 10 万行，而 Claude Code 要同时做 LLM 交互、终端渲染、文件操作、代码搜索、多 Agent 协作、权限管理、插件系统。51 万行或许不是膨胀，而是真的有那么多事要做。

这篇文章建立全局视角。不深入任何一个子系统，但读完之后你会知道每个模块在哪、负责什么、跟谁通信。

## 技术栈：为什么是 Bun + React？

Claude Code 的运行时不是 Node.js，而是 Bun。最直接的原因是启动速度——一个 CLI 工具如果启动要两三秒，体验会很差。Bun 的冷启动大约是 Node.js 的 1/4，而且原生支持 TypeScript，不需要额外的编译步骤。

更令我意外的是 UI 层。一个终端工具用 React？但翻完 `src/screens/` 和 `src/components/` 之后我理解了。Claude Code 的终端界面不是简单的 `console.log`——它有实时更新的 spinner、多面板布局、工具执行进度条、代码 diff 高亮。这些用传统的 ANSI 转义序列来写是噩梦级别的工作量。React + Ink 框架让你用组件化的方式来写终端 UI，声明式渲染，状态管理直接用 React 的 useState/useReducer。

```
技术选型总结：
  Bun          → 快速启动 + 原生 TS
  React + Ink  → 声明式终端 UI
  TypeScript   → 51万行必须有类型系统
  Commander.js → CLI 参数解析
  Zod          → 运行时数据验证
  ripgrep      → 代码搜索（外部 Rust 二进制）
  GrowthBook   → 远程 Feature Flag
```

## 目录结构：四大层次

翻了半天代码之后，我觉得 Claude Code 的架构可以抽象成四层：

**第一层：入口与 UI**。`src/screens/` 是 React 页面，`src/components/` 是 UI 组件，`src/commands/` 是用户主动发起的斜杠命令（`/commit`、`/compact`）。这一层面向用户。

**第二层：引擎**。`src/QueryEngine.ts`（1295 行）管理会话状态，`src/query.ts`（1729 行）是核心 agentic loop。这一层是大脑。

**第三层：工具**。`src/tools/` 下面有几十个工具——BashTool、FileReadTool、FileEditTool、GrepTool、AgentTool 等等。每个工具是 LLM 能调用的一个能力。这一层是手臂。

**第四层：基础设施**。`src/services/` 管 API 调用和 MCP 协议，`src/utils/` 管权限、Feature Flag、模型配置。这一层是神经和骨骼。

```
src/
├── screens/           # React 页面 (REPL, Onboarding, ...)
├── components/        # UI 组件 (PermissionRequest, ToolUseView, ...)
├── commands/          # 斜杠命令 (/commit, /compact, /model, ...)
├── query.ts           # 核心 agentic loop (1729行)
├── QueryEngine.ts     # 会话管理器 (1295行)
├── tools/             # 工具系统 (30+ 个工具目录)
│   ├── BashTool/      # Shell 执行 (1143行)
│   ├── AgentTool/     # 子 Agent 生成 (1397行)
│   ├── FileEditTool/  # 文件编辑
│   └── ...
├── services/          # API 客户端、MCP、OAuth
├── utils/             # 权限、Feature Flag、模型配置
├── buddy/             # 宠物系统（未发布）
├── voice/             # 语音模式（未发布）
├── bridge/            # 远程控制模式（31个文件）
└── coordinator/       # 多 Agent 编排
```

## 十大设计哲学

读完整个代码库，有十个反复出现的设计主题。我在后续每篇文章中都会引用这些：

1. **状态外化**：几乎所有配置、权限、工具定义都通过参数注入，而非 import。方便测试和替换。
2. **渐进式复杂度**：简单的事保持简单（读个文件就几十行），复杂的事再加层（BashTool 的安全检查上千行）。
3. **Feature Flag 门控**：44 个编译时/运行时 flag 控制功能开关。没通过测试的功能编译时就被删掉了。
4. **类型即文档**：TypeScript 的类型定义就是最准确的接口文档。Zod schema 同时做验证和类型推导。
5. **上下文经济学**：token 就是钱。每个工具的输出都有截断策略，上下文有多层压缩。
6. **安全分层**：两阶段门控（输入验证 + 权限检查），五级权限模式，危险命令检测。
7. **优雅降级**：API 超时就重试，模型不支持的参数就去掉，子 Agent 挂了不影响主循环。
8. **声明式配置**：工具、命令、Agent 类型都用声明式的 schema 定义，运行时动态组装。
9. **流式一切**：从 API 响应到工具执行，能流式的都流式，用户永远在看到进展。
10. **实验性前进**：大量未发布功能在代码里，用 flag 保护，随时可以灰度放量。

## 带着地图去探险

有了这张全局地图，后面每篇文章就是选一个感兴趣的区域深入。

下一篇我们进入最核心的部分：`query.ts` 那个 1729 行的 `while(true)` 循环——AI Agent 真正的心跳。

---

> 本文是 [Claude Code 源码导读](00-index.md) 系列第 1 篇。配套实现：[NanoCoder](https://github.com/he-yufeng/NanoCoder)
