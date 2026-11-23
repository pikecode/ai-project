#!/usr/bin/env python3
"""本地版运行管理；只管理本脚本创建的进程与目录。"""

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.40.6"
ARCHIVE_SHA256 = "b2b55434865a668decb494110d7fd970eed3ecc8837be2872f672ccbb95df974"
MODEL_DIGEST = "7f3251fa6878a78a606bcb3c074283306e5ad2ea411a7774a9dd98a4bc7f2d13"
MODEL = "qwen3.5:4b"
ALIAS = "agent-platform-local:stable"


def runtime_dir():
    return ROOT / ".runtime"


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            tokens = shlex.split(line, comments=True)
            if not tokens:
                continue
            if tokens[0] == "export":
                tokens.pop(0)
            if len(tokens) != 1 or "=" not in tokens[0]:
                raise ValueError(f"{path.name} 仅支持单行 KEY=VALUE，不执行 shell 表达式")
            key, value = tokens[0].split("=", 1)
            values[key] = value
    return values


def environment():
    return {**read_env(ROOT / ".env"), **os.environ, **read_env(ROOT / ".env.local")}


def get_json(url, token=""):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def alive(record):
    if not record or not isinstance(record.get("pid"), int) or not record.get("nonce"):
        return False
    command = subprocess.run(
        ["ps", "-p", str(record["pid"]), "-o", "args="], capture_output=True, text=True
    ).stdout
    return str(Path(__file__).resolve()) in command and f"run --nonce {record['nonce']}" in command


def service_record():
    path = runtime_dir() / "service.json"
    return json.loads(path.read_text()) if path.exists() else None


def require_stopped():
    if alive(service_record()):
        raise ValueError("请先执行 ./start.sh stop，避免备份或导入期间数据变化")


def snapshot(destination, database=None, uploads=None, knowledge=None, trial=None):
    destination.mkdir(parents=True, exist_ok=False)
    destination.chmod(0o700)
    if database:
        source_path = Path(database).resolve(strict=True)
        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("源数据库完整性检查未通过")
            with sqlite3.connect(destination / "agent.db") as target:
                source.backup(target)
    for source, name in [(uploads, "uploads"), (knowledge, "knowledge"), (trial, "trial")]:
        if source and Path(source).exists():
            source = Path(source).resolve()
            skipped = []

            def skip_links(directory, names):
                links = [name for name in names if (Path(directory) / name).is_symlink()]
                skipped.extend(str((Path(directory) / name).relative_to(source)) for name in links)
                return links

            shutil.copytree(source, destination / name, ignore=skip_links)
            if skipped:
                (destination / f"{name}-skipped-links.json").write_text(
                    json.dumps(skipped, ensure_ascii=False), encoding="utf-8"
                )
                print(f"{name}：跳过 {len(skipped)} 个符号链接，原链接保留，清单已写入快照")
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(),
                "database": str(database) if database else None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def backup():
    require_stopped()
    var = ROOT / "var"
    if not var.exists():
        raise ValueError("尚无本地持久数据")
    destination = ROOT / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    snapshot(
        destination,
        var / "agent.db" if (var / "agent.db").exists() else None,
        var / "uploads",
        var / "knowledge",
        trial=var / "trial",
    )
    print(f"备份：{destination}")
    return destination


