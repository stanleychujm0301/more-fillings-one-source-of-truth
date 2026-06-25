"""RAG 索引构建（P4 实现）— 把 kb/standards/*.md 入 ChromaDB。

每个 Markdown 文件结构（详见 kb/standards/00_README.md）：

---
topic_key: rnd_capitalize
topic_zh: 研发支出资本化
topic_en: R&D capitalization
cas_code: CAS 6
ifrs_code: IAS 38
keywords: [研发, R&D, 资本化, capitalization, 无形资产]
---

# 差异性质
...

# CAS 条款
...

# IFRS/HKFRS 条款
...

# 是否符合预期差异
...

构建时按章节切片，存入 Chroma collection `standard_kb`。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from loguru import logger

from ahcc.config import settings


def build_kb(kb_dir: Path | None = None) -> int:
    """构建/重建 RAG 索引。返回入库条目数。"""
    import chromadb
    from sentence_transformers import SentenceTransformer

    kb_dir = kb_dir or Path(__file__).resolve().parents[2] / "kb" / "standards"
    md_files = sorted(kb_dir.glob("[0-9]*.md"))
    if not md_files:
        logger.warning(f"未找到准则 MD 文件于 {kb_dir}")
        return 0

    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    collection = client.get_or_create_collection(
        name="standard_kb",
        metadata={"hnsw:space": "cosine"},
    )

    model = SentenceTransformer("BAAI/bge-m3")

    docs: list[str] = []
    metas: list[dict] = []
    ids: list[str] = []

    for md in md_files:
        for chunk in _split_md(md):
            docs.append(chunk["text"])
            metas.append(chunk["meta"])
            ids.append(chunk["id"])

    if not docs:
        return 0

    embeddings = model.encode(docs, normalize_embeddings=True).tolist()
    collection.upsert(documents=docs, metadatas=metas, ids=ids, embeddings=embeddings)
    logger.info(f"准则 RAG 入库 {len(docs)} 条")
    return len(docs)


def _split_md(md_path: Path) -> list[dict]:
    """切片：每个 H1 段落作为一个 chunk，frontmatter 作为元数据。"""
    # 归一化换行：Windows CRLF 的 kb/standards/*.md 否则匹配不到 frontmatter，准则元数据会丢失
    text = md_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        return [{"id": md_path.stem, "text": text, "meta": {"file": md_path.name}}]
    front = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)

    chunks: list[dict] = []
    parts = re.split(r"^# ", body, flags=re.MULTILINE)
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        chunks.append({
            "id": f"{md_path.stem}_{i}",
            "text": ("# " + part).strip(),
            "meta": {**front, "file": md_path.name, "chunk": i},
        })
    return chunks


if __name__ == "__main__":
    build_kb()
