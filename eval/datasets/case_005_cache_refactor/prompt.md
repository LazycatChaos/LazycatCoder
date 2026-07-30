# 任务背景

`cache.py` 实现了一个简单的内存缓存，支持基本的 get/set/delete 操作。
现在需要给缓存增加 TTL（Time-To-Live）功能，让缓存项可以自动过期。

# 需求

1. 给 `SimpleCache` 增加 TTL 支持：
   - `set(key, value, ttl=None)` — ttl 为秒数，None 表示永不过期
   - `get(key)` — 如果缓存项已过期，返回 None 并清理该项
   - 过期项在 get 时惰性清理

2. **过期项在所有查询中应被视为不存在**：
   - `__len__` 只统计未过期的项
   - `__contains__` 对过期项返回 False
   - `keys()` 只返回未过期的 key

3. 保持向后兼容：
   - `set(key, value)` 不带 ttl 参数时，行为与之前完全一致（永不过期）
   - 所有已有方法签名保持不变

# 约束

1. 只能使用 Python 标准库
2. 不能删除或重命名已有方法
3. 保持向后兼容：不带 ttl 参数的 set 调用行为不变
