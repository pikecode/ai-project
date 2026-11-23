"""固定两库的资料、签名预览和会话边界。"""

import hashlib

import numpy as np
import pytest

from src.tools.rag import get_knowledge_tool


class Embedding:
    def encode(self, texts, **kwargs):
        return np.array([[1.0, 0.0] for _ in texts], dtype=np.float32)


async def test_libraries_keep_documents_and_sources_separate(client):
    daily = get_knowledge_tool()
    test = get_knowledge_tool("test")
    daily.model = Embedding()
    for base, text in [("daily", "日常审批"), ("test", "演示审批")]:
        url = f"/knowledge/add?knowledge_base={base}"
        assert (await client.post(url, json={"documents": [text]})).status_code == 200
    assert test.model is daily.model
    assert test.data_dir == daily.data_dir / "test"
    for base, text in [("daily", "日常审批"), ("test", "演示审批")]:
        source_id = "KB-" + hashlib.sha256(text.encode()).hexdigest()[:16]
        assert (
            await client.get(f"/knowledge/sources/{source_id}?knowledge_base={base}")
        ).status_code == 200
        other = "test" if base == "daily" else "daily"
        assert (
            await client.get(f"/knowledge/sources/{source_id}?knowledge_base={other}")
        ).status_code == 404
        result = await client.post(
            f"/knowledge/search?knowledge_base={base}", json={"query": "审批", "top_k": 5}
        )
        assert [r["content"] for r in result.json()["results"]] == [text]
    assert (await client.get("/knowledge/stats?knowledge_base=../outside")).status_code == 422


async def test_signed_preview_cannot_change_library(client):
    get_knowledge_tool().model = Embedding()
    preview = (
        await client.post(
            "/knowledge/preview?knowledge_base=test", files={"file": ("demo.txt", b"demo")}
        )
    ).json()
    assert preview["payload"]["knowledge_base"] == "test"
    assert (await client.post("/knowledge/import", json=preview)).status_code == 409
    assert (
        await client.post("/knowledge/import?knowledge_base=test", json=preview)
    ).status_code == 200
    assert (await client.get("/knowledge/stats")).json()["total_documents"] == 0
    assert (await client.get("/knowledge/stats?knowledge_base=test")).json()["total_documents"] == 1


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
async def test_conversation_binding_and_history_filter(client, route):
    daily = (await client.post("/chat", json={"message": "日常问题"})).json()["conversation_id"]
    test = (
        await client.post("/chat", json={"message": "演示问题", "knowledge_base": "test"})
    ).json()["conversation_id"]
    assert client.fake_graph.inputs[-1].knowledge_base == "test"
    assert [c["id"] for c in (await client.get("/conversations")).json()] == [daily]
    assert [c["id"] for c in (await client.get("/conversations?knowledge_base=test")).json()] == [
        test
    ]
    assert (await client.get(f"/conversations/{test}/messages")).status_code == 404
    assert (
        await client.post(route, json={"message": "继续", "conversation_id": test})
    ).status_code == 409
    messages = (await client.get(f"/conversations/{test}/messages?knowledge_base=test")).json()
    assert len(messages) == 2


async def test_execution_uses_server_bound_library(client):
    import asyncio

    from src.agent.nodes import execute_node
    from src.agent.state import AgentState

    get_knowledge_tool().model = Embedding()
    get_knowledge_tool().add_documents(["日常审批"])
    get_knowledge_tool("test").add_documents(["演示审批"])

    async def run(base, parameters):
        return await execute_node(
            AgentState(
                messages=[],
                knowledge_base=base,
                tool_calls=[{"id": "search", "tool": "knowledge_search", "parameters": parameters}],
            ),
            "knowledge",
        )

    daily, test = await asyncio.gather(
        run("daily", {"query": "审批"}), run("test", {"query": "审批"})
    )
    assert daily["tool_results"][0]["result"]["results"][0]["content"] == "日常审批"
    assert test["tool_results"][0]["result"]["results"][0]["content"] == "演示审批"
    invalid = await run("test", {"query": "审批", "knowledge_base": "daily"})
    assert invalid["tool_results"][0]["success"] is False


async def test_legacy_conversation_defaults_to_daily(client):
    from src.models import database
    from src.models.database import Conversation

    async with database._async_session_maker() as session:
        conversation = Conversation(title="旧会话", meta={})
        session.add(conversation)
        await session.commit()
        cid = str(conversation.id)
    assert [c["id"] for c in (await client.get("/conversations")).json()] == [cid]
    assert (await client.get(f"/conversations/{cid}/messages")).status_code == 200
    assert (await client.get("/conversations?knowledge_base=test")).json() == []
