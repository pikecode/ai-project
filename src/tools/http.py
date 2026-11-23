"""HTTP API tools."""

import asyncio
import ipaddress
import json
import os
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.tools.base import BaseTool, registry


class HTTPRequestTool(BaseTool):
    """Tool for making HTTP requests."""

    name = "http_request"
    description = "Make HTTP GET, POST, PUT, DELETE requests to external APIs"
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to request",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "default": "GET",
                "description": "HTTP method",
            },
            "headers": {
                "type": "object",
                "description": "Request headers",
            },
            "params": {
                "type": "object",
                "description": "Query parameters",
            },
            "body": {
                "type": "object",
                "description": "Request body (for POST/PUT/PATCH)",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds",
                "default": 30,
            },
        },
        "required": ["url"],
    }

    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        params: dict | None = None,
        body: dict | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Execute HTTP request."""
        try:
            parsed = urlsplit(url)
            allowed = {
                host.strip().lower()
                for host in os.getenv("HTTP_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            }
            if (
                parsed.scheme != "https"
                or parsed.hostname not in allowed
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):
                raise ValueError("HTTPS host is not in HTTP_ALLOWED_HOSTS")
            addresses = await asyncio.to_thread(
                socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM
            )
            if not addresses or any(
                not ipaddress.ip_address(item[4][0]).is_global for item in addresses
            ):
                raise ValueError("Private and reserved destinations are not allowed")
            method = method.upper()
            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"} or not 1 <= timeout <= 60:
                raise ValueError("Invalid HTTP method or timeout")
            async with httpx.AsyncClient(
                timeout=timeout, trust_env=False, follow_redirects=False
            ) as client:
                request_kwargs = {
                    # 固定已验证的 IP，TLS 仍校验原域名，避免再次 DNS 解析绕过地址检查。
                    "url": httpx.URL(url).copy_with(host=addresses[0][4][0]),
                    "headers": {**(headers or {}), "Host": parsed.hostname},
                    "extensions": {"sni_hostname": parsed.hostname},
                    "params": params,
                }
                if body and method in ["POST", "PUT", "PATCH"]:
                    request_kwargs["json"] = body

                async with client.stream(method, **request_kwargs) as response:
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1024 * 1024:
                            raise ValueError("HTTP response exceeds 1 MiB")
                        chunks.append(chunk)
                    response = httpx.Response(
                        response.status_code, headers=response.headers, content=b"".join(chunks)
                    )

                result = {
                    "success": 200 <= response.status_code < 300,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                }

                # Try to parse as JSON
                try:
                    result["data"] = response.json()
                except json.JSONDecodeError:
                    result["text"] = response.text[:10000]

                return result
        except httpx.TimeoutException:
            return {"success": False, "error": f"Request timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": f"Request failed: {e!s}"}


def init_http_tools() -> None:
    """Initialize and register HTTP tools."""
    registry.register(HTTPRequestTool())
