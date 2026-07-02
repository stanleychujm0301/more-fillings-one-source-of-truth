"""统一配置加载（pydantic-settings）— 从 .env / 环境变量读取。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # API Key — DeepSeek (deepseek-v4-pro)
    deepseek_api_key: str = ""

    # 模型路由
    llm_extract_provider: str = "deepseek"
    llm_extract_model: str = "deepseek-v4-pro"
    llm_reason_provider: str = "deepseek"
    llm_reason_model: str = "deepseek-v4-pro"
    vlm_provider: str = "deepseek"
    vlm_model: str = "deepseek-v4-pro"

    # 应用
    app_env: str = "dev"
    log_level: str = "INFO"
    storage_dir: Path = Path("./storage")
    chroma_persist_dir: Path = Path("./storage/chroma")
    sqlite_path: Path = Path("./storage/ahcc.db")

    # 性能
    llm_concurrency: int = 4
    llm_timeout: int = 60
    llm_max_retries: int = 3

    # H 股中英文跨币种核对汇率（以 HKD 为基准换算后比较）
    # H 股中文版常以人民币披露、英文版常以港币披露，需换算后才能比对金额
    fx_cny_to_hkd: float = 1.08
    fx_usd_to_hkd: float = 7.80
    bilingual_cross_currency_tolerance: float = 0.01

    # H 股中英文 LLM 翻译审查：分批大小与成本护栏上限
    bilingual_semantic_batch_size: int = 40
    bilingual_semantic_max_pairs: int = 300

    # H 股中英文 LLM 事实对比（替代正则提取+位置配对，大幅降低误报）
    bilingual_use_llm_fact_compare: bool = True  # True=LLM 对比, False=旧正则逻辑
    bilingual_fact_batch_size: int = 6           # 每次 LLM 调用包含的段落对数
    bilingual_fact_max_pairs: int = 120          # 总处理段落对上限（成本护栏），从 60 提升到 120 以减少正则回退覆盖
    bilingual_fact_min_confidence: float = 0.75  # 最低段落配对置信度
    bilingual_regex_backfill_min_severity: str = "high"  # 正则回退的最低严重度：high/medium/low，默认仅回退 high

    # 翻译核查漏报修复 — 降低阈值以捕获更多真问题
    bilingual_pair_min_score_low: int = 3        # 段落配对最低 score（低于此值不配对），原硬编码 6
    bilingual_pair_min_score_high: int = 6       # 高置信配对阈值
    bilingual_llm_triage_confidence: float = 0.85  # LLM issue triage="real" 的最低置信度，原 0.92
    bilingual_trace_diagnostics: bool = True     # 是否输出诊断 trace 日志（漏报定位用）
    bilingual_use_raw_text_for_llm: bool = True  # LLM 比对时是否使用保留排版的 raw_text 而非清洗后的 text

    # 演示兜底
    demo_cache_path: Path = Path("./storage/demo_cache.json")
    demo_mode: bool = False

    # A/H numeric semantic review: LLM may downgrade non-comparable high-confidence candidates.
    numeric_use_llm_semantic_review: bool = True
    numeric_llm_review_min_confidence: float = 0.80
    event_use_llm_semantic_review: bool = True
    event_llm_review_min_confidence: float = 0.80
    enable_standard_check: bool = False
    enable_disclosure_coverage_check: bool = False
    enable_a_share_table_extraction: bool = False
    enable_profile_ocr_fallback: bool = False
    profile_ocr_fallback_max_pages: int = 40
    visual_ocr_smart_max_pages: int = 8
    visual_ocr_strict_max_pages: int = 24
    visual_ocr_max_seconds_per_side: float = 45.0
    visual_ocr_easyocr_skip_pages: int = 180
    visual_ocr_easyocr_skip_mb: float = 20.0
    chart_detection_max_pages: int = 60
    enable_chart_vlm_check: bool = False
    # 文本层叠加篡改检测（纯 fitz，无 OCR）：检出"错误值覆盖在原值上方"的植入式篡改
    enable_text_overlay_check: bool = True
    # 任务执行：subprocess=每个任务独立 worker 子进程（可强杀，崩溃不连累服务）；inline=事件循环内执行（测试/评估用）
    job_runner: str = "subprocess"
    job_max_concurrency: int = 1
    job_timeout_seconds: float = 1800
    job_stale_after_seconds: float = 900
    # worker 心跳文件超过该秒数未更新即判定卡死并强杀
    job_heartbeat_stale_seconds: float = 300
    # H 股解析乱码页 OCR 兜底预算（超出预算的乱码页跳过 OCR，仅记 audit warning）
    parse_garbled_ocr_max_pages: int = 10
    parse_garbled_ocr_max_seconds: float = 120.0

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "jobs").mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "user-assets" / "avatars").mkdir(parents=True, exist_ok=True)


settings = Settings()
