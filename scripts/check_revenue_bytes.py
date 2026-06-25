"""Check raw bytes of revenue label in H-share page 19 table."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
h_doc = parse_h_pdf(h_path)

for t in h_doc.tables:
    if t.page != 19:
        continue
    for cell in t.cells:
        if cell.row == 5 and cell.col == 0:
            raw = cell.text.strip()
            print(f"raw_label = {raw!r}")
            print(f"raw_bytes = {raw.encode('utf-8')!r}")
            try:
                decoded = raw.encode('utf-8').decode('utf-8')
                print(f"decoded = {decoded!r}")
            except Exception as e:
                print(f"decode error: {e}")

            simp = to_simplified(raw)
            print(f"to_simplified = {simp!r}")
            print(f"lookup(raw) = {glossary.lookup(raw)}")
            print(f"lookup(simp) = {glossary.lookup(simp)}")

            # Try decoding bytes manually
            b = raw.encode('utf-8')
            print(f"\nByte analysis:")
            for i in range(0, len(b), 3):
                chunk = b[i:i+3]
                try:
                    ch = chunk.decode('utf-8')
                    print(f"  {chunk!r} -> {ch!r}")
                except:
                    print(f"  {chunk!r} -> ???")
