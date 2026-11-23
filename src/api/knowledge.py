"""文件预览、确认导入和引用原文；预览不写入知识库。"""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from src.tools.pdf import PDFTool
from src.tools.rag import KnowledgeBase, get_knowledge_tool

router = APIRouter(prefix="/knowledge")
PREVIEW_KEY = secrets.token_bytes(32)
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 200000


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(max_length=255)
    title: str = Field(max_length=120)
    version: str = Field(max_length=80)
    part: int = Field(ge=1, le=100)
    parts: int = Field(ge=1, le=100)
    page: int | None = Field(default=None, ge=1, le=200)


class ImportPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=20000)
    metadata: SourceMetadata


class PreviewPayload(BaseModel):
    knowledge_base: KnowledgeBase = "daily"
    model_config = ConfigDict(extra="forbid")
    parts: list[ImportPart] = Field(min_length=1, max_length=100)
    expires_at: int
    warnings: list[str] = Field(default_factory=list, max_length=200)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: PreviewPayload
    signature: str = Field(min_length=64, max_length=64)


def knowledge(knowledge_base="daily"):
    tool = get_knowledge_tool(knowledge_base)
    if tool is None:
        raise HTTPException(503, "知识库尚未就绪")
    return tool


def sign(payload):
    encoded = json.dumps(payload.model_dump(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(PREVIEW_KEY, encoded, hashlib.sha256).hexdigest()


def parse_preview(filename, data, title, version):
    warnings = []
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "PDF 文件签名无效")
        try:
            with tempfile.TemporaryDirectory(prefix="knowledge-preview-") as folder:
                path = Path(folder) / "document.pdf"
                path.write_bytes(data)
                extracted = PDFTool()._extract(path, 200)
        except Exception as exc:
            raise HTTPException(400, "无法读取 PDF，请检查是否损坏或加密") from exc
        if extracted["truncated"]:
            raise HTTPException(413, "PDF 超出 200 页或 20 万字符，请拆分后导入；未导入任何内容")
        blocks = [(item["page"], item["text"]) for item in extracted["pages"]]
        missing = extracted.get("total_pages", len(blocks)) - len(blocks)
        if missing:
            warnings.append(
                f"PDF 有 {missing} 页未提取到文字，可能为空白页或扫描页；这些页不会导入，请核对原件。"
            )
    else:
        try:
            text = data.decode("utf-8-sig").replace("\r\n", "\n")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "TXT 和 Markdown 请使用 UTF-8 编码") from exc
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise HTTPException(400, "资料含有二进制控制字符，请提供文本文件")
        blocks = [(None, text)]
    if not any(text.strip() for _, text in blocks):
        raise HTTPException(400, "没有可导入文字；扫描 PDF 暂不支持 OCR")
    if sum(len(text) for _, text in blocks) > MAX_TEXT_CHARS:
        raise HTTPException(413, "提取文字超过 20 万字符，请拆分文件后导入")
    first_text = blocks[0][1]
    heading = re.search(r"^#\s+(.+)$", first_text, re.MULTILINE)
    detected_version = re.search(r"^版本[：:]\s*([^\n]+)", first_text, re.MULTILINE)
    title = " ".join(title.split()) or (heading[1].strip() if heading else Path(filename).stem)
    version = " ".join(version.split()) or (
        detected_version[1].strip() if detected_version else "未注明"
    )
    title, version = title[:120], version[:80]
    chunks = [
        (page, text[start : start + 6000])
        for page, text in blocks
        if text.strip()
        for start in range(0, len(text), 6000)
    ]
    if len(chunks) > 100:
        raise HTTPException(413, "资料超过 100 个片段，请拆分 PDF 或文件后导入")
    parts = []
    for index, (page, text) in enumerate(chunks, 1):
        prefix = f"# {title}\n文件名：{filename}\n版本：{version}\n片段：{index}/{len(chunks)}\n"
        if page is not None:
            prefix += f"[第{page}页]\n"
        parts.append(
            ImportPart(
                content=prefix + "\n" + text,
                metadata=SourceMetadata(
                    filename=filename,
                    title=title,
                    version=version,
                    part=index,
                    parts=len(chunks),
                    page=page,
                ),
            )
        )
    return PreviewPayload(parts=parts, expires_at=int(time.time()) + 1800, warnings=warnings)


@router.post("/preview")
async def preview_file(
    file: UploadFile = File(...),
    title: str = Form(default="", max_length=120),
    version: str = Form(default="", max_length=80),
    knowledge_base: KnowledgeBase = Query(default="daily"),
):
    try:
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename):
            raise HTTPException(400, "文件名无效或过长")
        if Path(filename).suffix.lower() not in {".pdf", ".txt", ".md", ".markdown"}:
            raise HTTPException(400, "请选择 Markdown、TXT 或 PDF 文件")
        data = bytearray()
        while chunk := await file.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(413, "文件超过 50 MiB")
        payload = await asyncio.to_thread(parse_preview, filename, bytes(data), title, version)
        payload.knowledge_base = knowledge_base
        return {"payload": payload.model_dump(), "signature": sign(payload)}
    finally:
        await file.close()


@router.post("/import")
async def confirm_import(
    request: ImportRequest, knowledge_base: KnowledgeBase = Query(default="daily")
):
    if not hmac.compare_digest(sign(request.payload), request.signature):
        raise HTTPException(400, "预览已失效或内容发生变化，请重新生成预览")
    if request.payload.expires_at <= int(time.time()):
        raise HTTPException(400, "预览已过期，请重新生成预览")
    if request.payload.knowledge_base != knowledge_base:
        raise HTTPException(409, "预览所属知识库与当前库不符，请重新预览")
    result = await asyncio.to_thread(
        knowledge(knowledge_base).add_documents,
        [part.content for part in request.payload.parts],
        [part.metadata.model_dump() for part in request.payload.parts],
        True,
    )
    if not result.get("success"):
        raise HTTPException(503, "导入未完成，请检查知识库服务后重试；保留当前预览即可")
    return result


@router.get("/sources/{source_id}")
async def source_content(source_id: str, knowledge_base: KnowledgeBase = Query(default="daily")):
    if not re.fullmatch(r"KB-[a-f0-9]{16}", source_id):
        raise HTTPException(400, "资料引用标识无效")
    try:
        source = await asyncio.to_thread(knowledge(knowledge_base).get_source, source_id)
    except Exception as exc:
        raise HTTPException(503, "暂时无法读取原文，请检查知识库服务") from exc
    if source is None:
        raise HTTPException(404, "当前知识库中没有这条引用，可能来自其他知识库或资料已不可用")
    return source
