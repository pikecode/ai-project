"""所有测试使用临时存储和模拟模型，禁止访问现有数据。"""

import asyncio

import httpx
import pytest

from src.api import main


class FakeGraph:
    def __init__(self):
        self.inputs = []

    async def astream(self, state, **kwargs):
        self.inputs.append(state)
        yield {
            "plan": {
                "current_step": 1,
                "tool_calls": [{"tool": "weather", "parameters": {"city": "Beijing"}}],
            }
        }
        await asyncio.sleep(0)
        yield {
            "execute": {
                "tool_results": [
                    {"tool": "weather", "success": True, "result": {"temperature": "20"}}
                ]
            }
        }
        yield {"plan": {"current_step": 2, "final_answer": "测试回答"}}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_WORKERS", "1")
    fake = FakeGraph()
    monkeypatch.setattr(main, "create_agent_graph", lambda *args: fake)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            client.fake_graph = fake
            yield client
