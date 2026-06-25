# Contributing to AHCC

感谢你对 A+H Consistency Checker（AHCC）的兴趣。下面是参与本项目的基本流程。

## 开发环境

1. 克隆仓库：
   ```bash
   git clone <repo-url>
   cd ah-consistency-checker
   ```

2. 安装依赖（推荐最小安装，按需启用可选依赖）：
   ```bash
   # 最小安装
   pip install -e .

   # 完整安装（含 OCR、RAG、云端多模态依赖）
   pip install -e ".[all]"
   ```

3. 复制环境变量模板并填写真实 API Key：
   ```bash
   cp .env.example .env
   ```

4. 启动后端：
   ```bash
   python -m uvicorn ahcc.api.main:app --reload --port 8000
   ```

## 代码风格

- 使用 [Ruff](https://docs.astral.sh/ruff/) 进行代码检查与格式化：
  ```bash
  ruff check .
  ruff format .
  ```
- 目标 Python 版本 >= 3.10。
- 保持类型提示，关键函数请补充 docstring。

## 测试

提交 PR 前请确保现有测试通过：

```bash
python -m pytest tests/ -q --tb=short
```

当前基线为 **154 passed，1 failed**（`test_bilingual_checker.py::test_bilingual_detects_date_percentage_and_per_10_share_mismatches` 为已知问题，与清理无关）。新增失败请先行修复。

## 提交 PR

1. 从 `main` 切出功能分支。
2. 小步提交，commit message 使用英文简洁描述。
3. 确保 `.env`、上传 PDF、运行时存储（`storage/`）等不会进入 commit。
4. 发起 Pull Request 并描述改动动机与验证方式。

## 目录说明

- `ahcc/` — 核心 Python 包
- `ui/static/` — 正式前端
- `archive/internal/` — 历史 Streamlit UI 与黑客松内部文档
- `kb/` — CAS/IFRS 准则知识库
- `rules/` — YAML 规则定义
- `scripts/` — 构建、评估、样例生成等核心脚本
- `tests/` — 单元测试

如有重大架构调整，请先阅读 `docs/architecture.md` 并在 PR 中说明。
