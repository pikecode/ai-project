import json
import pickle
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import faiss
import numpy as np
import pytest
from sqlalchemy import text

from src.config import workspace_path
from src.tools.base import registry, tool
from src.tools.code import CodeExecuteTool
from src.tools.file import FileReadTool, FileWriteTool
from src.tools.http import HTTPRequestTool
from src.tools.rag import RAGTool
from src.tools.sql import SQLQueryTool


class FakeEmbedding:
    def encode(self, documents, **kwargs):
        return np.array([[1.0, float(len(doc)) / 100] for doc in documents], dtype=np.float32)


async def test_file_boundaries_and_no_overwrite(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(root))
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    for source in ["../secret", str(tmp_path / "secret"), "escape/secret"]:
        with pytest.raises(ValueError):
            workspace_path(source)
    writer = FileWriteTool()
    assert (await writer.execute("result.txt", "text", content="原内容"))["success"]
    assert not (await writer.execute("result.txt", "text", content="覆盖"))["success"]
    assert (await FileReadTool().execute("uploads/result.txt"))["content"] == "原内容"
    assert not (await FileReadTool().execute("../secret"))["success"]
    assert not (await CodeExecuteTool().execute("print('unsafe')"))["success"]


async def test_http_requires_explicit_public_https_host(monkeypatch):
    monkeypatch.setenv("HTTP_ALLOWED_HOSTS", "127.0.0.1,localhost")
    tool_instance = HTTPRequestTool()
    for url in [
        "http://example.com",
        "https://example.com",
        "https://127.0.0.1",
        "https://localhost",
        "file:///etc/passwd",
    ]:
        assert not (await tool_instance.execute(url))["success"]


async def test_sql_is_bounded_and_read_only(tmp_path):
    sql = SQLQueryTool(f"sqlite:///{tmp_path}/sql.db")
    try:
        async with sql.engine.begin() as conn:
            await conn.execute(text("CREATE TABLE sample (value INTEGER)"))
            await conn.execute(text("INSERT INTO sample VALUES (1), (2), (3)"))
        result = await sql.execute("SELECT * FROM sample LIMIT 100", limit=2)
        assert result["success"] and len(result["rows"]) == 2
        assert not (await sql.execute("DELETE FROM sample"))["success"]
        assert not (await sql.execute("SELECT 1; DELETE FROM sample"))["success"]
        assert not (await sql.execute("SELECT * FROM sample", limit=-1))["success"]
    finally:
        await sql.engine.dispose()


async def test_async_tool_decorator():
    @tool("test_async", "test", {})
    async def decorated(value):
        return {"success": True, "value": value}

    assert await registry.get("test_async").execute(value=4) == {"success": True, "value": 4}


async def test_knowledge_api_and_agent_share_updated_index(client):
    rag = registry.get("knowledge_search")
    rag.model = FakeEmbedding()
    assert (await rag.execute("测试"))["results"] == []
    assert (await client.post("/knowledge/add", json={"documents": ["第一份文档"]})).json()[
        "success"
    ]
    assert (await rag.execute("测试"))["results"][0]["content"] == "第一份文档"
    assert (await client.get("/knowledge/stats")).json() == {"total_documents": 1}
    reloaded = RAGTool()
    reloaded.model = FakeEmbedding()
    assert (await reloaded.execute("测试"))["results"][0]["content"] == "第一份文档"


def test_knowledge_concurrent_writes_and_failed_save(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    rag = RAGTool()
    rag.model = FakeEmbedding()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: rag.add_documents([f"文档{i}"]), range(8)))
    assert all(result["success"] for result in results)
    assert rag.stats()["total_documents"] == 8
    assert len(json.loads(rag.store_path.read_text())["documents"]) == 8
    import os

    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    assert not rag.add_documents(["不能保存"])["success"]
    assert rag.stats()["total_documents"] == 8
    assert RAGTool().stats()["total_documents"] == 8


def test_legacy_knowledge_is_preserved_and_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    index = faiss.IndexFlatIP(2)
    index.add(FakeEmbedding().encode(["旧文档"]))
    faiss.write_index(index, str(tmp_path / "rag_index.faiss"))
    (tmp_path / "rag_docs.pkl").write_bytes(pickle.dumps(["旧文档"]))
    rag = RAGTool()
    rag.model = FakeEmbedding()
    assert rag.stats()["total_documents"] == 1
    assert rag.add_documents(["新文档"])["success"]
    assert RAGTool().stats()["total_documents"] == 2
    assert (tmp_path / "rag_docs.pkl").read_bytes() == pickle.dumps(["旧文档"])


async def test_http_pins_validated_address_and_preserves_tls_name(monkeypatch):
    import socket

    import httpx

    monkeypatch.setenv("HTTP_ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"value": 42})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = await HTTPRequestTool().execute("https://api.example.com/data")
    assert result["success"] and result["data"] == {"value": 42}
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "api.example.com"
    assert requests[0].extensions["sni_hostname"] == "api.example.com"


async def test_vision_uses_shared_config_and_local_file(tmp_path, monkeypatch):
    import httpx

    from src.tools.vision import VisionTool

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_BASE_URL", "http://model.internal:11434/v1")
    monkeypatch.setenv("VISION_MODEL", "custom-vision")
    (tmp_path / "sample.png").write_bytes(b"sample-image")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "图像描述"}}]})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = await VisionTool().execute(source="sample.png")
    assert result["success"] and result["description"] == "图像描述"
    assert str(requests[0].url) == "http://model.internal:11434/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == "custom-vision"


def test_legacy_pickle_cannot_load_classes(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    (tmp_path / "rag_docs.pkl").write_bytes(pickle.dumps(Path("forbidden")))
    index = faiss.IndexFlatIP(2)
    faiss.write_index(index, str(tmp_path / "rag_index.faiss"))
    with pytest.raises(ValueError, match="Unsupported"):
        RAGTool().stats()


async def test_chart_rendering_cleans_up_failed_and_successful_figures():
    import matplotlib.pyplot as plt

    from src.tools.visualize import VisualizationTool

    charts = VisualizationTool()
    failed = await charts.execute('[{"value":1}]', "scatter")
    assert not failed["success"]
    assert plt.get_fignums() == []
    successful = await charts.execute('[{"value":1}]', "bar")
    assert successful["success"] and successful["image_base64"]
    assert plt.get_fignums() == []


async def test_markdown_retrieval_preserves_source_and_excludes_unrelated_process(
    tmp_path, monkeypatch
):
    import hashlib

    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))

    class RoutingEmbedding:
        def encode(self, texts, **kwargs):
            return np.array(
                [[1.0, 0.0] if "账号" in item else [0.0, 1.0] for item in texts], dtype=np.float32
            )

    document = "# 支持手册\n资料编号：DEMO-IT-001\n版本：1.0\n## 账号恢复\n服务台核验身份。\n## 设备维修\n资产编号和故障时间。\n## 账号说明\n账号恢复材料未定义。"
    rag = RAGTool()
    rag.model = RoutingEmbedding()
    assert rag.add_documents([document])["success"]
    result = (await rag.execute("账号恢复"))["results"][0]
    assert result["excerpted"]
    assert "资料编号：DEMO-IT-001" in result["content"]
    assert "服务台核验身份" in result["content"]
    assert "账号恢复材料未定义" in result["content"]
    assert "资产编号" not in result["content"]
    assert result["source_id"] == "KB-" + hashlib.sha256(document.encode()).hexdigest()[:16]
    assert rag.documents == [document]
