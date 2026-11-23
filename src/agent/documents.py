"""在独立附件内分段检索，只把预算允许的片段送入模型。"""

import asyncio

from src.agent.context import ContextLimitError, input_budget
from src.tools.base import registry


def split_pages(pages, size=350, overlap=75):
    passages = []
    for page in pages:
        for start in range(0, len(page["text"]), size - overlap):
            passages.append({"page": page["page"], "text": page["text"][start : start + size]})
    return passages


async def attachment_context(question, result):
    pages = result.get("pages") or [{"page": 1, "text": result["text"]}]
    budget = input_budget() - len(question.encode("utf-8")) - 512
    if budget < 512:
        raise ContextLimitError("问题过长，没有足够预算读取附件，请缩短问题。")
    passages = pages
    warnings = []
    if len(result["text"].encode("utf-8")) > budget:
        rag = registry.get("knowledge_search")
        if rag is None:
            raise ContextLimitError("附件需要分段检索，但检索服务尚未就绪。")
        passages = await asyncio.to_thread(rag.select_passages, question, split_pages(pages))
        warnings.append("附件较长，本次仅检索相关片段，不能代表全文总结。")
    selected = []
    used = 0
    for item in passages:
        excerpt = f"[第{item['page']}页] {item['text']}"
        cost = len(excerpt.encode("utf-8")) + 4
        if used + cost <= budget:
            selected.append(excerpt)
            used += cost
    if len(selected) < len(passages):
        warnings.append("部分附件片段超出预算，本次只提供预算允许的片段。")
    if not selected:
        raise ContextLimitError("附件片段超出预算，请缩小问题范围。")
    if result.get("truncated"):
        warnings.append("附件超过 200 页或 20 万字符，本次未读取超限部分。")
    return "\n\n".join(selected), warnings