def import_data(database, uploads=None, knowledge=None):
    require_stopped()
    var = ROOT / "var"
    if var.exists():
        raise ValueError("var 已存在，拒绝覆盖；请使用新的目录或先核实原数据")
    destination = ROOT / "backups" / ("import-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    snapshot(destination, database, uploads, knowledge)
    stage = runtime_dir() / ("import-" + secrets.token_hex(8))
    shutil.copytree(destination, stage)
    stage.rename(var)
    print(f"已导入 var，原文件保留；导入前快照：{destination}")


def setup(archive=None):
    require_stopped()
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise ValueError("固定的本地运行包适用于 Apple Silicon macOS；其他平台请按部署文档手动启动")
    runtime = runtime_dir() / f"ollama-{VERSION}"
    if not runtime.exists():
        archive_path = Path(archive) if archive else runtime_dir() / "ollama-darwin.tgz"
        if not archive_path.exists():
            print("下载官方 Ollama 固定版本…", flush=True)
            urllib.request.urlretrieve(
                f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-darwin.tgz",
                archive_path,
            )
        with archive_path.open("rb") as archive_file:
            archive_digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
        if archive_digest != ARCHIVE_SHA256:
            raise ValueError("Ollama 安装包摘要不匹配，拒绝安装")
        stage = runtime_dir() / ("install-" + secrets.token_hex(8))
        stage.mkdir()
        with tarfile.open(archive_path) as bundle:
            bundle.extractall(stage, filter="data")
        stage.rename(runtime)
    profile = ROOT / ".env.local"
    if not profile.exists():
        var = ROOT / "var"
        values = {
            "LLM_BASE_URL": "http://127.0.0.1:11435/v1",
            "LLM_MODEL": ALIAS,
            "CONTEXT_TOKENS": "16384",
            "OUTPUT_TOKENS": "1024",
            "DATABASE_URL": f"sqlite:///{var / 'agent.db'}",
            "WORKSPACE_DIR": str(var / "uploads"),
            "RAG_DATA_DIR": str(var / "knowledge"),
            "API_HOST": "127.0.0.1",
            "API_PORT": "8000",
            "API_WORKERS": "1",
            "HOST": "127.0.0.1",
            "PORT": "8653",
            "AGENT_API_URL": "http://127.0.0.1:8000",
            "API_TOKEN": secrets.token_urlsafe(32),
        }
        with profile.open("x", encoding="utf-8") as target:
            os.chmod(profile, 0o600)
            target.write("# 本地稳定版专用配置，优先于 .env；不要提交此文件。\n")
            target.writelines(f"{key}={shlex.quote(value)}\n" for key, value in values.items())
    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "-q", "-c", "requirements-local.txt", "-e", ".[dev]"],
        cwd=ROOT,
        check=True,
    )
    (runtime_dir() / "model-lock.json").write_text(
        json.dumps(
            {"source": MODEL, "alias": ALIAS, "digest": MODEL_DIGEST, "ollama_version": VERSION},
            indent=2,
        )
    )
    print("环境准备完成。需要导入旧资料时先执行 import；否则 start 创建空的持久存储。")


def wait_ready(url, child, token="", attempts=60):
    for _ in range(attempts):
        if child.poll() is not None:
            raise RuntimeError("服务提前退出，请查看日志")
        try:
            get_json(url, token)
            return
        except (OSError, ValueError, urllib.error.URLError):
            time.sleep(1)
    raise RuntimeError("服务启动超时，请查看日志")


def supervise(nonce):
    env = environment()
    var = ROOT / "var"
    logs = var / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    var.chmod(0o700)
    children = []
    handles = []
    stopping = False

    def terminate(signum, frame):
        nonlocal stopping
        stopping = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    def spawn(command, name, child_env=env, cwd=ROOT):
        handle = (logs / f"{name}.log").open("a")
        handles.append(handle)
        child = subprocess.Popen(
            command, cwd=cwd, env=child_env, stdout=handle, stderr=subprocess.STDOUT
        )
        children.append(child)
        return child

    try:
        for port in [11435, int(env.get("API_PORT", 8000)), int(env.get("PORT", 8653))]:
            with socket.socket() as test:
                test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                test.bind(("127.0.0.1", port))
        ollama = str(runtime_dir() / f"ollama-{VERSION}" / "ollama")
        model_env = {
            **env,
            "OLLAMA_HOST": "127.0.0.1:11435",
            "OLLAMA_CONTEXT_LENGTH": env["CONTEXT_TOKENS"],
        }
        model_server = spawn([ollama, "serve"], "ollama", model_env)
        wait_ready("http://127.0.0.1:11435/api/version", model_server)
        models = get_json("http://127.0.0.1:11435/api/tags")["models"]
        if not any(item["name"] == MODEL and item["digest"] == MODEL_DIGEST for item in models):
            raise RuntimeError("缺少已校验的模型；请先执行 ./start.sh model")
        # 模型别名以固定摘要为准，防止默认标签更新后静默切换模型。
        subprocess.run([ollama, "cp", MODEL, ALIAS], env=model_env, check=True)
        subprocess.run(
            ["go", "build", "-o", str(runtime_dir() / "frontend"), "."],
            cwd=ROOT / "frontend",
            check=True,
        )
        api = spawn(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "src.api.main:app",
                "--host",
                env["API_HOST"],
                "--port",
                env["API_PORT"],
                "--workers",
                "1",
            ],
            "api",
        )
        wait_ready(f"http://127.0.0.1:{env['API_PORT']}/health", api, env["API_TOKEN"])
        spawn([str(runtime_dir() / "frontend")], "frontend", cwd=ROOT / "frontend")
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for handle in handles:
            handle.close()


