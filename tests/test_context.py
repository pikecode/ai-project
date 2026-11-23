import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.context import ContextLimitError, fit_messages
from src.agent.documents import attachment_context, split_pages
from src.tools.base import registry


def test_budget_removes_only_complete_old_turns(monkeypatch):
    monkeypatch.setattr(registry, "_tools", {})
    monkeypatch.setenv("CONTEXT_TOKENS", "4096")
    messages = [
        HumanMessage(content="旧问题" * 100),
        AIMessage(content="旧回答" * 100),
        HumanMessage(content="新问题"),
        AIMessage(content="", tool_calls=[{"id": "x", "name": "sample", "args": {}}]),
        ToolMessage(content="结果", tool_call_id="x"),
    ]
    selected, removed = fit_messages(messages)
    assert removed == 2
    assert selected[0].content == "新问题"
    assert selected[1].tool_calls[0]["id"] == selected[2].tool_call_id


def test_current_question_is_never_silently_truncated(monkeypatch):
    monkeypatch.setattr(registry, "_tools", {})
    monkeypatch.setenv("CONTEXT_TOKENS", "4096")
    with pytest.raises(ContextLimitError):
        fit_messages([HumanMessage(content="过长" * 3000)])


async def test_long_document_retrieval_can_select_last_page(monkeypatch):
    class Retriever:
        def select_passages(self, query, passages):
            return [item for item in passages if "最终结论" in item["text"]]

    monkeypatch.setattr(registry, "_tools", {"knowledge_search": Retriever()})
    monkeypatch.setenv("CONTEXT_TOKENS", "4096")
    # 预算测试不需要工具描述，独立控制预算大小。
    monkeypatch.setattr("src.agent.documents.input_budget", lambda: 2000)
    pages = [
        {"page": 1, "text": "无关内容" * 2000},
        {"page": 60, "text": "最终结论编号 FINAL-6927"},
    ]
    context, warnings = await attachment_context(
        "最终结论编号是什么", {"pages": pages, "text": "无关内容" * 2000, "truncated": False}
    )
    assert "FINAL-6927" in context and "第60页" in context
    assert warnings and "片段" in warnings[0]


def test_page_chunks_keep_overlap_and_page_numbers():
    chunks = split_pages([{"page": 9, "text": "a" * 900}], size=700, overlap=100)
    assert len(chunks) == 2
    assert chunks[0]["page"] == chunks[1]["page"] == 9
    assert len(chunks[0]["text"]) == 700


async def test_cancel_stops_waiting_for_graph(client, monkeypatch):
    from src.api import main
    from src.models import database

    entered, canceled = asyncio.Event(), asyncio.Event()

    class SlowGraph:
        async def astream(self, state, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()
            yield

    monkeypatch.setattr(main, "graph", SlowGraph())
    async with database._async_session_maker() as session:
        args = await main.prepare_chat(main.ChatRequest(message="测试取消"), session)
        events = main.run_chat(*args, session)
        assert (await anext(events))["type"] == "thinking"
        task = asyncio.create_task(anext(events))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert canceled.is_set()
