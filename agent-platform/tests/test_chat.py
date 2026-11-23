import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.api import main
from src.models import database


async def test_chat_and_stream_share_history_and_persistence(client):
    first = await client.post("/chat", json={"message": "你好"})
    assert first.status_code == 200
    data = first.json()
    assert data["response"] == "测试回答"
    assert data["tools_used"] == ["weather"]
    cid = data["conversation_id"]
    before = (await client.get("/conversations")).json()[0]["updated_at"]
    stream = await client.post("/chat/stream", json={"message": "继续", "conversation_id": cid})
    assert stream.status_code == 200
    events = [
        json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == [
        "thinking",
        "tool_call",
        "tool_result",
        "message",
        "done",
    ]
    assert events[-1]["response"] == "测试回答"
    assert events[-1]["tools_used"] == ["weather"]
    assert len(client.fake_graph.inputs[-1].messages) == 3
    messages = (await client.get(f"/conversations/{cid}/messages")).json()
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[-1]["tool_results"]["results"][0]["tool"] == "weather"
    after = (await client.get("/conversations")).json()[0]["updated_at"]
    assert after > before


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
async def test_invalid_and_missing_conversation(client, path):
    invalid = await client.post(path, json={"message": "你好", "conversation_id": "invalid"})
    assert invalid.status_code == 422
    missing = await client.post(path, json={"message": "你好", "conversation_id": str(uuid4())})
    assert missing.status_code == 404
    assert (await client.post(path, json={"message": "  "})).status_code == 422
    assert (await client.get("/conversations")).json() == []


async def test_api_auth_and_remote_access(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-secret")
    assert (await client.get("/health")).status_code == 401
    assert (
        await client.get("/health", headers={"Authorization": "Bearer test-secret"})
    ).status_code == 200
    monkeypatch.setenv("API_TOKEN", "")
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, client=("192.0.2.1", 1234)),
        base_url="http://test",
    ) as remote:
        assert (await remote.get("/health")).status_code == 401


async def test_events_arrive_before_graph_finishes(client, monkeypatch):
    waiting = asyncio.Event()

    class SlowGraph:
        async def astream(self, state, **kwargs):
            yield {"plan": {"current_step": 1, "tool_calls": [{"tool": "weather"}]}}
            await waiting.wait()
            yield {"plan": {"current_step": 2, "final_answer": "完成"}}

    monkeypatch.setattr(main, "graph", SlowGraph())
    async with database._async_session_maker() as session:
        args = await main.prepare_chat(main.ChatRequest(message="测试"), session)
        events = main.run_chat(*args, session)
        assert (await anext(events))["type"] == "thinking"
        assert (await asyncio.wait_for(anext(events), timeout=1))["type"] == "tool_call"
        assert not waiting.is_set()
        waiting.set()
        rest = [event async for event in events]
        assert rest[-1]["type"] == "done"


async def test_graph_error_never_saves_a_fake_answer(client, monkeypatch):
    class BrokenGraph:
        async def astream(self, state, **kwargs):
            raise RuntimeError("private internal detail")
            yield

    monkeypatch.setattr(main, "graph", BrokenGraph())
    response = await client.post("/chat/stream", json={"message": "测试"})
    assert '"type": "error"' in response.text
    assert '"type": "done"' not in response.text
    assert "private internal detail" not in response.text
    async with database._async_session_maker() as session:
        messages = (await session.execute(select(database.Message))).scalars().all()
        assert [m.role for m in messages] == ["user"]
    assert (await client.post("/chat", json={"message": "测试"})).status_code == 502


async def test_failed_request_retry_reuses_last_user_message(client, monkeypatch):
    old = main.graph

    class Broken:
        async def astream(self, state, **kwargs):
            raise RuntimeError("offline")
            yield

    monkeypatch.setattr(main, "graph", Broken())
    first = await client.post("/chat/stream", json={"message": "重试测试"})
    events = [json.loads(line[6:]) for line in first.text.splitlines() if line.startswith("data: ")]
    cid = events[0]["conversation_id"]
    monkeypatch.setattr(main, "graph", old)
    retried = await client.post(
        "/chat", json={"message": "重试测试", "conversation_id": cid, "retry": True}
    )
    assert retried.status_code == 200
    history = (await client.get(f"/conversations/{cid}/messages")).json()
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert (
        await client.post(
            "/chat", json={"message": "重试测试", "conversation_id": cid, "retry": True}
        )
    ).status_code == 409


async def test_side_effect_attempt_disables_retry_even_if_graph_fails(client, monkeypatch):
    class SideEffect:
        async def astream(self, state, **kwargs):
            yield {"plan": {"tool_calls": [{"tool": "send_email", "parameters": {}}]}}
            raise RuntimeError("unknown outcome")

    monkeypatch.setattr(main, "graph", SideEffect())
    response = await client.post("/chat/stream", json={"message": "测试"})
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    cid = events[0]["conversation_id"]
    result = await client.post(
        "/chat", json={"message": "测试", "conversation_id": cid, "retry": True}
    )
    assert result.status_code == 409


async def test_reference_failure_is_explicit_and_does_not_save_answer(client, monkeypatch):
    from src.agent.nodes import ReferenceValidationError

    class InvalidReferences:
        async def astream(self, state, **kwargs):
            raise ReferenceValidationError("回答引用未通过校验，请重试或核对原文。")
            yield

    monkeypatch.setattr(main, "graph", InvalidReferences())
    response = await client.post("/chat/stream", json={"message": "审批人是谁？"})
    assert "引用未通过校验" in response.text
    assert '"type": "done"' not in response.text
    async with database._async_session_maker() as session:
        messages = (await session.execute(select(database.Message))).scalars().all()
        assert [message.role for message in messages] == ["user"]
