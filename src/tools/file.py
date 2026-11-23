"""File processing tools."""

import asyncio
import json
import os
import tempfile
from typing import Any

import pandas as pd

from src.config import workspace_path
from src.tools.base import BaseTool, registry


class FileReadTool(BaseTool):
    """Tool for reading various file formats."""

    name = "read_file"
    description = "Read CSV, Excel, JSON, or text files and return structured data"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path within WORKSPACE_DIR",
            },
            "file_type": {
                "type": "string",
                "enum": ["csv", "excel", "json", "text"],
                "description": "File type (auto-detected if not specified)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum rows to read (default 1000)",
                "default": 1000,
            },
        },
        "required": ["file_path"],
    }

    async def execute(self, file_path: str, file_type: str | None = None, limit: int = 1000):
        return await asyncio.to_thread(self._execute, file_path, file_type, limit)

    def _execute(
        self, file_path: str, file_type: str | None = None, limit: int = 1000
    ) -> dict[str, Any]:
        """Read file and return structured data."""
        try:
            path = workspace_path(file_path)
            file_path = str(path)
            if path.is_file() and path.stat().st_size > 50 * 1024 * 1024:
                raise ValueError("File exceeds 50 MiB")
            if not path.exists():
                return {"success": False, "error": f"File not found: {file_path}"}

            if not 1 <= limit <= 1000:
                raise ValueError("limit must be between 1 and 1000")

            # Auto-detect file type
            if file_type is None:
                ext = path.suffix.lower()
                if ext == ".csv":
                    file_type = "csv"
                elif ext in [".xlsx", ".xls"]:
                    file_type = "excel"
                elif ext == ".json":
                    file_type = "json"
                else:
                    file_type = "text"

            # Read based on type
            if file_type == "csv":
                df = pd.read_csv(file_path, nrows=limit)
                return {
                    "success": True,
                    "type": "csv",
                    "columns": df.columns.tolist(),
                    "rows": df.head(limit).to_dict("records"),
                    "shape": df.shape,
                    "dtypes": {k: str(v) for k, v in df.dtypes.items()},
                }
            elif file_type == "excel":
                df = pd.read_excel(file_path, nrows=limit)
                return {
                    "success": True,
                    "type": "excel",
                    "columns": df.columns.tolist(),
                    "rows": df.head(limit).to_dict("records"),
                    "shape": df.shape,
                }
            elif file_type == "json":
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {
                    "success": True,
                    "type": "json",
                    "data": data if isinstance(data, list) else [data],
                }
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read(10001)
                return {
                    "success": True,
                    "type": "text",
                    "content": content[:10000],
                    "truncated": len(content) > 10000,
                    "length": len(content),
                }
        except Exception as e:
            return {"success": False, "error": f"Failed to read file: {e!s}"}


class FileWriteTool(BaseTool):
    """Tool for writing data to files."""

    name = "write_file"
    description = "Write data to CSV, Excel, JSON, or text files"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path within WORKSPACE_DIR",
            },
            "data": {
                "type": "object",
                "description": "Data to write (for JSON) or rows (for CSV/Excel)",
            },
            "file_type": {
                "type": "string",
                "enum": ["csv", "excel", "json", "text"],
                "description": "File type",
            },
            "content": {
                "type": "string",
                "description": "Text content (for text files)",
            },
        },
        "required": ["file_path", "file_type"],
    }

    async def execute(self, file_path, file_type, data=None, content=None):
        return await asyncio.to_thread(self._execute, file_path, file_type, data, content)

    def _execute(
        self,
        file_path: str,
        file_type: str,
        data: list[dict] | dict | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Write data to file."""
        try:
            if file_type not in {"csv", "excel", "json", "text"}:
                raise ValueError("Unsupported file type")
            path = workspace_path(file_path)
            file_path = str(path)
            if path.exists():
                raise ValueError("Refusing to overwrite an existing file")
            path.parent.mkdir(parents=True, exist_ok=True)

            if file_type in {"csv", "excel"} and data is None:
                raise ValueError("Data required for CSV/Excel")
            suffix = ".xlsx" if file_type == "excel" else ".tmp"
            with tempfile.NamedTemporaryFile(
                dir=path.parent, suffix=suffix, delete=False
            ) as target:
                temporary = path.parent / target.name
            try:
                if file_type == "csv":
                    pd.DataFrame(data).to_csv(temporary, index=False)
                elif file_type == "excel":
                    pd.DataFrame(data).to_excel(temporary, index=False)
                elif file_type == "json":
                    temporary.write_text(
                        json.dumps(data if data is not None else {}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                else:
                    temporary.write_text(content or "", encoding="utf-8")
                # 完整写入后原子创建目标；并发请求也不能覆盖已有文件。
                os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

            return {
                "success": True,
                "file_path": str(path.absolute()),
                "size": path.stat().st_size,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to write file: {e!s}"}


def init_file_tools() -> None:
    """Initialize and register file tools."""
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
