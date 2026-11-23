"""在提供操作系统级隔离前关闭代码执行。"""

from .base import BaseTool, registry


class CodeExecuteTool(BaseTool):
    name = "code_execute"
    description = "Code execution is disabled until an isolated runner is configured"
    parameters = {"type": "object", "properties": {"code": {"type": "string"}}}

    async def execute(self, code: str, timeout: int = 30) -> dict:
        return {"success": False, "error": "Code execution is disabled: isolated runner required"}


def init_code_execute_tool():
    registry.register(CodeExecuteTool())
