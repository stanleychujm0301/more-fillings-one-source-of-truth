"""准则 RAG 检索（P4 实现）。"""

from __future__ import annotations

from functools import lru_cache

from loguru import logger

from ahcc.config import settings


@lru_cache(maxsize=1)
def _get_collection():
    import chromadb
    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    return client.get_or_create_collection(name="standard_kb")


@lru_cache(maxsize=1)
def _get_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("BAAI/bge-m3")


# 准则解读为可选模块：未安装 chromadb / 未构建知识库时整条链路按检索为空优雅跳过。
# 该提示每进程只打一次，避免对每条数值差异都刷一条 warning 把日志/控制台淹没。
_rag_unavailable_warned = False


def _warn_rag_once(message: str) -> None:
    global _rag_unavailable_warned
    if not _rag_unavailable_warned:
        logger.warning(message)
        _rag_unavailable_warned = True


def retrieve_clauses(query: str, top_k: int = 4) -> list[dict]:
    """语义检索准则条款。返回 [{text, meta, distance}]。未启用 RAG 时返回空列表。"""
    try:
        collection = _get_collection()
        if collection.count() == 0:
            _warn_rag_once("准则 RAG 库为空，准则解读将跳过（如需启用：安装 chromadb 后运行 scripts/build_kb.py）")
            return []
        embed = _get_embedder().encode([query], normalize_embeddings=True).tolist()
        result = collection.query(query_embeddings=embed, n_results=top_k)
    except Exception as exc:
        _warn_rag_once(f"准则 RAG 不可用，准则解读将跳过（仅提示一次）: {exc}")
        return []

    return [
        {"text": doc, "meta": meta, "distance": dist}
        for doc, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        )
    ]
