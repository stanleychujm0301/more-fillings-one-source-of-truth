import fitz, sys
from pathlib import Path

doc = fitz.open("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")
start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
end = int(sys.argv[2]) if len(sys.argv) > 2 else 10
lines = []
for i in range(start - 1, min(end, len(doc))):
    text = doc[i].get_text()
    lines.append(f"=== Page {i+1} ===")
    lines.append(text[:1200])
    lines.append("")
doc.close()
Path("storage/inspect_output.log").write_text("\n".join(lines), encoding="utf-8")
