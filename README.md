# AI Project

本仓库包含一个本地 AI 助手平台，应用源码、运行说明和开发文档位于 `agent-platform/`。

## 快速开始

```bash
cd agent-platform
./start.sh setup
./start.sh model
./start.sh start
```

启动后访问 `http://127.0.0.1:8653`。首次设置和模型下载需要联网及较多磁盘空间。环境要求、配置、开发与测试命令请查看 [应用 README](agent-platform/README.md)。

## 项目目录

- `agent-platform/src/agent/`：LangGraph 执行图与智能体逻辑。
- `agent-platform/src/api/`：FastAPI 接口。
- `agent-platform/src/models/`：数据库访问。
- `agent-platform/src/tools/`：智能体工具。
- `agent-platform/frontend/`：Go 页面服务与前端资源。
- `agent-platform/tests/`：Python 测试。
- `agent-platform/docs/`：部署、使用和验收文档。

运行数据库、上传内容、知识库、环境变量文件和日志均为本地数据，不应提交到 Git。分支与提交约定见 [AGENTS.md](AGENTS.md)。
