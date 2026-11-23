"""Web search tool using DuckDuckGo (free, no API key)."""

from typing import Any

import httpx

from .base import BaseTool


class WebSearchTool(BaseTool):
    """Search the web for information."""

    name = "web_search"
    description = (
        "Search the web for information. Input: search query and optional max results (default 5)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """Search web."""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                # DuckDuckGo instant answer API
                url = "https://api.duckduckgo.com/"
                params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                results = []

                # RelatedTopics for general results
                for topic in data.get("RelatedTopics", [])[:max_results]:
                    if text := topic.get("Text"):
                        results.append(
                            {
                                "title": topic.get("Text", "")[:100],
                                "url": topic.get("FirstURL", ""),
                                "snippet": text[:200],
                            }
                        )

                # Abstract for Wikipedia-style answers
                if abstract := data.get("AbstractText"):
                    results.insert(
                        0,
                        {
                            "title": data.get("Heading", query),
                            "url": data.get("AbstractURL", ""),
                            "snippet": abstract[:300],
                        },
                    )

                return {
                    "success": True,
                    "query": query,
                    "answer": data.get("Answer", ""),
                    "results": results[:max_results],
                }
        except Exception as e:
            return {"success": False, "error": str(e)}


def init_web_search_tool():
    """Register web search tool."""
    from .base import registry

    registry.register(WebSearchTool())
