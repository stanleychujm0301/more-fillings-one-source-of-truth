# 系统架构

> A+H Consistency Checker (AHCC) — KPMG 黑客松 Challenge #1

## 1. 整体数据流

```
[React UI (ui-new)]
    ↓ POST /api/jobs/  (upload A.pdf + H.pdf)
[FastAPI Orchestrator]
    ↓
[Parser]  解析两份 PDF → ReportDocument (tables + texts + charts + 页码 bbox)
    ↓
[Aligner]  抽 30 关键数据点 + 跨语言对齐 → List[AlignedPair]
    ↓
[Checker ×3]  并发执行:
    ├─ NumericChecker (规则引擎, 容差判断)        → 模块 A
    ├─ StandardChecker (RAG + LLM 准则推理)       → 模块 B (亮点 1)
    └─ ChartChecker (VLM 抽图表数据三方核对)      → 模块 C (亮点 2)
    ↓
[ReportBuilder]  生成 Excel / PDF / HTML 报告
    ↓
[SQLite + 文件系统]  持久化 Job / Diffs / Reviews
    ↓
[React UI]  差异表 + PDF 高亮预览 + 审计师覆盖
```

## 2. 模块依赖图

```
schemas.py
    ↑
    │ 所有模块依赖
    │
parser/       align/       check/       rag/       llm/       vlm/
                  │           │           │          │          │
                  └───────────┴───────────┴──────────┴──────────┘
                              orchestrator.py
                                    │
                                    ↓
                              api/main.py  ←─  ui/app.py
```

## 3. 关键设计决策

### 3.1 数据契约先行（schemas.py）

P1 Day 1 锁定 Pydantic 模型后，P2/P3/P4/P5 可完全并行开工。**所有跨模块通信必须经过 schemas.py 定义的类型**，禁止用裸 dict 传数据。

### 3.2 证据链强制

`Diff.evidence: list[Evidence]` 不允许为空。如果某条差异无法定位到页码 / bbox，则视为不合格，必须改进解析器或丢弃该差异。

### 3.3 LLM 调用三层兜底

```
[业务调用 cached_call(purpose, messages)]
    ↓
1. 磁盘缓存命中 → 直接返回
2. 路由到主 provider (按 .env 配置)
3. 主 provider 失败 → 自动切 Ollama 本地兜底
```

### 3.4 RAG 知识库可热更新

P6 修改 `kb/standards/*.md` 后，运行 `python scripts/build_kb.py` 重建 ChromaDB 索引，无需重启服务。

### 3.5 异步并发 ＋ 缓存

- 同一 Job 内的 LLM 调用用 `asyncio.gather` 并发（受 `LLM_CONCURRENCY` 限流）
- 所有 LLM 输出都进磁盘缓存，演示当天可秒回放

## 4. 性能目标分解（10 分钟内）

| 阶段 | 目标耗时 | 实现要点 |
|---|---|---|
| Parser × 2 | 2 分钟 | pdfplumber 单线程足够；表格抽取并行 |
| Aligner (LLM 抽取 + 对齐) | 3 分钟 | 并发 4 路 LLM；prompt 严格 |
| NumericChecker | 5 秒 | 纯 Python 规则引擎 |
| StandardChecker | 3 分钟 | RAG 检索 + 推理并发 |
| ChartChecker | 1 分钟 | 仅对图表区调用，VLM 调用数量受限 |
| Report 生成 | 30 秒 | openpyxl + reportlab |

## 5. 安全 & 合规

- API Key 仅在 `.env`，绝不入库
- `storage/` 含真实年报，`.gitignore` 已排除
- 主办方样本仅本地使用，演示后清空
- 用户数据上传后保存路径用 job_id 隔离，避免串号
