import sqlite3
from types import SimpleNamespace

import pytest

from scripts import local


@pytest.fixture
def manager(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".runtime").mkdir()
    monkeypatch.setattr(local, "ROOT", root)
    return root


def test_import_backs_up_and_preserves_source_then_refuses_overwrite(manager, tmp_path):
    source = tmp_path / "legacy.db"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE data (value TEXT)")
        database.execute("INSERT INTO data VALUES ('保留原数据')")
    before = source.read_bytes()
    uploads = tmp_path / "old-uploads"
    uploads.mkdir()
    (uploads / "sample.txt").write_text("原附件")
    local.import_data(source, uploads)
    assert source.read_bytes() == before
    with sqlite3.connect(manager / "var/agent.db") as db:
        assert db.execute("SELECT value FROM data").fetchone()[0] == "保留原数据"
    assert (manager / "var/uploads/sample.txt").read_text() == "原附件"
    assert len(list((manager / "backups").glob("import-*/agent.db"))) == 1
    with pytest.raises(ValueError, match="拒绝覆盖"):
        local.import_data(source, uploads)
    (manager / "var/trial").mkdir()
    (manager / "var/trial/feedback.csv").write_text("编号,结果\nT01,待准备\n", encoding="utf-8")
    backup = local.backup()
    assert (backup / "agent.db").exists()
    assert (backup / "trial/feedback.csv").read_text(encoding="utf-8") == "编号,结果\nT01,待准备\n"


def test_backup_does_not_follow_symlink_sources(manager, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "escape").symlink_to(tmp_path / "secret")
    local.snapshot(tmp_path / "snapshot", uploads=uploads)
    assert not (tmp_path / "snapshot/uploads/escape").is_symlink()
    assert "escape" in (tmp_path / "snapshot/uploads-skipped-links.json").read_text()
    assert (uploads / "escape").is_symlink()


def test_stop_never_signals_unknown_process(manager, monkeypatch):
    (manager / ".runtime/service.json").write_text('{"pid":12345,"nonce":"expected"}')
    monkeypatch.setattr(
        local.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="python another-service.py"),
    )
    monkeypatch.setattr(
        local.os, "kill", lambda *args: pytest.fail("must not signal unrelated process")
    )
    local.stop()


def test_dotenv_parser_does_not_execute_shell(manager):
    path = manager / ".env"
    path.write_text("A='value with spaces'\nB=\nexport C=test # 注释\n")
    assert local.read_env(path) == {"A": "value with spaces", "B": "", "C": "test"}
    path.write_text("A=$(touch hacked)\n")
    with pytest.raises(ValueError):
        local.read_env(path)
    assert not (manager / "hacked").exists()


def test_setup_rejects_changed_runtime_archive(manager, tmp_path):
    archive = tmp_path / "fake.tgz"
    archive.write_bytes(b"not trusted")
    with pytest.raises(ValueError, match="摘要不匹配"):
        local.setup(archive)
    assert not (manager / ".env.local").exists()


def test_backup_includes_both_knowledge_libraries(manager):
    knowledge = manager / "var/knowledge"
    (knowledge / "test").mkdir(parents=True)
    (knowledge / "rag_store.json").write_text("日常资料", encoding="utf-8")
    (knowledge / "test/rag_store.json").write_text("测试资料", encoding="utf-8")
    backup = local.backup()
    assert (backup / "knowledge/rag_store.json").read_text(encoding="utf-8") == "日常资料"
    assert (backup / "knowledge/test/rag_store.json").read_text(encoding="utf-8") == "测试资料"
