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


def test_record_and_consume_llm_failures_clears_after_read():
    """B10: record_llm_failure/consume_llm_failures 是 orchestrator 汇总"N 次 LLM 调用失败"
    警告用的记录器 —— 消费后必须清空，且不同 job_id 互不干扰。"""
    llm_client.record_llm_failure("job-record-1", "reason a")
    llm_client.record_llm_failure("job-record-1", "reason b")
    llm_client.record_llm_failure("job-record-2", "other job reason")

    failures = llm_client.consume_llm_failures("job-record-1")

    assert failures == ["reason a", "reason b"]
    assert llm_client.consume_llm_failures("job-record-1") == []
    assert llm_client.consume_llm_failures("job-record-2") == ["other job reason"]
    assert llm_client.consume_llm_failures("job-record-2") == []


def test_cached_call_records_failure_for_current_job_without_changing_return_value(monkeypatch, tmp_path):
    """LLM 失败静默降级要显性化：cached_call 对外返回值语义（失败返回 {}）不能变，但失败
    次数应该记录下来，供 orchestrator 汇总进 module_warnings 提示用户结果可能不完整。"""
    monkeypatch.setattr(llm_client.settings, "deepseek_api_key", "sk-realkey123abc")
    llm_client._CACHE_DIR = tmp_path / "llm_cache"

    fake_primary = MagicMock()
    fake_primary.provider = "deepseek"
    fake_primary.model = "test-model"
    fake_primary.chat_json.side_effect = Exception("deepseek conn error")
    monkeypatch.setattr(llm_client._router, "get", lambda purpose: fake_primary)

    token = llm_client.set_current_job_id("job-cached-call")
    try:
        result = cached_call("reason", [{"role": "user", "content": "test"}], json_mode=True)
    finally:
        llm_client._current_job_id.reset(token)

    assert result == {}
    assert llm_client.consume_llm_failures("job-cached-call") == ["deepseek conn error"]


def test_cached_call_without_current_job_id_does_not_record_failure(monkeypatch, tmp_path):
    """未设置当前 job_id 时（如脚本/单测直接调用），不应记录到任何 job 桶下。"""
    monkeypatch.setattr(llm_client.settings, "deepseek_api_key", "sk-realkey123abc")
    llm_client._CACHE_DIR = tmp_path / "llm_cache"

    fake_primary = MagicMock()
    fake_primary.provider = "deepseek"
    fake_primary.model = "test-model"
    fake_primary.chat_json.side_effect = Exception("deepseek conn error")
    monkeypatch.setattr(llm_client._router, "get", lambda purpose: fake_primary)

    assert llm_client._current_job_id.get() is None
    result = cached_call("reason", [{"role": "user", "content": "test"}], json_mode=True)

    assert result == {}
