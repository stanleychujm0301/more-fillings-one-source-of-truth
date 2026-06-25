"""Find cash flow statement keyword in H-share PDF using PyMuPDF."""
import fitz

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

doc = fitz.open(h_path)

keywords = ["现金流量表", "現金流量表", "Cash Flow Statement", "Statement of Cash Flows", "經營活動所得現金流量淨額"]

print("=== Searching for cash flow keywords in all pages ===")
for page_idx in range(len(doc)):
    page_num = page_idx + 1
    page = doc[page_idx]
    text = page.get_text() or ""
    for kw in keywords:
        if kw in text:
            print(f"Page {page_num}: found keyword")
            break

doc.close()
