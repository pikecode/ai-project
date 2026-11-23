"""在数据库只读事务中执行有界查询。"""

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.models.database import get_database_url

from .base import BaseTool, registry


class SQLQueryTool(BaseTool):
    name = "sql_query"
    description = "Execute a SELECT query in a read-only transaction (maximum 1000 rows)"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": ["query"],
    }

    def __init__(self, database_url: str):
        self.engine = create_async_engine(get_database_url(database_url), pool_pre_ping=True)

    def _is_safe(self, query: str) -> bool:
        return query.strip().lower().startswith("select ") and ";" not in query

    async def execute(self, query: str, limit: int = 100) -> dict[str, Any]:
        if not self._is_safe(query) or not 1 <= limit <= 1000:
            return {
                "success": False,
                "error": "Expected a single SELECT and limit between 1 and 1000",
            }
        try:
            async with asyncio.timeout(30):
                async with self.engine.connect() as conn, conn.begin():
                    if self.engine.dialect.name == "sqlite":
                        await conn.execute(text("PRAGMA query_only = ON"))
                    else:
                        await conn.execute(text("SET TRANSACTION READ ONLY"))
                        await conn.execute(text("SET LOCAL statement_timeout = '30s'"))
                    result = await conn.execute(
                        text(f"SELECT * FROM ({query}) AS query_result LIMIT :row_limit"),
                        {"row_limit": limit},
                    )
                    rows = [dict(row) for row in result.mappings()]
                    return {
                        "success": True,
                        "columns": list(result.keys()),
                        "rows": rows,
                        "row_count": len(rows),
                    }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def init_sql_tool(database_url: str) -> None:
    registry.register(SQLQueryTool(database_url))
