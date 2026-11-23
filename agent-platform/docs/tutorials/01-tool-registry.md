# 第01课：工具注册不等于执行

核对日期：2026-11-06。前置知识：字典、函数调用；遇到类和对象不熟悉时先看下面的说明。

## 目标与场景

用户问“办公费用800元谁审批”，模型可能请求 `knowledge_search`。程序首先需要根据名称找到对应工具。本课只学习如何找到，不运行模型和检索。

学完后应能回答：工具存在哪里？找不到时返回什么？找到工具后为什么还没有执行？

## 对照真实代码

打开 [base.py](../../src/tools/base.py)，搜索 `class ToolRegistry`。核心代码简化如下，省略类型标注和其他方法：

```python
class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)
```

类是定义对象行为的模板；`ToolRegistry()` 创建一个对象；`self` 表示当前对象。每个注册表对象有自己的 `_tools` 字典，下划线表示内部使用约定，不是安全权限机制。

`register` 用工具名称作为键、工具对象作为值。`get` 调用字典的 `.get()`：存在时返回对象，不存在时返回 `None`，不会因为缺少名称而抛出 `KeyError`。

## 设计取舍

方案一：为每个名称增加一条 `if/elif`，工具很少时直观，但增加工具需要反复修改查找逻辑。

方案二：用字典注册再查找。项目采用这一方案，结构简单（KISS）、减少重复分支（DRY），也将查找与执行职责分开（SOLID）。暂不增加插件发现机制（YAGNI）。

当前实现同名注册会覆盖旧对象；这不是本课要修改的功能。以后引入多个外部工具来源时，再讨论名称冲突处理。

## 先预测，再运行

在 `agent-platform/` 执行以下练习。它使用局部注册表，不启动服务、不访问网络、不修改正式数据；`NamedTool` 仅用于说明查找，不是可直接用于生产的完整工具。

先不要执行，写下 `a`、`b`、`calls` 的预期结果：

```bash
.venv/bin/python - <<'PY'
from src.tools.base import ToolRegistry

class NamedTool:
    name = "knowledge_search"

registry = ToolRegistry()
search_tool = NamedTool()
calls = 0
registry.register(search_tool)
a = registry.get("knowledge_search")
b = registry.get("send_email")
print(a is search_tool, b, calls)
PY
```

这里的 `is` 判断是否为同一个对象；`calls` 是普通变量，没有任何代码增加它。

<details>
<summary>预测后查看参考结果</summary>

输出 `True None 0`。`a` 是原工具对象，`b` 是 `None`。这里只做注册和查找，没有调用工具执行方法；变量为0本身也不是完整的执行审计。

</details>

## 独立练习

1. 把工具名称改成 `document_lookup`，分别用旧名和新名查询，运行前先预测。
2. 新建第二个 `ToolRegistry()`，直接查询 `document_lookup`。解释为什么两个注册表结果不同。
3. 不看正文，用自己的话说明注册、查找、执行、授权四者区别。授权位置将在第02课对照真实执行节点学习。

## 验收与复习

完成标准：预测有记录，实际输出已核对，能够解释差异，并明确“注册表中存在工具”不代表“允许执行”。只读过正文或看过答案，记为学习中。

导师把学习者的回答、实际反馈及未解决问题记入学习记录，不替学习者填已掌握。复习时换一个名称重做，不只背诵输出。

下一课：从 `plan_node` 到 `execute_node` 跟踪一次工具调用，找出权限、参数校验和执行入口。
