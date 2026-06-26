"""LLM 客户端测试：占位 key 识别 + 调用失败优雅降级。"""

from __future__ import annotations

from unittest.mock import MagicMock

from ahcc.llm import client as llm_client
from ahcc.llm.client import _is_placeholder_key, cached_call


def test_is_placeholder_key():
    assert _is_placeholder_key("sk-xxxxxxxxxxxxxxxx")
    assert _is_placeholder_key("xxxx-xxxx-xxxx")
    assert _is_placeholder_key("your-key")
    assert _is_placeholder_key("placeholder")
    assert _is_placeholder_key("your_api_key")
    assert not _is_placeholder_key("sk-real123abc")
    assert not _is_placeholder_key("")
    assert not _is_placeholder_key(None)  # type: ignore[arg-type]


def test_cached_call_returns_empty_when_llm_fails(monkeypatch, tmp_path):
    """LLM 调用抛异常时，cached_call 返回空值而非抛异常。"""
    monkeypatch.setattr(llm_client.settings, "deepseek_api_key", "sk-realkey123abc")
    llm_client._CACHE_DIR = tmp_path / "llm_cache"

    fake_primary = MagicMock()
    fake_primary.provider = "deepseek"
    fake_primary.model = "test-model"
    fake_primary.chat_json.side_effect = Exception("deepseek conn error")
    monkeypatch.setattr(llm_client._router, "get", lambda purpose: fake_primary)

    result = cached_call("reason", [{"role": "user", "content": "test"}], json_mode=True)
    assert result == {}


def test_cached_call_fast_fail_on_placeholder_key(monkeypatch, tmp_path):
    """占位 key 时快速失败返回空，不发起任何网络调用。"""
    monkeypatch.setattr(llm_client.settings, "deepseek_api_key", "sk-xxxxxxxxxxxxxxxx")
    llm_client._CACHE_DIR = tmp_path / "llm_cache"
    fake = MagicMock()
    fake.provider = "deepseek"
    fake.model = "test-model"
    monkeypatch.setattr(llm_client._router, "get", lambda purpose: fake)

    result = cached_call("reason", [{"role": "user", "content": "test"}], json_mode=True)
    assert result == {}
    fake.chat_json.assert_not_called()
