# -*- coding: utf-8 -*-
"""把 finals-deck.html 打包成单文件离线版：所有 assets/*.png 内嵌为 base64 data URI。
产出 finals-deck-offline.html —— 演示电脑只需拷贝这一个文件，图片零丢失风险。"""
import base64
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "finals-deck.html"
OUT = HERE / "finals-deck-offline.html"

html = SRC.read_text(encoding="utf-8")

refs = sorted(set(re.findall(r'src="(assets/[^"]+)"', html)))
if not refs:
    print("未发现 assets 图片引用，无需打包")
    sys.exit(0)

total_in = total_out = 0
for ref in refs:
    p = HERE / ref
    if not p.exists():
        print(f"[错误] 引用的图片不存在: {ref}")
        sys.exit(1)
    raw = p.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    html = html.replace(f'src="{ref}"', f'src="data:image/png;base64,{b64}"')
    total_in += len(raw)
    total_out += len(b64)
    print(f"  内嵌 {ref:<40s} {len(raw)/1024:8.0f} KB -> base64 {len(b64)/1024:8.0f} KB")

# 校验：打包后不应再有任何相对图片路径残留
leftover = re.findall(r'src="assets/', html)
if leftover:
    print(f"[错误] 仍有 {len(leftover)} 处 assets 引用未内嵌")
    sys.exit(1)

OUT.write_text(html, encoding="utf-8")
print(f"\n内嵌图片 {len(refs)} 张，原始 {total_in/1024/1024:.1f} MB")
print(f"产出 {OUT.name}：{OUT.stat().st_size/1024/1024:.1f} MB（单文件，拷此一个即可）")
