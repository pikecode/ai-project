"""应用配置。"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3.5:4b"
    context_tokens: int = 16384
    output_tokens: int = 1024
    agent_profile: str = "general"
    vision_model: str = "llava"
    database_url: str = "sqlite:///./agent.db"
    workspace_dir: str = "./uploads"
    api_token: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_workers: int = 1

    @classmethod
    def from_env(cls) -> "Config":
        values = {}
        for name, field in cls.__dataclass_fields__.items():
            value = os.getenv(name.upper(), field.default)
            if isinstance(field.default, bool):
                value = str(value).lower() == "true"
            elif isinstance(field.default, int):
                value = int(value)
            values[name] = value
        return cls(**values)


def workspace_path(source: str) -> Path:
    """把工具文件访问限制在工作目录内，包括符号链接的目标。"""
    root = Path(Config.from_env().workspace_dir).resolve()
    path = Path(source)
    if not path.is_absolute():
        if path.parts and path.parts[0] == "uploads":
            path = Path(*path.parts[1:])
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("File access is limited to WORKSPACE_DIR")
    return path
