"""统一的用户可读错误信息包装。

过去任务失败时后端直接把 Python 异常的 ``str(exc)`` 透传给前端/日志——裸的解析器/超时/
通用异常英文字符串对审计师评委不友好。``friendly_error_message`` 把常见异常类型映射成
"<中文说明>(原始信息: <原始异常文本>)"格式，同时完整保留原始异常文本，
不影响任何依赖原始异常子串做断言的存量测试。
"""

from __future__ import annotations

import asyncio

_TIMEOUT_KEYWORDS = ("timeout", "timed out")
_MEMORY_KEYWORDS = ("memory",)
_PARSE_KEYWORDS = ("pdf", "parse", "parsing", "解析")


def friendly_error_message(exc: BaseException | str) -> str:
    """把异常包装成"<中文说明>(原始信息: <原始异常文本>)"，原始文本逐字保留。"""
    raw = str(exc)
    return f"{_classify(exc, raw)}(原始信息: {raw})"


def _classify(exc: BaseException | str, raw: str) -> str:
    lowered = raw.lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or any(k in lowered for k in _TIMEOUT_KEYWORDS):
        return "任务处理超时"
    if isinstance(exc, MemoryError) or any(k in lowered for k in _MEMORY_KEYWORDS):
        return "内存不足"
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, PermissionError, OSError)):
        return "文件读取失败"
    if any(k in lowered for k in _PARSE_KEYWORDS):
        return "PDF 解析失败"
    return "任务执行失败"
