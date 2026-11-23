"""解析工作目录中的 PDF，不访问任意 URL 或系统文件。"""

import asyncio
from typing import Any

from pypdf import PdfReader

from src.config import workspace_path

from .base import BaseTool, registry


class PDFTool(BaseTool):
    name = "pdf_extract"
    description = "Extract text from an uploaded PDF within WORKSPACE_DIR"
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Uploaded PDF path"},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["source"],
    }

    async def execute(
        self, source: str = None, file_path: str = None, max_pages: int = 10
    ) -> dict[str, Any]:
        try:
            path = workspace_path(source or file_path or "")
            if path.suffix.lower() != ".pdf" or not 1 <= max_pages <= 200:
                raise ValueError("Expected a PDF and max_pages between 1 and 200")
            return await asyncio.to_thread(self._extract, path, max_pages)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _extract(self, path, max_pages):
        reader = PdfReader(path)
        pages = reader.pages[:max_pages]
        extracted = []
        remaining = 200000
        processed = 0
        for i, page in enumerate(pages):
            processed = i + 1
            content = (page.extract_text() or "").strip()
            if content:
                extracted.append({"page": i + 1, "text": content[:remaining]})
                remaining -= len(content)
            if remaining <= 0:
                break
        text = "\n\n".join(f"[第{item['page']}页]\n{item['text']}" for item in extracted)
        return {
            "success": True,
            "total_pages": len(reader.pages),
            "pages_extracted": processed,
            "text": text,
            "pages": extracted,
            "truncated": remaining <= 0 or len(reader.pages) > max_pages,
        }


def init_pdf_tool():
    registry.register(PDFTool())
