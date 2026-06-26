# A+H Consistency Checker (AHCC)

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

> 跨市场年报数据一致性核查工具：自动比对 A 股与 H 股年报中的数值、披露、准则差异与图表一致性，输出带证据链的差异报告。
>
> 本项目源于 KPMG 黑客松 China Challenge #1，现整理为开源项目供审计、投行、研究与开发者使用。

---

## 核心能力

| 模块 | 说明 |
|---|---|
| **底座：数值核查** | 抽取 A/H 年报关键财务指标，识别数值差异与勾稽断裂 |
| **亮点 1：准则差异解读** | 基于 CAS/IFRS 知识库进行 RAG 推理，输出准则引用与解读 |
| **亮点 2：图表交叉核对** | 多模态比对图表与表格数据，识别图表-表格不一致 |

输出格式：Excel、PDF、Word 工作底稿、PPT 路演稿。

---

## 快速启动

### 1. 环境准备（Python ≥ 3.10）

```bash
# 推荐使用 uv（更快）
pip install uv
uv sync

# 或使用传统 pip（最小安装，不含 OCR/RAG 重型依赖）
pip install -e .

# 完整安装（含 paddleocr、chromadb、sentence-transformers、dashscope）
pip install -e ".[all]"
```

### 2. API Key 配置

```bash
cp .env.example .env
# 填入 DeepSeek API Key：
# DEEPSEEK_API_KEY — deepseek-v4-pro
```

### 3. 启动应用

```bash
python -m uvicorn ahcc.api.main:app --reload --port 8000
```

启动后打开浏览器访问 `http://localhost:8000/` 即可使用前端界面。

---

## 目录速览

```
ahcc/              核心 Python 包（数据契约 / 解析 / 检查 / RAG / LLM / 报告）
ui/static/         正式 HTML 前端（单页应用，由 FastAPI 挂载）
archive/internal/  历史 Streamlit 前端与黑客松内部文档归档
kb/                知识库（准则差异 15 条 + 中英术语对照 + 披露框架映射）
rules/             YAML 规则（数值 / 勾稽 / 披露三类）
storage/           运行时存储（默认 .gitignore，不上传）
scripts/           构建、评估、样例生成等核心脚本
scripts/build_kb.py           构建 ChromaDB 准则 RAG 索引
scripts/eval_samples.py       样本评估与指标计算
scripts/e2e_test.py           端到端流程测试
scripts/generate_sample_report.py  生成示例 PDF/Excel 报告
docs/              架构、演示脚本、风险登记
tests/             单元测试
```

> 说明：早期 Streamlit 前端代码（`ui/app.py`、`ui/pages/`、`ui/components/`）已归档到 `archive/internal/ui-streamlit-legacy/`。

---

## 关键文档

- [CONTRIBUTING.md](CONTRIBUTING.md) — 如何贡献代码
- [docs/architecture.md](docs/architecture.md) — 系统架构与数据流
- [docs/demo_script.md](docs/demo_script.md) — 演示脚本
- [docs/risk_register.md](docs/risk_register.md) — 风险登记
- [kb/standards/00_README.md](kb/standards/00_README.md) — 准则库说明

---

## 主要评分指标（参考）

| 指标 | 目标 |
|---|---|
| 单组样本处理时长 | < 10 分钟 |
| 漏检率 | ≤ 5% |
| 每条差异附页码证据链 | 100% |
| 准则差异智能解读 | ≥ 1 条带 RAG 引用 |
| 图表三方核对 | ≥ 1 个场景 |

---

## 许可证

[MIT](LICENSE)

---

## 历史

本项目最初由 KPMG 黑客松 China Challenge #1 团队开发，解题方向为 A+H 股年报一致性检查。赛后整理为开源仓库，方便审计同行、金融机构与开发者复用与改进。
