import io

from pypdf import PdfWriter

from src.api import main
from src.tools.pdf import PDFTool


def blank_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


async def test_upload_is_stored_in_backend_workspace(client, tmp_path):
    response = await client.post(
        "/files/upload", files={"file": ("说明.pdf", blank_pdf(), "application/pdf")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "说明.pdf"
    path = tmp_path / data["file_url"]
    assert path.exists()
    assert path.name != "说明.pdf"
    result = await PDFTool().execute(source=data["file_url"])
    assert result["success"] and result["total_pages"] == 1
    assert not (await PDFTool().execute(source="/etc/passwd"))["success"]
    assert not (await PDFTool().execute(source="https://example.com/a.pdf"))["success"]


async def test_upload_rejects_bad_signature_and_size(client, monkeypatch, tmp_path):
    bad = await client.post("/files/upload", files={"file": ("bad.pdf", b"not a pdf")})
    assert bad.status_code == 400
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    large = await client.post("/files/upload", files={"file": ("large.pdf", b"%PDF-" + b"x" * 20)})
    assert large.status_code == 413
    assert not list((tmp_path / "uploads").glob("*"))


async def test_upload_body_limit_before_multipart_parse(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)

    async def body():
        yield b"x" * (1024 * 1024 + 11)

    response = await client.post(
        "/files/upload",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=test"},
    )
    assert response.status_code == 413


async def test_attachment_preserves_question_and_conversation(client, monkeypatch):
    async def extract(self, **kwargs):
        return {"success": True, "text": "参考资料内容", "truncated": False}

    monkeypatch.setattr(PDFTool, "execute", extract)
    cid = (await client.post("/chat", json={"message": "先聊一下"})).json()["conversation_id"]
    response = await client.post(
        "/chat/stream",
        json={
            "message": "这份文件的结论是什么？",
            "conversation_id": cid,
            "attachment": "uploads/sample.pdf",
        },
    )
    assert response.status_code == 200
    assert '"tool": "pdf_extract"' in response.text
    content = client.fake_graph.inputs[-1].messages[-1].content
    assert "这份文件的结论是什么？" in content and "参考资料内容" in content
    assert len((await client.get("/conversations")).json()) == 1


async def test_blank_pdf_requires_ocr(client):
    uploaded = await client.post("/files/upload", files={"file": ("blank.pdf", blank_pdf())})
    response = await client.post(
        "/chat", json={"message": "提取文字", "attachment": uploaded.json()["file_url"]}
    )
    assert response.status_code == 400
    assert "OCR" in response.json()["detail"]
    assert (await client.get("/conversations")).json() == []


async def test_real_pdf_text_reaches_chat_model(client):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
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
    content.set_data(b"BT /F1 12 Tf 10 100 Td (Important PDF conclusion) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    uploaded = await client.post("/files/upload", files={"file": ("text.pdf", buffer.getvalue())})
    response = await client.post(
        "/chat", json={"message": "请解释结论", "attachment": uploaded.json()["file_url"]}
    )
    assert response.status_code == 200
    assert "pdf_extract" in response.json()["tools_used"]
    message = client.fake_graph.inputs[-1].messages[-1].content
    assert "Important PDF conclusion" in message and "请解释结论" in message


async def test_attachment_history_displays_question_instead_of_internal_passages(
    client, monkeypatch
):
    async def extract(self, **kwargs):
        return {"success": True, "text": "参考资料内容", "truncated": False}

    monkeypatch.setattr(PDFTool, "execute", extract)
    response = await client.post(
        "/chat", json={"message": "用户的问题", "attachment": "uploads/test.pdf"}
    )
    cid = response.json()["conversation_id"]
    history = (await client.get(f"/conversations/{cid}/messages")).json()
    assert history[0]["content"] == "用户的问题"
    assert history[0]["attachment"] == "uploads/test.pdf"
    assert "参考资料内容" in client.fake_graph.inputs[-1].messages[-1].content


async def test_attachment_retrieval_failure_is_actionable_and_saves_no_message(client, monkeypatch):
    async def extract(self, **kwargs):
        return {"success": True, "text": "参考资料", "truncated": False}

    async def unavailable(*args):
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr(PDFTool, "execute", extract)
    monkeypatch.setattr(main, "attachment_context", unavailable)
    response = await client.post(
        "/chat", json={"message": "读取附件", "attachment": "uploads/test.pdf"}
    )
    assert response.status_code == 503
    assert "嵌入模型" in response.json()["detail"]
    assert (await client.get("/conversations")).json() == []
