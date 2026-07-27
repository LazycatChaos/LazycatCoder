# 任务背景

`config_utils.py` 是一个配置管理模块，目前只有 `load_config` 函数用于从字典加载配置。
项目需要支持配置覆盖功能：将用户配置与默认配置合并，用户配置优先。

# 需求

1. 在 `config_utils.py` 中新增 `merge_configs(default: dict, override: dict) -> dict` 函数
2. 合并必须是**深度合并**（deep merge）：嵌套字典也要递归合并，而不是简单覆盖
3. 在 `app.py` 中使用 `merge_configs` 来合并默认配置和用户配置

# 示例

```python
default = {"db": {"host": "localhost", "port": 5432}, "debug": False}
override = {"db": {"port": 3306}, "debug": True}
result = merge_configs(default, override)
# result = {"db": {"host": "localhost", "port": 3306}, "debug": True}
```

# 约束

1. 只能使用 Python 标准库
2. 不能修改 `load_config` 函数的行为
3. 合并不能修改原始的 default 或 override 字典（返回新字典）
4. 非字典类型的值，override 直接覆盖 default
