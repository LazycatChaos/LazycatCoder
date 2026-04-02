# 知乎文章草稿 2：导读系列引流

**标题**：Claude Code 源码导读：七个你应该知道的设计模式

---

上个月 Claude Code 源码泄露之后，我写了一篇分析文章（17万阅读，感谢支持）。很多朋友说："分析是看了，但源码 51 万行，自己看不动，能不能出个导读？"

这个系列就是导读。不是面面俱到的文档（那个也有，16 篇 16 万字的完整版在 GitHub 上），而是我从 51 万行里挑出的 7 个最值得开发者了解的设计模式。每篇围绕一个核心问题展开。

如果你在做 AI Agent 相关的工作，或者单纯对"一个 51 万行的生产级 AI 产品内部长什么样"好奇，这个系列应该能给你一些启发。

## 目录

### 第一篇：从 51 万行说起

为什么一个 CLI 工具需要 51 万行代码？Claude Code 的技术栈选型（Bun + React + TypeScript），目录结构，以及贯穿整个代码库的十大设计哲学。这篇建立全局心智模型，是后面六篇的地图。

### 第二篇：1729 行的 while(true)

`src/query.ts` 是 Claude Code 的心脏。1729 行代码，一个 while(true) 循环，驱动整个 AI Agent 的核心行为：调用 LLM、解析工具调用、执行工具、把结果喂回 LLM。听起来简单，但错误恢复、流式解析、token 预算管理的细节处理非常精妙。

### 第三篇：让 AI 安全地改你的代码

Claude Code 的编辑策略不用行号补丁，也不整文件重写。它让 LLM 指定一段精确的、必须唯一出现的文本来查找替换。就这一个约束，干掉了一整类编辑 bug。这篇还分析了工具系统的两阶段门控、BashTool 的 1143 行安全堡垒。

### 第四篇：有限窗口，无限任务

128K token 看起来很大，十几轮工具调用就快满了。Claude Code 不是简单截断旧消息，而是用四层策略渐进压缩：裁剪工具输出 → LLM 摘要 → 硬压缩 → 后台自动压缩。每一层应对不同程度的上下文膨胀，工程权衡很到位。

### 第五篇：边想边做

`StreamingToolExecutor`（530 行）让 Claude Code 在 LLM 还没说完的时候就开始执行工具。不是等生成完了再串行执行，而是流式解析、并行启动。这篇分析它的事件驱动状态机实现和实际性能收益。

### 第六篇：当一个 Claude 不够用

AgentTool 有 1397 行、目录总共 6700 行。子 Agent 生成、Worktree 隔离、团队协作。一个有意思的设计决策：子 Agent 不能再创建子 Agent。为什么。

### 第七篇：Feature Flag 背后的秘密

44 个 Feature Flag。KAIROS 永驻守护进程模式、Buddy 电子宠物系统、Voice Mode（内部代号 Amber Quartz）、Bridge Mode（31 个文件的远程控制系统）、Undercover Mode（Anthropic 员工给外部项目提代码时自动去 AI 归因）。这些未发布功能揭示了 AI 编程工具的未来方向。

## 完整阅读

全部 7 篇文章在 GitHub 上：

👉 https://github.com/he-yufeng/NanoCoder/tree/main/article

完整版导读（16 篇 16 万字，覆盖从构建系统到 MCP 协议的每个子系统）：

👉 https://github.com/he-yufeng/NanoCoder/tree/main/docs

配套的 Python 参考实现（1300 行，Claude Code 核心架构的可运行版本，支持任意大模型）：

👉 https://github.com/he-yufeng/NanoCoder

---

如果觉得有用，给个 Star。有技术问题可以在评论区讨论，或者到 GitHub 开 issue。
