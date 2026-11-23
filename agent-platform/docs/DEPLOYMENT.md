# 部署、数据迁移与回退

## 本地稳定版

本地管理命令见 README。固定运行包目前用于 Apple Silicon macOS，安装包 SHA-256 和模型摘要由 `scripts/local.py` 固定，实际记录在 `.runtime/model-lock.json`。下载结果不匹配时拒绝激活，不静默切换到新模型。升级版本或模型摘要后必须重新执行固定验收集。

`requirements-local.txt` 固定本机验收的直接依赖版本，不是完整的跨平台依赖锁文件。其他平台可按 `pyproject.toml` 安装并手动运行 API、Go 前端和兼容模型服务；不能直接使用本机固定运行包。

## 配置和监听

本地管理器读取 `.env`，然后应用进程环境，最后使用 `.env.local` 的专用设置。`.env` 和 `.env.local` 中的值不会作为 shell 执行，复杂 shell 表达式须改成实际值。

| 配置 | 作用 |
|---|---|
| `LLM_BASE_URL` / `LLM_MODEL` | 本地版使用 11435 端口和固定模型别名 |
| `CONTEXT_TOKENS` / `OUTPUT_TOKENS` | 当前为 16384 / 1024，预算采用保守字节上界 |
| `DATABASE_URL` / `WORKSPACE_DIR` / `RAG_DATA_DIR` | 本地版指向 `var/` 下的绝对持久路径 |
| `API_TOKEN` | 本地安装生成的 Bearer 令牌，由代理传递，不写进浏览器脚本 |
| `API_HOST` / `API_PORT` | 默认 127.0.0.1:8000 |
| `HOST` / `PORT` | 默认 127.0.0.1:8653 |
| `WEB_USERNAME` / `WEB_PASSWORD` | 共享访问的 Basic 认证，非回环监听必须设置密码 |
| `HTTP_ALLOWED_HOSTS` | 通用 HTTP 的显式允许域名，默认空 |
| `SMTP_*` | 可选邮件配置；无 SMTP_HOST 时不注册邮件工具 |

本地管理器启动独立 Ollama，并将 `OLLAMA_CONTEXT_LENGTH` 设为与预算一致。手动使用外部模型服务时，维护者必须保证实际上下文不小于 `CONTEXT_TOKENS`；标称最大上下文不等于服务当前启用值。

当前知识库只支持单个 API 进程，命令行也须使用 `--workers 1`。端口已被其他进程占用时启动失败，不会自动终止占用者。

## 迁移旧数据

本次已将原配置指向的应用根目录数据库及其附件、知识库导入 `var/`。原配置数据库包含 63 个会话、122 条消息，导入后计数与完整性检查一致。仓库根目录和旧前端目录的另两份数据库未自动合并。

`import` 只能用于不存在的 `var/` 目标，先使用 SQLite 备份 API 生成一致性快照，再复制到目标，源文件不修改。目录中的符号链接不跟随、不复制，原链接保留，跳过清单写入快照；本次旧附件目录有一个悬空链接。

```bash
./start.sh stop
./start.sh import --database /绝对路径/agent.db --uploads /绝对路径/uploads --knowledge /绝对路径/data
```

若 `var/` 已存在，命令拒绝覆盖。需要恢复或重新迁移时，先停止服务，将当前 `var/` 整体改名保留，再从确认的备份导入；不要混合两份数据库或直接覆盖运行中的 SQLite。恢复后执行完整性检查并核对会话数和附件。

备份和导入要求本管理器停止。还存在手动启动的写入进程时，维护者也必须先停止它们。备份包含数据库、附件、知识库和 `trial/` 试用反馈，不包含日志、模型权重、虚拟环境或凭据；`.env`、`.env.local` 应由维护者另行安全保存。备份包含用户资料，禁止公开或提交到版本控制。

## 数据库与知识库兼容

ORM 是应用结构的权威定义。SQLite 使用字符串 UUID，PostgreSQL 使用 UUID 列；旧 PostgreSQL 的 `metadata` 列需在备份后显式执行 `scripts/migrate_metadata.sql` 重命名为 `meta`，应用不会自动执行迁移。

旧 FAISS 索引和文档 pickle 必须成对保留；读取只允许字符串列表，拒绝任意类对象。成功新增文档后写入原子存储 `rag_store.json`，不删除旧文件。回退旧版本时应恢复配套数据快照，旧版本不会读取新存储。

## Docker 或手动部署

本轮本地管理器不负责启动 Docker。Compose 仍提供 PostgreSQL 与两个应用服务，需要设置 `POSTGRES_PASSWORD`、`API_TOKEN`、`WEB_PASSWORD`，并把 `LLM_BASE_URL` 指向容器可访问的模型服务。

```bash
docker compose config --quiet
docker compose up --build -d
```

确保 Compose 的模型名、上下文预算和宿主机模型服务一致；不要假设容器的 localhost 指向宿主机。API 和 PostgreSQL 不公开宿主机端口，页面默认仅发布到回环地址。已有 PostgreSQL 卷不会因为修改环境变量自动轮换密码，应使用最小权限数据库账号。

## 取消与重试

浏览器取消会中断网络请求，Go 代理传递取消，API 停止等待模型或异步工具。已经进入线程的解析、文件操作或 SMTP 发送不能强制撤销，远端模型也可能短暂继续清理任务。

服务端在执行可能有副作用的工具前持久化标记；即使连接丢失，也拒绝对该用户消息一键重试。无法确认请求是否被接收时，页面提示先查看历史，不能保证副作用已经回滚。

## 共享使用边界

这是单用户或互相信任团队的共享实例，不提供多用户会话、文件或知识库隔离。外部访问须经 HTTPS 反向代理，配置认证、限流、容量配额和备份。健康接口仅表示应用就绪，真实模型能力须运行验收集验证。
