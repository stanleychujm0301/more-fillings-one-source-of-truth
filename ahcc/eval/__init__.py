"""评估模块：将系统检出的差异与主办方预期答案比对，量化召回率/精确率/漏检率。"""

from ahcc.eval.matcher import (
    ExpectedDiff,
    EvalReport,
    MatchResult,
    evaluate,
    export_eval_excel,
    load_answer_key,
    print_report,
)

__all__ = [
    "ExpectedDiff",
    "EvalReport",
    "MatchResult",
    "evaluate",
    "export_eval_excel",
    "load_answer_key",
    "print_report",
]
