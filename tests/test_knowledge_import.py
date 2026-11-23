"""预览不入库、确认内容一致、原文可追溯，以及旧资料兼容。"""

import copy
import hashlib
import io
import json

import numpy as np
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.api import knowledge as api
from src.tools.base import registry
from src.tools.rag import RAGTool


class Embedding:
    def encode(self, texts, **kwargs):
        return np.array([[1.0, 0.0] for _ in texts], dtype=np.float32)


def text_pdf(pages=1):
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 10 100 Td (PDF page {index + 1}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def test_preview_confirm_persistence_and_duplicate_retry(client):
    rag = registry.get("knowledge_search")
    rag.model = Embedding()
    text = "# 报销说明\n版本：1.2\n\n## 审批\n800元由部门负责人审批。"
    response = await client.post("/knowledge/preview", files={"file": ("说明.md", text.encode())})
    assert response.status_code == 200
    preview = response.json()
    part = preview["payload"]["parts"][0]
    assert part["metadata"]["filename"] == "说明.md"
    assert part["metadata"]["version"] == "1.2"
    assert "## 审批\n800元" in part["content"]
    assert (await client.get("/knowledge/stats")).json()["total_documents"] == 0
    assert not rag.store_path.exists()
    imported = await client.post("/knowledge/import", json=preview)
    assert imported.status_code == 200 and imported.json()["added"] == 1
    duplicate = await client.post("/knowledge/import", json=preview)
    assert duplicate.json()["added"] == 0
    source_id = "KB-" + hashlib.sha256(part["content"].encode()).hexdigest()[:16]
    source = (await client.get("/knowledge/sources/" + source_id)).json()
    assert source["content"] == part["content"]
    assert source["original_content"] == text
    assert source["metadata"]["title"] == "报销说明"
    assert RAGTool().get_source(source_id) == source
    assert (await client.get("/knowledge/sources/KB-aaaaaaaaaaaaaaaa")).status_code == 404
    assert (await client.get("/knowledge/sources/invalid")).status_code == 400


async def test_tampered_expired_and_restarted_preview_are_rejected(client, monkeypatch):
    preview = (
        await client.post("/knowledge/preview", files={"file": ("a.txt", b"content")})
    ).json()
    tampered = copy.deepcopy(preview)
    tampered["payload"]["parts"][0]["content"] = "altered"
    assert (await client.post("/knowledge/import", json=tampered)).status_code == 400
    expired = copy.deepcopy(preview)
    expired["payload"]["expires_at"] = 0
    expired["signature"] = api.sign(api.PreviewPayload(**expired["payload"]))
    assert (await client.post("/knowledge/import", json=expired)).status_code == 400
    monkeypatch.setattr(api, "PREVIEW_KEY", b"new process key")
    assert (await client.post("/knowledge/import", json=preview)).status_code == 400
    assert (await client.get("/knowledge/stats")).json()["total_documents"] == 0


@pytest.mark.parametrize(
    "filename,content",
    [
        ("bad.exe", b"x"),
        ("fake.pdf", b"not pdf"),
        ("bad.txt", b"\xff"),
        ("binary.txt", b"hello\x00world"),
        ("empty.md", b"  "),
    ],
)
async def test_bad_files_never_enter_knowledge(client, filename, content):
    response = await client.post("/knowledge/preview", files={"file": (filename, content)})
    assert response.status_code == 400
    assert (await client.get("/knowledge/stats")).json()["total_documents"] == 0


async def test_preview_limits_and_pdf_page_provenance(client, monkeypatch):
    response = await client.post(
        "/knowledge/preview", files={"file": ("report.pdf", text_pdf(2))}, data={"version": "2026"}
    )
    assert response.status_code == 200
    parts = response.json()["payload"]["parts"]
    assert len(parts) == 2
    assert parts[1]["metadata"]["page"] == 2
    assert "[第2页]" in parts[1]["content"] and "PDF page 2" in parts[1]["content"]
    monkeypatch.setattr(api, "MAX_FILE_BYTES", 5)
    assert (
        await client.post("/knowledge/preview", files={"file": ("a.md", b"123456")})
    ).status_code == 413


async def test_oversized_or_blank_pdf_is_not_silently_truncated(client, monkeypatch):
    def extract(*args):
        return {"truncated": True, "pages": [{"page": 1, "text": "partial"}]}

    monkeypatch.setattr(api.PDFTool, "_extract", extract)
    response = await client.post("/knowledge/preview", files={"file": ("report.pdf", b"%PDF-test")})
    assert response.status_code == 413
    monkeypatch.setattr(api.PDFTool, "_extract", lambda *args: {"truncated": False, "pages": []})
    response = await client.post("/knowledge/preview", files={"file": ("scan.pdf", b"%PDF-test")})
    assert response.status_code == 400 and "OCR" in response.json()["detail"]


async def test_old_json_without_metadata_keeps_ids_and_content(client):
    rag = registry.get("knowledge_search")
    rag.data_dir.mkdir(parents=True, exist_ok=True)
    rag.store_path.write_text(
        json.dumps({"documents": ["旧资料原文"], "vectors": [[1.0, 0.0]]}), encoding="utf-8"
    )
    source_id = "KB-" + hashlib.sha256("旧资料原文".encode()).hexdigest()[:16]
    before = rag.store_path.read_bytes()
    source = await client.get("/knowledge/sources/" + source_id)
    assert source.json()["content"] == "旧资料原文"
    assert rag.store_path.read_bytes() == before
    rag.model = Embedding()
    preview = (await client.post("/knowledge/preview", files={"file": ("new.txt", b"new")})).json()
    assert (await client.post("/knowledge/import", json=preview)).status_code == 200
    assert RAGTool().get_source(source_id)["content"] == "旧资料原文"


async def test_import_failure_does_not_change_index_or_metadata(client, monkeypatch):
    rag = registry.get("knowledge_search")
    rag.model = Embedding()
    preview = (
        await client.post("/knowledge/preview", files={"file": ("test.txt", b"test")})
    ).json()
    monkeypatch.setattr(
        "src.tools.rag.os.replace", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
    )
    response = await client.post("/knowledge/import", json=preview)
    assert response.status_code == 503
    assert rag.stats()["total_documents"] == 0 and rag.metadata == []
    assert not rag.store_path.exists()


async def test_new_file_search_is_bounded_but_source_keeps_all_text(client):
    rag = registry.get("knowledge_search")
    rag.model = Embedding()
    text = "长文内容。" * 1500
    preview = (
        await client.post("/knowledge/preview", files={"file": ("long.txt", text.encode())})
    ).json()
    assert (await client.post("/knowledge/import", json=preview)).status_code == 200
    result = (await rag.execute("长文"))["results"][0]
    assert result["excerpted"] and len(result["content"].encode()) < 2400
    source = rag.get_source(result["source_id"])
    assert len(source["content"]) > len(result["content"])


async def test_preview_body_is_limited_before_multipart_parser(client, monkeypatch):
    from src.api import main

    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)

    async def body():
        yield b"x" * (1024 * 1024 + 11)

    response = await client.post(
        "/knowledge/preview",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=test"},
    )
    assert response.status_code == 413


async def test_mixed_pdf_warns_about_pages_without_text_before_confirmation(client, monkeypatch):
    monkeypatch.setattr(
        api.PDFTool,
        "_extract",
        lambda *args: {
            "truncated": False,
            "total_pages": 2,
            "pages": [{"page": 1, "text": "readable"}],
        },
    )
    response = await client.post("/knowledge/preview", files={"file": ("mixed.pdf", b"%PDF-test")})
    assert response.status_code == 200
    assert "1 页未提取到文字" in response.json()["payload"]["warnings"][0]
    assert (await client.get("/knowledge/stats")).json()["total_documents"] == 0
