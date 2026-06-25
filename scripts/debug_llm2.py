import sys, asyncio, json
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts, _build_extraction_context
from ahcc.llm.client import cached_call, load_prompt

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
doc = parse_h_pdf(h_path)

table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)
seen = set()
for p in table_points:
    seen.add(p.canonical_key)
for p in text_points:
    seen.add(p.canonical_key)

async def main():
    context = _build_extraction_context(doc)
    prompt_template = load_prompt("extract_keypoints.txt")
    prompt = prompt_template.format(
        side=doc.side.value,
        language=doc.primary_language.value,
        currency=doc.metadata.get("currency", "CNY"),
        period=doc.metadata.get("period", ""),
        section="、".join(set(t.section for t in doc.texts if t.section)),
        content=context,
    )
    messages = [
        {"role": "system", "content": "你是一个专业的财务数据提取助手。请从以下年报内容中提取关键财务数据点，输出 JSON 格式。"},
        {"role": "user", "content": prompt},
    ]
    print(f"Context length: {len(context)}")
    try:
        result = await asyncio.to_thread(cached_call, "extract", messages, json_mode=True, temperature=0.1)
        print(f"Result type: {type(result)}")
        print(f"Result: {json.dumps(result, ensure_ascii=False, indent=2)[:1000]}")
    except Exception as e:
        print(f"Error type: {type(e)}")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
