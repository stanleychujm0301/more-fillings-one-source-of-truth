"""高级图表引擎 — matplotlib 单引擎，PDF 与 Excel 共用同一张精致 PNG。

为什么用 matplotlib 而非 reportlab 矢量：本机 reportlab 的 renderPM 光栅后端
（_renderPM C 扩展 / rlPyCairo）均不可用，无法把 Drawing 导成 PNG；而 matplotlib
已随环境安装、PNG 渲染稳定，且能产出克制、专业、可控的图表。一套代码渲染一张 PNG，
PDF 用 Image flowable 嵌入、Excel 用 openpyxl Image 嵌入，两端像素级一致。

线程安全：使用 matplotlib OO API（Figure + FigureCanvasAgg），不触碰 pyplot 全局状态，
可安全用于 FastAPI 的 asyncio.to_thread 调用。中文字体经 rcParams 指定微软雅黑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from loguru import logger

from ahcc.report import _style as S

_MPL_READY = False


def _ensure_mpl() -> bool:
    """配置 matplotlib（Agg 后端 + 中文字体）；不可用时返回 False。"""
    global _MPL_READY
    if _MPL_READY:
        return True
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        # Microsoft YaHei/SimHei only exist on Windows; Linux containers (Zeabur/
        # Docker) install "Noto Sans CJK SC" via apt — list it so chart labels
        # don't render as missing-glyph boxes in production.
        matplotlib.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "SimHei",
            "sans-serif",
        ]
        matplotlib.rcParams["font.family"] = "sans-serif"
        matplotlib.rcParams["axes.unicode_minus"] = False
        _MPL_READY = True
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"matplotlib 初始化失败，图表将回退：{exc}")
        return False


def _hex(color: str) -> str:
    return color if color.startswith("#") else "#" + color


def _new_fig(width_in: float, height_in: float, dpi: int):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(width_in, height_in), dpi=dpi, facecolor="white")
    FigureCanvasAgg(fig)
    return fig


def _save(fig, out_path: Path, dpi: int) -> Path | None:
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_path), dpi=dpi, facecolor="white", bbox_inches="tight", pad_inches=0.06)
        return out_path
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"图表保存失败：{exc}")
        return None


# 图表显示尺寸（英寸）；dpi 提到 200 保证 PDF/Excel 内清晰
CHART_W_IN = 3.7
CHART_H_IN = 2.35
CHART_DPI = 200


def donut_png(data: Mapping[str, int], out_path: Path, title: str = "",
              dpi: int = CHART_DPI) -> Path | None:
    """严重度分布甜甜圈：海军蓝单色阶、白描边、中心总计、右侧轻量图例。"""
    if not _ensure_mpl():
        return None
    items = [(str(k), int(v)) for k, v in data.items() if v]
    total = sum(v for _, v in items)
    if not items or total <= 0:
        return None
    try:
        labels = [k for k, _ in items]
        values = [v for _, v in items]
        colors = [_hex(S.mono_color(i)) for i in range(len(values))]

        fig = _new_fig(CHART_W_IN, CHART_H_IN, dpi)
        ax = fig.add_axes([0.02, 0.04, 0.56, 0.84])
        wedges, _ = ax.pie(
            values, colors=colors, startangle=90, counterclock=False,
            wedgeprops=dict(width=0.40, edgecolor="white", linewidth=1.6),
        )
        ax.set(aspect="equal")
        # 中心总计
        ax.text(0, 0.12, str(total), ha="center", va="center",
                fontsize=17, color=_hex(S.INK))
        ax.text(0, -0.22, "差异", ha="center", va="center",
                fontsize=8, color=_hex(S.CHART_PALETTE["label"]))
        if title:
            fig.text(0.04, 0.93, title, ha="left", va="top",
                     fontsize=10, color=_hex(S.INK), fontweight="bold")
        # 右侧图例
        legend_labels = [f"{lab}  ·  {val}  ({val / total * 100:.0f}%)"
                         for lab, val in items]
        ax.legend(wedges, legend_labels, loc="center left",
                  bbox_to_anchor=(1.02, 0.5), frameon=False,
                  fontsize=8, labelcolor=_hex(S.INK_SOFT), handlelength=1.0,
                  handleheight=1.0, borderaxespad=0)
        return _save(fig, out_path, dpi)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"甜甜圈图渲染失败：{exc}")
        return None


def hbar_png(data: Mapping[str, int], out_path: Path, title: str = "",
             dpi: int = CHART_DPI) -> Path | None:
    """类型分布水平条：clean、无边框、右侧轻量数值、海军蓝单色阶。"""
    if not _ensure_mpl():
        return None
    items = [(str(k), int(v)) for k, v in data.items()]
    if not items:
        return None
    try:
        labels = [k for k, _ in items]
        values = [v for _, v in items]
        colors = [_hex(S.mono_color(i)) for i in range(len(values))]
        max_val = max(values) if values else 1

        fig = _new_fig(CHART_W_IN, CHART_H_IN, dpi)
        ax = fig.add_axes([0.26, 0.08, 0.60, 0.78])
        y = list(range(len(items)))
        ax.barh(y, values, color=colors, height=0.62, zorder=3)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=8, color=_hex(S.INK_SOFT))
        ax.invert_yaxis()  # 第一项在顶部
        ax.set_xlim(0, max_val * 1.18)
        # 去边框/刻度/网格
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(axis="both", length=0)
        ax.set_xticks([])
        # 右侧数值标签
        for yi, val in zip(y, values):
            ax.text(val + max_val * 0.02, yi, str(val), va="center", ha="left",
                    fontsize=8.5, color=_hex(S.CHART_PALETTE["label"]))
        if title:
            fig.text(0.04, 0.94, title, ha="left", va="top",
                     fontsize=10, color=_hex(S.INK), fontweight="bold")
        return _save(fig, out_path, dpi)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"水平条形图渲染失败：{exc}")
        return None
