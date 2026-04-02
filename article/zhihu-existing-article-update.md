# 在已有知乎文章末尾追加的引流段落

（追加到「我花了一晚上读完 Claude Code 泄露的全部源码，这是我发现的」文章末尾）

---

## 更新：两个后续项目

写完这篇文章之后，我做了两件事：

**1. 用 1300 行 Python 重写了 Claude Code 的核心架构。** 支持 DeepSeek、Qwen、Kimi 等任意大模型。所有关键设计模式（搜索替换编辑、并行工具执行、三层上下文压缩、子代理生成）都有实现。代码量只有原版的 1/400，但核心逻辑完全一致。

👉 [NanoCoder - GitHub](https://github.com/he-yufeng/NanoCoder)

**2. 写了一套 7 篇的 Claude Code 源码导读。** 从 Agent 循环到工具系统到多 Agent 协作到未发布功能，每篇围绕一个核心设计模式展开。如果你看完这篇分析想深入了解具体实现，从导读开始。

👉 [Claude Code 源码导读系列](https://github.com/he-yufeng/NanoCoder/tree/main/article)

另外还有完整版导读（16 篇、16 万字）在 GitHub 的 docs/ 目录下，覆盖了从构建系统到 MCP 协议的每个子系统。
