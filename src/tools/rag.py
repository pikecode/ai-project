"""单进程知识库，共享实例、串行写入和单文件原子持久化。"""

import asyncio
import hashlib
import json
import os
import pickle
import tempfile
from pathlib import Path
from threading import RLock
from typing import Literal

import faiss
import numpy as np

from .base import BaseTool, registry

KnowledgeBase = Literal["daily", "test"]
_test_tool = None


class DocumentUnpickler(pickle.Unpickler):
    """旧文档仅允许基本类型，禁止 pickle 导入或执行对象。"""

    def find_class(self, module, name):
        raise ValueError("Unsupported legacy document object")


class RAGTool(BaseTool):
    name = "knowledge_search"
    description = "Search knowledge base using semantic similarity"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    }

    def __init__(self, data_dir=None, embedding_source=None):
        self.model = None
        self._embedding_source = embedding_source
        self.index = None
        self.documents = []
        self.metadata = []
        self.data_dir = Path(data_dir or os.getenv("RAG_DATA_DIR", "./data"))
        self.store_path = self.data_dir / "rag_store.json"
        self.index_path = self.data_dir / "rag_index.faiss"
        self.docs_path = self.data_dir / "rag_docs.pkl"
        self._loaded = False
        self._lock = embedding_source._lock if embedding_source else RLock()

    def _load_model(self):
        if self.model is None and self._embedding_source is not None:
            self._embedding_source._load_model()
            self.model = self._embedding_source.model
        if self.model is None:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer("BAAI/bge-small-zh-v1.5")

    def _load_index(self):
        if self._loaded:
            return
        if self.store_path.exists():
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            documents = data["documents"]
            vectors = np.asarray(data["vectors"], dtype=np.float32)
            metadata = data.get("metadata", [{} for _ in documents])
            if (
                not isinstance(metadata, list)
                or len(metadata) != len(documents)
                or not all(isinstance(item, dict) for item in metadata)
            ):
                raise ValueError("Invalid knowledge metadata")
            if not isinstance(documents, list) or not all(
                isinstance(doc, str) for doc in documents
            ):
                raise ValueError("Invalid knowledge documents")
            if (
                vectors.ndim != 2
                or len(vectors) != len(documents)
                or not np.isfinite(vectors).all()
            ):
                raise ValueError("Invalid knowledge vectors")
            index = faiss.IndexFlatIP(vectors.shape[1])
            index.add(vectors)
            self.index, self.documents = index, documents
            self.metadata = metadata
        elif self.index_path.exists() or self.docs_path.exists():
            if not self.index_path.exists() or not self.docs_path.exists():
                raise ValueError(
                    "Incomplete legacy knowledge store; restore both files from backup"
                )
            with self.docs_path.open("rb") as source:
                documents = DocumentUnpickler(source).load()
            index = faiss.read_index(str(self.index_path))
            if not isinstance(documents, list) or not all(
                isinstance(doc, str) for doc in documents
            ):
                raise ValueError("Invalid legacy documents")
            if index.ntotal != len(documents):
                raise ValueError("Legacy knowledge index does not match documents")
            self.index, self.documents = index, documents
            self.metadata = [{} for _ in documents]
        self._loaded = True

    def _source(self, index):
        document = self.documents[index]
        info = self.metadata[index]
        original = document
        if info.get("filename") and document.startswith(
            f"# {info.get('title')}\n文件名：{info['filename']}\n"
        ):
            original = document.partition("\n\n")[2]
        return {
            "source_id": "KB-" + hashlib.sha256(document.encode("utf-8")).hexdigest()[:16],
            "content": document,
            "original_content": original,
            "metadata": {
                "title": (document.splitlines() or ["未命名资料"])[0].lstrip("# ")[:100],
                **self.metadata[index],
            },
        }

    def get_source(self, source_id):
        with self._lock:
            self._load_index()
            for index in range(len(self.documents)):
                source = self._source(index)
                if source["source_id"] == source_id:
                    return source
            return None

    def stats(self):
        with self._lock:
            self._load_index()
            return {"total_documents": len(self.documents)}

    async def execute(self, query: str, top_k: int = 3):
        return await asyncio.to_thread(self._search, query, top_k)

    def _excerpt(self, query, document):
        # shortcut: 只识别 Markdown 二级标题，复杂文档解析有明确需求时再扩展。
        sections = document.split("\n## ")
        if len(sections) <= 3:
            return document, False
        passages = [{"text": "## " + section} for section in sections[1:]]
        selected = self.select_passages(query, passages, limit=2)
        return sections[0] + "\n\n" + "\n\n".join(item["text"] for item in selected), True

    def _search(self, query, top_k):
        try:
            if not query.strip() or not 1 <= top_k <= 20:
                raise ValueError("Expected a query and top_k between 1 and 20")
            with self._lock:
                self._load_index()
                if not self.documents:
                    return {"success": True, "results": [], "total_docs": 0}
                self._load_model()
                vector = self.model.encode([query], normalize_embeddings=True).astype(np.float32)
                scores, indices = self.index.search(vector, min(top_k, len(self.documents)))
                results = []
                for score, i in zip(scores[0], indices[0]):
                    if i < 0:
                        continue
                    source = self._source(i)
                    content, excerpted = self._excerpt(query, self.documents[i])
                    info = source["metadata"]
                    if info.get("filename") and len(content.encode("utf-8")) > 2400:
                        passages = [
                            {"text": content[start : start + 400]}
                            for start in range(0, len(content), 400)
                        ]
                        selected = self.select_passages(query, passages, limit=1)
                        content = f"文件：{info['filename'][:80]}\n标题：{info.get('title', '')[:60]}\n版本：{info.get('version', '')[:32]}\n"
                        if info.get("page"):
                            content += f"[第{info['page']}页]\n"
                        content += "以下为相关片段，并非全文：\n" + selected[0]["text"]
                        excerpted = True
                    results.append(
                        {
                            "source_id": source["source_id"],
                            "content": content,
                            "excerpted": excerpted,
                            "score": float(score),
                        }
                    )
                return {
                    "success": True,
                    "query": query,
                    "results": results,
                    "total_docs": len(self.documents),
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def select_passages(self, query, passages, limit=4):
        """复用嵌入模型检索临时附件，不向共享知识库写入内容。"""
        with self._lock:
            self._load_model()
            vectors = self.model.encode(
                [query, *[item["text"] for item in passages]], normalize_embeddings=True
            )
            scores = np.asarray(vectors[1:]) @ np.asarray(vectors[0])
            ranked = sorted(range(len(passages)), key=lambda i: float(scores[i]), reverse=True)
            return [passages[i] for i in ranked[:limit]]

    def add_documents(self, documents: list[str], metadata=None, deduplicate=False):
        try:
            if (
                not documents
                or len(documents) > 100
                or any(not doc.strip() or len(doc) > 20000 for doc in documents)
            ):
                raise ValueError(
                    "Expected 1–100 non-empty documents, each at most 20000 characters"
                )
            metadata = [{} for _ in documents] if metadata is None else metadata
            if len(metadata) != len(documents) or not all(
                isinstance(item, dict) for item in metadata
            ):
                raise ValueError("Invalid knowledge metadata")
            with self._lock:
                self._load_index()
                if deduplicate:
                    seen = set(self.documents)
                    entries = []
                    for document, info in zip(documents, metadata):
                        if document not in seen:
                            entries.append((document, info))
                            seen.add(document)
                    documents = [entry[0] for entry in entries]
                    metadata = [entry[1] for entry in entries]
                    if not documents:
                        return {"success": True, "added": 0, "total": len(self.documents)}
                self._load_model()
                vectors = self.model.encode(documents, normalize_embeddings=True).astype(np.float32)
                index = (
                    faiss.clone_index(self.index)
                    if self.index is not None
                    else faiss.IndexFlatIP(vectors.shape[1])
                )
                index.add(vectors)
                updated = self.documents + documents
                payload = {
                    "documents": updated,
                    "metadata": self.metadata + metadata,
                    "vectors": index.reconstruct_n(0, index.ntotal).tolist(),
                }
                self.data_dir.mkdir(parents=True, exist_ok=True)
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8", dir=self.data_dir, delete=False
                    ) as target:
                        temp_path = Path(target.name)
                        json.dump(payload, target, ensure_ascii=False, allow_nan=False)
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(temp_path, self.store_path)
                finally:
                    if temp_path is not None:
                        temp_path.unlink(missing_ok=True)
                self.index, self.documents = index, updated
                self.metadata = self.metadata + metadata
                return {"success": True, "added": len(documents), "total": len(updated)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def get_knowledge_tool(knowledge_base="daily"):
    if knowledge_base not in {"daily", "test"}:
        raise ValueError("知识库仅支持 daily 或 test")
    return registry.get("knowledge_search") if knowledge_base == "daily" else _test_tool


def init_rag_tool():
    global _test_tool
    daily = RAGTool()
    registry.register(daily)
    _test_tool = RAGTool(daily.data_dir / "test", embedding_source=daily)
