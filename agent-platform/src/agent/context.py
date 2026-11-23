"""保守预算：按 UTF-8 字节上界计数，避免依赖其他模型的分词器。"""

import json

from langchain_core.messages import HumanMessage

from src.config import Config
from src.tools.base import registry


class ContextLimitError(ValueError):
    pass


def byte_cost(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def input_budget():
    config = Config.from_env()
    if config.context_tokens < 4096 or not 128 <= config.output_tokens < config.context_tokens:
        raise ContextLimitError("上下文配置无效，请检查 CONTEXT_TOKENS 和 OUTPUT_TOKENS。")
    # 保留消息模板、系统提示和服务端工具模板的余量。
    return config.context_tokens - config.output_tokens - byte_cost(registry.list_tools()) - 1024


def message_cost(message):
    cost = byte_cost(message.content) + 64
    if getattr(message, "tool_calls", None):
        cost += byte_cost(message.tool_calls)
    return cost


def fit_messages(messages):
    """仅移除完整的旧轮次；当前问题和正在执行的工具轮次不得截断。"""
    messages = list(messages)
    budget = input_budget()
    groups = []
    for message in messages:
        if isinstance(message, HumanMessage) or not groups:
            groups.append([])
        groups[-1].append(message)
    removed = 0
    while len(groups) > 1 and sum(message_cost(m) for group in groups for m in group) > budget:
        removed += len(groups.pop(0))
    if sum(message_cost(m) for group in groups for m in group) > budget:
        raise ContextLimitError("当前问题或工具结果超出上下文预算，请缩小问题或文件范围。")
    return [m for group in groups for m in group], removed
