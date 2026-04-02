# 第三篇：让 AI 安全地改你的代码

Claude Code 里最让我佩服的设计不是什么高深的架构模式，而是 `FileEditTool` 里一个看似简单的约束：**old_string 必须在文件中唯一出现。**

就这一条规则，解决了 AI 代码编辑领域最头疼的问题。

## 问题在哪

让 LLM 编辑代码，有几种常见思路：

**行号补丁**（"把第 42 行改成 xxx"）。问题：LLM 对行号的记忆非常不靠谱，尤其是在上下文压缩之后，行号可能完全对不上。更麻烦的是，如果你在第 30 行插入了一行，后面所有行号都变了。

**整文件重写**（"输出完整的新文件"）。问题：浪费 token。一个 500 行的文件，你只想改 2 行，但 LLM 要重新生成全部 500 行。而且 LLM 在复制长文本时经常丢行或者微调格式。

**diff 格式**（"输出 unified diff 补丁"）。问题：LLM 生成的 diff 格式经常有格式错误——行号偏移、缺少上下文行、`@@` 标记不对。解析成功率不高。

Claude Code 的解法是：**搜索替换**。

## 搜索替换的精妙之处

LLM 不需要知道行号，也不需要输出整个文件。它只需要做两件事：

1. 给出一段**精确的**、**在文件中唯一存在**的文本（old_string）
2. 给出替换后的文本（new_string）

```json
{
  "file_path": "src/auth.py",
  "old_string": "def verify_token(token):\n    return jwt.decode(token, SECRET)",
  "new_string": "def verify_token(token):\n    try:\n        return jwt.decode(token, SECRET)\n    except jwt.ExpiredSignatureError:\n        return None"
}
```

如果 old_string 在文件中出现了 0 次——说明 LLM 记错了文件内容。返回错误，让它先 `read_file` 再试。

如果 old_string 出现了 2 次以上——说明给的上下文不够，无法确定改哪一处。返回错误，要求 LLM 包含更多周围的行来消除歧义。

只有恰好出现 1 次的情况才执行替换。这个约束优雅地消除了所有编辑歧义。

## 工具系统的接口设计

`FileEditTool` 是 Claude Code 30+ 个工具之一。所有工具共享 `src/Tool.ts`（792 行）定义的泛型接口。几个值得注意的设计：

**两阶段门控。** 每个工具调用经过两道检查：

```
validateInput()  →  checkPermissions()  →  execute()
```

第一道 `validateInput` 检查输入是否合法（文件路径存不存在、命令是不是空字符串）。不合法就直接告诉 LLM，不弹窗问用户。

第二道 `checkPermissions` 检查权限。Claude Code 有五级权限模式，从"全部自动通过"到"每次都要用户确认"。只有 `validateInput` 通过了才会进到权限检查，避免用频繁的权限弹窗打扰用户。

**工具 schema = LLM 的说明书。** 每个工具的 `description` 和参数 `schema` 不仅是给开发者看的文档，更重要的是给 LLM 看的。LLM 根据这些描述来决定什么时候调用哪个工具、传什么参数。所以 Claude Code 的工具描述写得非常讲究——既简洁又准确，还会包含使用建议（"use edit_file for small changes, write_file only for new files"）。

**大结果落盘。** 工具的 `maxResultSizeChars` 字段决定了输出超过多少字节时写入磁盘文件，只在上下文里留一个摘要和文件路径。这样一个 `grep` 返回了几万行匹配结果，也不会撑爆上下文窗口。LLM 需要详细看的时候可以再 `read_file` 那个磁盘文件。

## BashTool：1143 行的安全堡垒

BashTool 是最复杂的单个工具。不是因为"执行 shell 命令"这件事本身复杂，而是因为安全检查很复杂。

它要处理的问题包括：

- **输出管理**。命令输出可能几百 MB（`cat` 一个大日志文件）。BashTool 有输出长度限制，超过就截断，保留头和尾。
- **超时控制**。默认 120 秒超时。长命令可以通过参数调大。
- **交互式命令检测**。`vim`、`less`、`ssh` 这些需要用户交互的命令，直接拒绝——LLM 不会用交互式终端。
- **工作目录追踪**。如果命令中有 `cd`，BashTool 会更新后续命令的工作目录。
- **信号处理**。用户按 Ctrl+C 时，正在跑的命令要被 kill 掉，但已经产生的输出要保留。

NanoCoder 的 BashTool 实现了其中最核心的部分：输出截断（头尾保留）、超时控制、危险命令检测。大约 80 行。

## 编辑后的 Diff

Claude Code 在编辑成功后会给用户展示一个漂亮的 diff 视图。NanoCoder 也做了这个——每次 `edit_file` 成功后，用 Python 标准库的 `difflib.unified_diff` 生成 diff 输出。这不是花哨的装饰，而是实际的信任建设：用户需要看到 AI 到底改了什么，才敢让它继续改。

---

> 本文是 [Claude Code 源码导读](00-index.md) 系列第 3 篇。配套实现：[NanoCoder](https://github.com/he-yufeng/NanoCoder)
