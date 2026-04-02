# 第六篇：当一个 Claude 不够用

Claude Code 的 AgentTool 有 1397 行，是除了 BashTool 之外最大的工具实现。它对应的目录 `src/tools/AgentTool/` 总共超过 6700 行代码。

为什么需要这么多代码来"启动另一个 Agent"？

## 为什么要多 Agent

单 Agent 模式有一个根本限制：上下文窗口是共享的。

假设你让 Claude Code 做一个复杂任务："重构 auth 模块的错误处理，然后给每个改动加测试，最后更新文档。" 在单 Agent 模式下，这三个子任务共享同一个 128K 的上下文窗口。auth 模块的文件内容、测试代码、文档内容——全部塞在一个窗口里。几轮工具调用后上下文就快满了。

更糟糕的是，后面写测试的时候，前面读过的 auth 模块代码可能已经被上下文压缩掉了。

多 Agent 的解法：主 Agent 把任务拆成三个子任务，每个子任务交给一个独立的子 Agent。每个子 Agent 有自己的上下文窗口——128K 全给它用。三个子 Agent 并行工作，完成后把结果汇报给主 Agent。

## AgentTool 的三种模式

源码里，AgentTool 不只是"创建一个新 Agent"那么简单。它有三种执行模式：

**1. 普通模式（Default）。** 创建一个新 Agent，在主进程的同一个工作目录下执行。子 Agent 共享文件系统但有独立的上下文。这是最常用的模式。

**2. Worktree 模式（Isolation: "worktree"）。** 创建一个 Git worktree，子 Agent 在隔离的目录下工作。改文件不影响主分支。适合并行修改同一组文件的场景——比如一个子 Agent 改前端，一个改后端，各自在自己的 worktree 里，不会冲突。

```typescript
// src/tools/AgentTool/AgentTool.tsx
// 高级参数（Feature Flag 门控）
isolation: z.enum(["worktree"]).optional()  // 在隔离的 git worktree 中运行
```

**3. 后台模式（Background）。** 子 Agent 在后台运行，主 Agent 不等待结果，继续处理其他事情。后台 Agent 完成后通过通知系统告知主 Agent。这个模式在 COORDINATOR_MODE 下使用。

## 子 Agent 的能力限制

一个重要的设计决策：子 Agent 不能再创建子 Agent。

```typescript
// src/tools/AgentTool/runAgent.ts 中，子 Agent 的工具列表
// 会过滤掉 AgentTool 本身，防止递归生成
const tools = parentTools.filter(t => t.name !== 'agent')
```

这不是技术上做不到（递归 Agent 在理论上可行），而是工程上的理性选择：
1. 无限递归的风险——一个 Agent 可以无限生成子 Agent，耗尽所有资源
2. 调试困难——三层嵌套的 Agent 之间传递的上下文很难追踪
3. 实际收益有限——两层（主 Agent + 子 Agent）已经能处理绝大多数场景

NanoCoder 也做了同样的设计：子 Agent 的工具列表中排除了 `agent` 工具。

## 子 Agent 的上下文构建

子 Agent 不是从零开始的。它的系统提示词包含：

1. 主 Agent 给它的任务描述（`task` 参数）
2. 当前工作目录和环境信息
3. 可用工具列表（减去 agent 工具）
4. 一个特殊指令："完成任务后，把结果写成一段文字总结返回。"

最后一点很关键。子 Agent 的全部输出（可能是几十轮工具调用的过程）最终被压缩成一段文字，作为 `tool_result` 返回给主 Agent。这段文字需要包含足够的信息让主 Agent 理解子任务做了什么，但又不能太长（否则撑爆主 Agent 的上下文）。

## 团队系统

在 AgentTool 之上，Claude Code 还有一个更高层的"团队"概念。`TeamCreateTool` 可以创建一组 Agent，给每个 Agent 分配角色（"frontend-agent"、"backend-agent"、"test-agent"），它们之间可以通过消息系统通信。

团队系统目前还在 Feature Flag 后面，没有对外发布。但从源码来看，它已经实现得相当完整了——包括角色分配、消息路由、进度追踪、结果汇总。

NanoCoder 目前只实现了基本的子 Agent 生成（对标 AgentTool 的普通模式），没有做 worktree 隔离和团队系统。但核心思路是一样的：独立上下文 + 受限工具集 + 结果摘要返回。50 行代码覆盖了 80% 的使用场景。

---

> 本文是 [Claude Code 源码导读](00-index.md) 系列第 6 篇。配套实现：[NanoCoder](https://github.com/he-yufeng/NanoCoder)
