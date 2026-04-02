# 第四篇：有限窗口，无限任务

128K token 听起来很多。但一个稍微复杂点的编程任务——"重构这个模块的错误处理"——可能涉及读十几个文件、跑几轮测试、反复修改。每一轮的工具输出都可能几千 token。十几轮之后，128K 就快见底了。

Claude Code 没有选择简单的截断（"超出限制就丢掉最早的消息"），而是设计了一套四层压缩策略。我在泄露的源码里找到了四个相关的 Feature Flag：

```
HISTORY_SNIP
CACHED_MICROCOMPACT
CONTEXT_COLLAPSE
REACTIVE_COMPACT (即 Autocompact)
```

每一层处理不同规模的上下文膨胀，从轻量裁剪到激进压缩。

## 第一层：HISTORY_SNIP — 裁工具输出

最容易膨胀的不是用户消息，也不是 LLM 回复，而是**工具输出**。一个 `grep` 返回 200 行匹配结果，一个 `cat` 返回整个文件内容，一个 `bash` 跑了个 `npm test` 输出 500 行日志。

HISTORY_SNIP 做的事情很简单：遍历历史消息，找到所有 `role: "tool"` 的消息，如果内容超过阈值，替换成一个精简版本。精简策略是保留前几行和后几行（最有用的信息通常在开头的命令回显和结尾的总结/错误信息里），中间用 "[snipped N lines]" 替代。

这是成本最低的压缩——不需要调用 LLM，不会丢失关键信息，效果立竿见影。

NanoCoder 的 `context.py` 里的 `_snip_tool_outputs()` 就是这一层的实现。

## 第二层：CACHED_MICROCOMPACT — LLM 摘要

如果第一层裁完还是太长，第二层启动。

这一层拿老的对话片段（比如最早的 10 轮 user-assistant 交互），发给 LLM 做一次摘要："把这段对话压缩成关键信息。保留文件路径、做出的决策、遇到的错误、当前任务状态。"

LLM 返回的摘要通常只有原对话的 1/5 到 1/10。然后用这个摘要替换掉那 10 轮旧对话。

"Cached" 指的是这个摘要会被缓存——如果下次又需要压缩，上次的摘要直接用，不用重新调 LLM 生成。

NanoCoder 的 `_summarize_old()` 实现了这一层。用同一个 LLM 实例来做摘要，如果失败就 fallback 到文本提取。

## 第三层：CONTEXT_COLLAPSE — 硬压缩

如果前两层都做了，token 还是太多（比如用户在一个超长会话里处理多个任务），第三层做最激进的压缩：只保留最近 4-6 条消息，其他全部压缩成一段总结。

这一层会丢信息。但它比直接截断好：截断是随机的（按时间顺序丢），而 CONTEXT_COLLAPSE 至少会试图保留关键决策和文件路径。

NanoCoder 的 `_hard_collapse()` 是这一层。

## 第四层：Autocompact / REACTIVE_COMPACT — 后台压缩

这是最巧妙的一层。Claude Code 有一个 `/compact` 命令让用户主动触发压缩，但 REACTIVE_COMPACT 是自动的——当系统检测到上下文接近上限时，在用户下一次输入之前自动执行压缩。用户不需要关心 token 管理，系统自己处理。

这一层在 NanoCoder 里对应的是 `maybe_compress()` 的自动触发——每次 `agent.chat()` 开头和每轮工具执行后都会检查是否需要压缩。

## 压缩的工程权衡

做上下文压缩不难，做好很难。几个 Claude Code 源码里体现的工程考量：

**什么信息绝对不能丢？** 文件路径（LLM 需要知道之前编辑了哪些文件）、做出的关键决策（避免重复讨论）、未解决的错误（需要继续处理的问题）。Claude Code 的摘要 prompt 明确列出了这些保留项。

**摘要本身占多少 token？** 如果摘要太长，压缩的意义就打折了。Claude Code 对摘要长度有隐含控制——通过 `max_tokens` 参数限制摘要 LLM 调用的输出长度。

**用什么模型做摘要？** Claude Code 用的是同一个模型。这意味着每次压缩都有一次额外的 API 调用成本。NanoCoder 也是这样做的——用 `self.llm` 做摘要。如果你想省钱，可以换一个便宜的模型专门做摘要。

**压缩后 LLM 会不会"忘记"之前同意做的事？** 这是最大的风险。如果压缩过程中丢掉了"用户说不要修改 config.yaml"这样的指令，LLM 可能就去改了。Claude Code 的解法是在摘要 prompt 里强调"保留用户指令和约束"。

---

> 本文是 [Claude Code 源码导读](00-index.md) 系列第 4 篇。配套实现：[NanoCoder](https://github.com/he-yufeng/NanoCoder)
