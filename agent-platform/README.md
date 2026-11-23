# Agent Platform

本地 AI 助手：Go 提供页面与代理，FastAPI 保存会话，LangGraph 使用原生工具调用执行任务。当前本地稳定版固定 Qwen3.5 4B 与独立的 Ollama 0.40.6，不覆盖系统 Ollama。

## 本地启动

已配置的本机可直接运行：

```bash
cd agent-platform
./start.sh start
./start.sh status
```

页面：`http://127.0.0.1:8653`。首次安装（当前固定包适用于 Apple Silicon macOS，验收使用 Python 3.14）：

```bash
./start.sh setup       # 校验并安装应用专属 Ollama，创建虚拟环境和本地配置
./start.sh model       # 下载模型并核对固定摘要
# 如需导入旧数据，在首次 start 前执行；目标 var/ 必须不存在
./start.sh import --database agent.db --uploads uploads --knowledge data
./start.sh start
```

安装和模型首次加载需要联网及较多磁盘空间。`setup` 不修改已有 `.env` 或系统安装；本地版专用 `.env.local` 优先级最高，权限为 600，不提交到版本控制。普通环境变量优先于旧 `.env`，但不能覆盖本地专用配置。配置仅支持单行 `KEY=VALUE`，不执行 shell 表达式。

## 日常管理

```bash
./start.sh stop        # 仅停止本脚本创建且身份匹配的进程
./start.sh logs        # 查看管理器、API、前端和模型日志末尾
./start.sh stop
./start.sh backup      # 服务停止后对持久数据做一致性快照
./start.sh start
```

- `var/agent.db`：正式 SQLite；`var/uploads/`：附件；`var/knowledge/`：知识库。
- `var/trial/`：业务试用反馈，纳入备份；`var/logs/`：日志；`var/evaluations/`：真实验收结果；`backups/`：备份。
- `.runtime/`：固定 Ollama、前端二进制、模型锁文件和进程记录。
- 进程不是开机自启服务；机器重启后需要重新 start。日志当前追加写入，需要维护者定期归档。

## 使用与能力边界

页面显示当前模型、任务阶段和附件；可以取消等待，失败后保留输入并重试。重试会复用已保存的最后一条用户消息，避免重复写入；若任务已尝试发送邮件、写文件或 HTTP 请求，则禁止一键重试，需要先确认结果。

PDF 最多读取 200 页、20 万字符。长文件按片段检索并标注页码；页面会提示未阅读全文，不能将检索结果视为全文总结。扫描 PDF 暂不支持 OCR。上下文预算按 UTF-8 字节上界保守计算，保留输出空间；旧轮次可从模型上下文移除，但数据库历史不删除。当前问题或工具结果超限会明确报错。

工具参数在执行前按 JSON Schema 校验。文件仅访问工作目录，写入不覆盖现有目标；代码执行保持关闭。通用 HTTP 默认关闭，只有显式授权的公网 HTTPS 域名可访问。

## 目录与开发检查

主要代码：`src/agent/` 执行图与预算，`src/api/` 接口，`src/models/` 数据库，`src/tools/` 工具，`frontend/` 页面与 Go 代理，`scripts/` 运行和验收，`tests/` 回归测试。

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests scripts
.venv/bin/black --check src tests scripts
(cd frontend && go test -race ./... && go vet ./...)
(cd frontend && node --test static/app.test.cjs)
.venv/bin/python scripts/evaluate.py --self-check
.venv/bin/python scripts/evaluate.py
```

真实验收读取固定任务集 `tests/fixtures/local_cases.json`，自动启动临时数据库和附件目录，不向正式知识库写测试资料；结果记录答案、工具、事件耗时和模型服务内存。耗时是单轮观测，不代表稳定延迟或跨模型排名。

参见 [部署、迁移与回退](docs/DEPLOYMENT.md)、[本地稳定版验收](docs/LOCAL_STABLE_ACCEPTANCE.md)。

## 个人业务试用

按 [个人试用清单](docs/PERSONAL_TRIAL.md) 准备脱敏资料和人工标准答案，填写本机 `var/trial/feedback.csv`。15 项目前是待执行模板，不计入已通过的合成验收。备份实际恢复情况见 [恢复演练](docs/RESTORE_DRILL.md)。

内部资料问答助手的启用、资料准备和权限边界见 [使用指南](docs/KNOWLEDGE_AGENT.md)。

资料文件预览导入、版本标记与点击引用查看原文，见 [使用说明](docs/KNOWLEDGE_IMPORT.md)。

资料文件导入和原文核对见 [资料导入说明](docs/KNOWLEDGE_IMPORT.md)。页面顶部可切换日常库与测试库，演示资料建议导入测试库；两库的资料、检索引用和会话分开，原有资料仍在日常库。验证记录见 [两库验收](docs/KNOWLEDGE_LIBRARIES_ACCEPTANCE.md)。

## 结对学习教程

从简单Python阅读逐步学习工具调用、Skill任务规范与MCP外部工具接入，见[教程路线](docs/tutorials/README.md)。包含首课练习、个人学习记录和可用于指导其他学习者的课程模板；后续课程随实践补齐。
