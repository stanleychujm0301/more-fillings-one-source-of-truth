# A+H Consistency Checker (AHCC)

> KPMG 黑客松 China Challenge #1 — A+H 股年报数据一致性检查解决方案
>
> **三模块设计**：底座（数值核查）+ 亮点 1（CAS↔IFRS 准则差异智能解读）+ 亮点 2（多模态图表交叉核对）

---

## 1. 团队分工速查

| # | 角色 | 主代码区 | 业务伙伴 |
|---|---|---|---|
| P1 | 技术负责人/架构 | `ahcc/schemas.py`, `ahcc/orchestrator.py`, `ahcc/api/` | — |
| P2 | 后端 A（解析） | `ahcc/parser/` | P1 定 Schema |
| P3 | 后端 B（规则+报告） | `ahcc/check/numeric.py`, `ahcc/check/rule_runner.py`, `ahcc/report/` | P6 写规则 |
| P4 | AI / 算法 | `ahcc/llm/`, `ahcc/rag/`, `ahcc/vlm/`, `ahcc/check/standard.py`, `ahcc/check/chart.py` | P6 提供准则库 |
| P5 | 前端 | `ui/` | — |
| **P6** | **审计业务专家** | **`kb/standards/` + `rules/*.yaml`** | 全员 |
| P7 | 业务+PM | `docs/demo_script.md`, `docs/risk_register.md`, 路演 PPT | 全员 |

---

## 2. 快速启动

### 2.1 环境准备（Python ≥ 3.10）

```bash
# 推荐使用 uv（更快）
pip install uv
uv sync

# 或使用传统 pip
pip install -e .
```

### 2.2 API Key 配置

```bash
cp .env.example .env
# 填入至少一把 API Key：
# DASHSCOPE_API_KEY (通义千问) — 主推荐
# ZHIPUAI_API_KEY (智谱 GLM-4)
# MOONSHOT_API_KEY (Kimi)
# DEEPSEEK_API_KEY (DeepSeek)
```

### 2.3 启动应用

```bash
# 启动后端（已包含静态 HTML 前端，端口 8000）
uvicorn ahcc.api.main:app --reload --port 8000
```

启动后打开浏览器访问 `http://localhost:8000/` 即可使用前端界面。

> 说明：`ui/` 目录下保留了早期 Streamlit 前端代码作为历史备份，当前正式界面为 `ui/static/index.html`，由 FastAPI 直接挂载服务。

### 2.4 构建准则差异 RAG 库

```bash
# P6 在 kb/standards/ 填好 15 条 Markdown 后运行
python scripts/build_kb.py
```

### 2.5 主办方样本评估（Day 5 关键）

```bash
python scripts/eval_samples.py --pair samples/A.pdf,samples/H.pdf --answers kb/samples_answer_key.xlsx
```

---

## 3. 关键评分指标（硬卡点）

| 指标 | 目标 | Day 5 必达 |
|---|---|---|
| 单组样本处理时长 | < 10 分钟 | ✓ |
| 漏检率 | ≤ 5% | ✓ |
| 每条差异附页码证据链 | 100% | ✓ |
| 准则差异智能解读 | ≥ 1 条带 RAG 引用 | ✓ |
| 图表三方核对 | ≥ 1 个场景 | ✓ |

---

## 4. 目录速览

```
ahcc/         核心 Python 包（数据契约 / 解析 / 检查 / RAG / LLM / 报告）
ui/static/    正式 HTML 前端（单页应用，由 FastAPI 挂载）
ui/           历史 Streamlit 前端代码备份
kb/           知识库（准则差异 15 条 + 中英术语对照 + 样本预期答案）
rules/        YAML 规则（数值 / 勾稽 / 披露三类）
storage/      运行时存储（.gitignore）
scripts/      构建/评估脚本
docs/         架构、演示脚本、风险登记
tests/        单元测试
```

---

## 5. 关键文档

- 实施计划：`C:\Users\Thinkpad X1\.claude\plans\challenge-1-quizzical-boole.md`
- 架构说明：[docs/architecture.md](docs/architecture.md)
- 演示脚本：[docs/demo_script.md](docs/demo_script.md)
- 风险登记：[docs/risk_register.md](docs/risk_register.md)
- 准则库说明：[kb/standards/00_README.md](kb/standards/00_README.md)
