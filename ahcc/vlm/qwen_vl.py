"""VLM 图表数据抽取 — 通过 OpenAI-compatible API 调用 deepseek-v4-pro。

复用 ahcc.llm.client 的 cached_call 基础设施：
- SHA-256 磁盘缓存（同图同 prompt 不重复计费）
- tenacity 3 次指数退避重试
- Ollama 本地兜底（若可用）
- placeholder API key 检测自动跳过
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from loguru import logger

from ahcc.llm.client import cached_call


CHART_EXTRACT_PROMPT = """你是图表解析专家。请从下面的财务图表中抽取所有可读数据，输出严格 JSON。

要求：
1. 识别图表类型（pie / bar / line）
2. 列出每个数据点（标签、数值、单位）
3. 若图表有标题，单独记录
4. 数据精度：保留原始小数位

输出格式：
{
  "chart_type": "pie",
  "title": "2024 年业务收入构成",
  "unit": "%",
  "data_points": [
    {"label": "零售业务", "value": 36.2},
    {"label": "对公业务", "value": 45.5},
    {"label": "其他", "value": 18.3}
  ]
}
"""


def extract_chart_data(image_path: str | Path) -> dict:
    """对单张图表抽取结构化数据。

    通过 cached_call("vlm", ...) 走 OpenAI-compatible 多模态接口，
    自动享受磁盘缓存、指数退避重试、Ollama 兜底。
    任何失败（文件不存在、API 不可用、模型不支持视觉等）返回 {}。
    """
    image_path = Path(image_path)
    if not image_path.exists():
        return {}

    try:
        with image_path.open("rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as exc:
        logger.warning(f"读取图表图片失败 {image_path.name}: {exc}")
        return {}

    # OpenAI-compatible 多模态消息格式
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
                {"type": "text", "text": CHART_EXTRACT_PROMPT},
            ],
        }
    ]

    try:
        result = cached_call("vlm", messages, json_mode=True)
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        logger.warning(f"VLM 调用失败 {image_path.name}: {exc}")
        return {}
