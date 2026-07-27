# 任务背景

`string_utils.py` 是一个字符串处理工具模块，目前只有一个 `to_lower` 函数。
项目需要一个 `slugify` 函数，用于将任意字符串转换为 URL 安全的 slug 格式。

# 需求

在 `string_utils.py` 中新增一个 `slugify(text)` 函数，要求：

1. 将字符串转为小写
2. 将空格和连续空白替换为单个连字符 `-`
3. 移除所有非字母数字和连字符的字符
4. 去除首尾的连字符
5. 将连续多个连字符合并为一个

示例：
- `"Hello World"` → `"hello-world"`
- `"  Python 3.14  "` → `"python-314"`
- `"Café & Restaurant"` → `"cafe-restaurant"`
- `"---test---"` → `"test"`

# 约束

1. 只能使用 Python 标准库，不能引入第三方包
2. 不能修改已有的 `to_lower` 函数
3. 处理空字符串和只含特殊字符的输入时返回空字符串
