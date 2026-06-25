"""rule_runner.py 单元测试 — 确保 YAML 表达式求值正确且安全。"""

from __future__ import annotations

import pytest

from ahcc.check.rule_runner import evaluate


def test_simple_equality():
    assert evaluate("a == b + c", {"a": 10, "b": 3, "c": 7}) is True
    assert evaluate("a == b + c", {"a": 11, "b": 3, "c": 7}) is False


def test_inequality():
    assert evaluate("a <= b", {"a": 5, "b": 10}) is True
    assert evaluate("a >= b", {"a": 5, "b": 10}) is False


def test_safe_no_eval_injection():
    """禁止函数调用、属性访问、import 等危险操作。"""
    with pytest.raises(ValueError):
        evaluate("__import__('os').system('ls')", {})
    with pytest.raises(ValueError):
        evaluate("a.append(1)", {"a": []})
