# Repository Guidelines

## 项目结构与模块组织

主要应用位于 `agent-platform/`：

- `src/agent/`：LangGraph 智能体状态、节点与执行图。
- `src/api/`：FastAPI 接口和流式响应；`src/models/`：数据库访问。
- `src/tools/`：SQL、邮件、文件、检索等工具；优先复用已有实现。
- `frontend/main.go`：Go 前端，页面模板与处理逻辑位于同一入口。
- `scripts/schema.sql`：数据库结构；`docs/`：部署文档。
- `data/`、`uploads/`、`frontend/uploads/`：运行数据与上传文件，避免作为源码提交。

## 构建、测试与本地开发

除特别说明外，先执行 `cd agent-platform`。Python 要求 3.11 以上，Go 模块声明版本为 1.21。

- `python3 -m venv .venv && source .venv/bin/activate`：创建并激活虚拟环境。
- `pip install -e '.[dev]'`：安装应用及测试、格式化工具。
- `./start.sh`：准备 Ollama 模型并启动后端与前端；脚本可能下载模型并安装依赖。
- `python -m uvicorn src.api.main:app --reload --port 8000`：单独启动开发 API。
- `cd frontend && go run main.go`：单独启动前端。
- `cd frontend && go build ./...`：检查 Go 构建。

API 文档位于 `http://localhost:8000/docs`；启动脚本标示前端端口为 8653，Compose 配置使用 8080。

## 编码风格与命名

Python 使用四空格缩进、`snake_case` 函数与模块名、`PascalCase` 类名。Black 和 Ruff 均配置为 100 字符行宽、Python 3.11 目标版本。提交前运行 `black --check src` 和 `ruff check src`；需要格式化时运行 `black src`。Go 使用 `gofmt -w frontend/main.go`，沿用现有命名与 JSON 字段约定。新增文档和注释使用中文，文件保存为 UTF-8，无 BOM。

保持修改范围最小；遵循 KISS、YAGNI、SOLID、DRY，复用已有模块边界，避免为假设需求添加抽象。

## 测试规范

开发依赖包含 pytest 和 pytest-asyncio，目前未发现测试文件或覆盖率门槛。新增 Python 测试建议放在 `tests/test_<模块>.py`，测试函数命名为 `test_<行为>`，运行 `pytest`。异步测试使用 pytest-asyncio；覆盖成功、失败和边界路径。Go 测试使用 `*_test.go`，在 `frontend/` 执行 `go test ./...`。外部模型、邮件和 HTTP 调用优先模拟，避免真实发送或依赖在线服务。

## 提交与合并请求

### 分支管理

- `main` 是稳定主线，只保留可交付代码；日常开发不得直接提交或推送到 `main`。
- 每项任务从最新 `main` 新建一个短期分支，完成后通过 GitHub Pull Request 合并；合并后删除远端任务分支。
- 分支名使用 `<类型>/<简短英文主题>`：`feat/` 新功能、`fix/` 缺陷修复、`docs/` 文档、`refactor/` 重构、`test/` 测试、`chore/` 工程维护。例如 `fix/stream-response`。
- 分支只处理一个目标。开始前同步主线：`git switch main && git pull --ff-only`，再执行 `git switch -c feat/<主题>`。
- 提交信息使用 `<类型>: <中文简述>`，类型采用 `feat`、`fix`、`docs`、`refactor`、`test`、`chore`。例如 `fix: 修复流式响应`。
- 推送任务分支使用 `git push -u origin <分支名>`；PR 目标为 `main`。合并前检查改动范围、运行与改动相关的验证，并在 PR 描述中记录行为、验证结果和风险。
- 本地 `main` 更新使用 `git pull --ff-only`；不要对共享分支强推、重写历史或提交密钥、数据库、上传文件和运行状态。

仓库已建立 Git 历史。PR 描述需说明问题、修改行为、验证命令及结果；关联相关问题，界面变更附截图，配置或数据库变更注明迁移步骤。

## 安全与配置

参考 `.env.example` 配置环境变量；配置入口是 `src/config.py`，通过进程环境读取。不要假设启动脚本会自动加载 `.env`。禁止提交密钥、SMTP 凭据、用户上传内容或本地数据库。默认数据库为 SQLite；使用 PostgreSQL 时显式配置 `DATABASE_URL`。部署前替换 Compose 示例凭据，并核实端口暴露范围。
