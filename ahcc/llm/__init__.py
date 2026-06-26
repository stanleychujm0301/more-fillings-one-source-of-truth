"""LLM 调用层：统一接口 + DeepSeek 路由 + 重试。"""

from ahcc.llm.client import LLMClient, get_client

__all__ = ["LLMClient", "get_client"]
