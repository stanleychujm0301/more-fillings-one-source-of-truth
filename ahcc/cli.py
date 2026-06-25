"""命令行入口（typer）— ahcc check / ahcc build-kb / ahcc eval。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ahcc.orchestrator import Orchestrator

app = typer.Typer(help="A+H Consistency Checker CLI")
console = Console()


@app.command()
def check(a_file: Path, h_file: Path) -> None:
    """对一对 A/H 年报跑一致性核查。"""
    job = asyncio.run(Orchestrator().run(str(a_file), str(h_file)))
    table = Table(title=f"差异清单（共 {len(job.diffs)} 条）")
    table.add_column("ID")
    table.add_column("类别")
    table.add_column("严重度")
    table.add_column("主题")
    for d in job.diffs:
        table.add_row(d.diff_id, d.diff_type.value, d.severity.value, d.topic.best())
    console.print(table)


@app.command("build-kb")
def build_kb() -> None:
    """重建准则 RAG 知识库。"""
    from ahcc.rag.builder import build_kb as _build
    n = _build()
    console.print(f"[green]入库完成：{n} 条")


if __name__ == "__main__":
    app()
