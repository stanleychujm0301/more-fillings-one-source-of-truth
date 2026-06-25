"""一键重建准则差异 RAG 索引。

用法：
    python scripts/build_kb.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahcc.rag.builder import build_kb


if __name__ == "__main__":
    n = build_kb()
    print(f"OK: 入库 {n} 条")
