"""统一 LLM 客户端 — 多 provider 路由 + 重试 + Ollama 兜底。

设计要点：
1. 所有国产 LLM 大多提供 OpenAI 兼容接口，统一用 openai SDK + 不同 base_url 调用
2. 通过 LLMRouter 按用途（extract / reason / vlm）路由到不同模型
3. 失败时降级到本地 Ollama（演示当天 API 故障兜底）
4. 内置幂等缓存：相同 prompt + 相同 seed 返回缓存结果（演示稳定性）
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Optional

from loguru import logger
from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# 仅对瞬时错误重试；认证/参数等 4xx 错误立即失败，尽快进 Ollama 兜底
_RETRYABLE_LLM_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

from ahcc.config import settings


# Provider 元信息：把 OpenAI 兼容接口的 base_url 都放在这里
PROVIDERS: dict[str, dict[str, str]] = {
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_attr": "dashscope_api_key",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "api_key_attr": "zhipuai_api_key",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_attr": "moonshot_api_key",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_attr": "deepseek_api_key",
    },
    "ollama": {
        "base_url": settings.ollama_base_url + "/v1",
        "api_key_attr": None,
    },
}


Purpose = Literal["extract", "reason", "vlm"]


def _ensure_json_keyword(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DeepSeek 要求 prompt 含 "json" 关键字才允许 json_object response_format。

    在 messages 中搜索是否存在 "json"（大小写不敏感）。若不存在，
    在最后一条 user message 末尾追加 "Return JSON." 提示。
    """
    combined = " ".join(m.get("content", "") or "" for m in messages)
    if "json" in combined.lower():
        return messages  # 已有关键字，无需修改

    # 找到最后一条 user message 并追加
    result = [dict(m) for m in messages]  # 浅拷贝，避免修改原始 list
    for m in reversed(result):
        if m.get("role") == "user":
            m["content"] = (m.get("content", "") or "") + "\n\nReturn JSON."
            break
    else:
        # 没有任何 user message（极端情况），追加一条
        result.append({"role": "user", "content": "Return JSON."})
    return result


class LLMClient:
    """单 provider 客户端。LLMRouter 根据用途路由到对应 client。"""

    def __init__(self, provider: str, model: str):
        if provider not in PROVIDERS:
            raise ValueError(f"未知 provider: {provider}")
        cfg = PROVIDERS[provider]
        api_key = getattr(settings, cfg["api_key_attr"], "") if cfg["api_key_attr"] else "ollama"
        if cfg["api_key_attr"] and not api_key:
            logger.warning(f"{provider} API Key 未配置")
        self.provider = provider
        self.model = model
        self.client = OpenAI(
            api_key=api_key or "EMPTY",
            base_url=cfg["base_url"],
            timeout=settings.llm_timeout,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE_LLM_ERRORS),
    )
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        response_format: Optional[dict] = None,
        seed: Optional[int] = 42,
    ) -> str:
        """同步 chat 调用，返回纯文本响应。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if seed is not None:
            kwargs["seed"] = seed

        try:
            resp = self.client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            logger.warning(f"[{self.provider}/{self.model}] 调用失败: {exc}")
            raise

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        seed: Optional[int] = 42,
    ) -> dict:
        """强制 JSON 输出。"""
        # DeepSeek 要求 prompt 中必须出现 "json" 关键字才允许 json_object response_format。
        # 若 messages 中不含 "json"（大小写不敏感），在末尾 user message 追加提示。
        if self.provider == "deepseek":
            messages = _ensure_json_keyword(messages)

        text = self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            seed=seed,
        )
        if not text or not text.strip():
            logger.warning(
                f"[{self.provider}/{self.model}] chat_json 收到空响应，"
                f"prompt 长度≈{sum(len(m.get('content','')) for m in messages)}，"
                f"max_tokens={max_tokens}"
            )
            return {}

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 兜底：从文本中提取 JSON 块
            import re
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                return json.loads(match.group(0))
            raise


class LLMRouter:
    """根据用途路由。"""

    def __init__(self) -> None:
        self._clients: dict[str, LLMClient] = {}

    def get(self, purpose: Purpose) -> LLMClient:
        if purpose not in self._clients:
            self._clients[purpose] = self._build(purpose)
        return self._clients[purpose]

    def _build(self, purpose: Purpose) -> LLMClient:
        if purpose == "extract":
            return LLMClient(settings.llm_extract_provider, settings.llm_extract_model)
        if purpose == "reason":
            return LLMClient(settings.llm_reason_provider, settings.llm_reason_model)
        if purpose == "vlm":
            return LLMClient(settings.vlm_provider, settings.vlm_model)
        raise ValueError(f"未知用途: {purpose}")

    def fallback(self) -> LLMClient:
        """降级到 Ollama（演示当天 API 故障）。"""
        return LLMClient("ollama", settings.ollama_model)


# ---------------- Module-level 单例 + 缓存 ----------------

_router = LLMRouter()


def get_client(purpose: Purpose = "extract") -> LLMClient:
    return _router.get(purpose)


def _is_placeholder_key(key: str) -> bool:
    """识别 .env 模板里的占位 key（sk-xxxx / 全 x / your-key 等），视为未配置。"""
    k = (key or "").strip()
    if not k or k == "ollama":
        return False
    low = k.lower()
    return "xxxx" in low or low in {"your-key", "placeholder", "your_api_key", "your-api-key"}


# 简易磁盘缓存（演示稳定性兜底）
_CACHE_DIR = settings.storage_dir / "llm_cache"


def cached_call(
    purpose: Purpose,
    messages: list[dict[str, Any]],
    *,
    json_mode: bool = False,
    **kwargs: Any,
) -> Any:
    """带磁盘缓存的调用：相同 messages 直接读缓存，演示当天 0 延迟。"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 先解析 client，使 cache key 纳入 provider/model：切换模型时缓存不会串号
    client = get_client(purpose)
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "p": purpose,
                "provider": client.provider,
                "model": client.model,
                "m": messages,
                "k": kwargs,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cache_file = _CACHE_DIR / f"{cache_key}.json"

    if cache_file.exists():
        logger.debug(f"LLM 缓存命中 {cache_key[:8]}")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    # 快速失败：若 API Key 未配置或为占位符且 Ollama 不可用，直接跳过
    # ollama 的 api_key_attr 为 None，getattr(settings, None) 会抛 TypeError，需先守卫
    api_key_attr = PROVIDERS[client.provider].get("api_key_attr")
    has_key = getattr(settings, api_key_attr, "") if api_key_attr else "ollama"
    if client.provider != "ollama" and (not has_key or _is_placeholder_key(has_key)):
        logger.warning(f"{client.provider} API Key 未配置或为占位符，跳过 LLM 调用")
        return {} if json_mode else ""

    try:
        if json_mode:
            result = client.chat_json(messages, **kwargs)
        else:
            result = client.chat(messages, **kwargs)
    except Exception as exc:
        logger.warning(f"主 provider 失败，切 Ollama: {exc}")
        try:
            client = _router.fallback()
            if json_mode:
                result = client.chat_json(messages, **kwargs)
            else:
                result = client.chat(messages, **kwargs)
        except Exception as fallback_exc:
            # Ollama 兜底也失败（未启动/网络不通）：永不抛异常，返回空值让上层优雅降级
            logger.error(f"Ollama 兜底也失败，跳过本次 LLM 调用: {fallback_exc}")
            return {} if json_mode else ""

    cache_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def load_prompt(name: str) -> str:
    """从 prompts/ 目录加载 prompt 模板。"""
    path = Path(__file__).parent / "prompts" / name
    return path.read_text(encoding="utf-8")
