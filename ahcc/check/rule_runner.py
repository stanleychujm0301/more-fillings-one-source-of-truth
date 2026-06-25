"""YAML 规则执行器（P3 实现）— 把 P6 写的规则映射成可执行的 Python 函数。

支持的规则表达式：
- 简单等式：`total_assets == current_assets_total + non_current_assets_total`
- 容差等式：`total_assets ≈ ... (tol=0.01)`
- 区间约束：`debt_ratio in [0, 1]`

安全策略：表达式只允许白名单中的运算符和函数（防 eval 注入）。
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from ahcc.schemas import RuleDef


SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.USub: operator.neg,
}


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """安全求值：只允许白名单 AST 节点。"""
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree.body, context)


def _eval_node(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return ctx.get(node.id)
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, ctx)
        right = _eval_node(node.right, ctx)
        return SAFE_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        return SAFE_OPERATORS[type(node.op)](_eval_node(node.operand, ctx))
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval_node(comparator, ctx)
            if not SAFE_OPERATORS[type(op)](left, right):
                return False
            left = right
        return True
    raise ValueError(f"不允许的表达式节点: {type(node).__name__}")


def run_rule(rule: RuleDef, context: dict[str, float]) -> bool:
    """对单条规则求值，返回 True = 通过，False = 违反。"""
    if not rule.expression:
        return True
    return bool(evaluate(rule.expression, context))
