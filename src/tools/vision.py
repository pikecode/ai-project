"""使用统一模型配置分析工作目录中的图片。"""

import base64

import httpx

from src.config import Config, workspace_path

from .base import BaseTool, registry


class VisionTool(BaseTool):
    name = "vision"
    description = "Analyze an image within WORKSPACE_DIR"
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "question": {"type": "string"},
        },
        "required": ["source"],
    }

    async def execute(self, source=None, image_url=None, question="Describe this image."):
        try:
            path = workspace_path(source or image_url or "")
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("Image exceeds 10 MiB")
            config = Config.from_env()
            async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                response = await client.post(
                    config.llm_base_url.rstrip("/") + "/chat/completions",
                    json={
                        "model": config.vision_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": question},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "data:image/png;base64,"
                                            + base64.b64encode(path.read_bytes()).decode()
                                        },
                                    },
                                ],
                            }
                        ],
                        "stream": False,
                    },
                )
                response.raise_for_status()
                return {
                    "success": True,
                    "description": response.json()["choices"][0]["message"]["content"],
                    "model": config.vision_model,
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def init_vision_tool():
    registry.register(VisionTool())