def start():
    if alive(service_record()):
        print("本地版已经运行")
        return
    if not (ROOT / ".env.local").exists():
        raise ValueError("请先运行 ./start.sh setup")
    nonce = secrets.token_hex(16)
    log = (runtime_dir() / "supervisor.log").open("a")
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "run", "--nonce", nonce],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (runtime_dir() / "service.json").write_text(json.dumps({"pid": child.pid, "nonce": nonce}))
    time.sleep(1)
    if child.poll() is not None:
        raise RuntimeError("启动失败，请查看 .runtime/supervisor.log 和 var/logs/")
    print("启动已提交；./start.sh status 查看状态，./start.sh logs 查看日志")


def stop():
    record = service_record()
    if not alive(record):
        print("本脚本管理的进程未运行，不操作其他服务")
        return
    os.kill(record["pid"], signal.SIGTERM)
    for _ in range(50):
        if not alive(record):
            print("已停止本地服务")
            return
        time.sleep(1)
    raise RuntimeError("停止超时，请核实日志；未强制终止未知进程")


def model():
    env = {**environment(), "OLLAMA_HOST": "127.0.0.1:11435"}
    ollama = str(runtime_dir() / f"ollama-{VERSION}" / "ollama")
    server = None
    try:
        try:
            get_json("http://127.0.0.1:11435/api/version")
        except OSError:
            server = subprocess.Popen(
                [ollama, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            wait_ready("http://127.0.0.1:11435/api/version", server)
        subprocess.run([ollama, "pull", MODEL], env=env, check=True)
        tags = get_json("http://127.0.0.1:11435/api/tags")["models"]
        if not any(item["name"] == MODEL and item["digest"] == MODEL_DIGEST for item in tags):
            raise ValueError("下载结果与 model-lock.json 固定摘要不一致，拒绝激活")
        print("固定模型摘要校验通过")
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["setup", "model", "start", "stop", "status", "logs", "backup", "import", "run"],
        nargs="?",
        default="start",
    )
    parser.add_argument("--archive")
    parser.add_argument("--database")
    parser.add_argument("--uploads")
    parser.add_argument("--knowledge")
    parser.add_argument("--nonce")
    args = parser.parse_args()
    runtime_dir().mkdir(exist_ok=True)
    if args.action == "run":
        if not args.nonce:
            parser.error("run 需要管理进程标识")
        supervise(args.nonce)
        return
    with (runtime_dir() / "manager.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "setup":
            setup(args.archive)
        elif args.action == "model":
            model()
        elif args.action == "start":
            start()
        elif args.action == "stop":
            stop()
        elif args.action == "status":
            running = alive(service_record())
            print("管理进程：" + ("运行中" if running else "未运行"))
            if running:
                env = environment()
                try:
                    info = get_json(f"http://127.0.0.1:{env['API_PORT']}/runtime", env["API_TOKEN"])
                    print(f"模型：{info['model']}；页面：http://127.0.0.1:{env['PORT']}")
                except OSError:
                    print("后端尚未就绪，请检查日志")
        elif args.action == "logs":
            for name in [
                runtime_dir() / "supervisor.log",
                *sorted((ROOT / "var/logs").glob("*.log")),
            ]:
                print(f"--- {name.relative_to(ROOT)} ---")
                if name.exists():
                    print("\n".join(name.read_text(errors="replace").splitlines()[-30:]))
        elif args.action == "backup":
            backup()
        elif args.action == "import":
            if not args.database:
                parser.error("import 必须指定 --database，拒绝自动选择多份数据库")
            import_data(args.database, args.uploads, args.knowledge)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        sys.exit(1)
