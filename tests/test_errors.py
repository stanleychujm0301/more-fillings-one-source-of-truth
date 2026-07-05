"""B9: friendly_error_message 把裸英文异常包装成中文说明，同时保留原始异常文本。"""

from __future__ import annotations

from ahcc.errors import friendly_error_message


def test_generic_exception_gets_wrapped_with_chinese_explanation():
    result = friendly_error_message(Exception("boom"))
    assert "原始信息" in result
    assert "boom" in result
    assert result != "boom"


def test_timeout_exception_is_classified_as_timeout():
    result = friendly_error_message(TimeoutError("upstream call timed out"))
    assert "超时" in result
    assert "upstream call timed out" in result


def test_pdf_parse_failure_is_classified_accordingly():
    result = friendly_error_message(RuntimeError("failed to parse PDF structure"))
    assert "解析" in result
    assert "failed to parse PDF structure" in result


def test_os_error_is_classified_as_file_failure():
    result = friendly_error_message(FileNotFoundError("a.pdf not found"))
    assert "文件" in result
    assert "a.pdf not found" in result


def test_raw_exception_substring_is_preserved_verbatim():
    """原始异常文本必须完整、逐字出现在返回值里 —— 现有测试对特定失败场景断言了英文子串
    （如 worker.py/orchestrator.py 包裹前的异常信息），包装后这些子串不能被替换或翻译掉。"""
    raw = "worker exception: division by zero"
    result = friendly_error_message(Exception(raw))
    assert raw in result
