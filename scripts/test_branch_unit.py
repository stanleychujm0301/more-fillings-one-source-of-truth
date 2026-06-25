"""Unit test for branch disclosure extraction logic (no full PDF parse)."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.check.branch_disclosure import extract_branch_table
from ahcc.schemas import ReportDocument, ReportSide, Language, TextSegment

# Build a minimal doc with sample branch text
texts = [
    TextSegment(
        segment_id="test1", page=130, bbox=(0,0,0,0),
        text="本行分支机构具体情况见下表：北京分行 75 810,136 北京市西城区宣武门内大街1号 "
             "天津分行 34 101,325 天津市和平区曲阜道83号 "
             "石家庄分行 55 120,269 石家庄市桥东区裕华东路56号",
        language=Language.ZH, section=None,
    ),
    TextSegment(
        segment_id="test2", page=130, bbox=(0,0,0,0),
        text="上海分行 58 443,188 上海市浦东新区世纪大道1118号 "
             "广州分行 91 338,488 广州市天河区天河北路685号",
        language=Language.ZH, section=None,
    ),
]

doc = ReportDocument(
    doc_id="test_a", side=ReportSide.A_SHARE,
    file_path="test.pdf", total_pages=1,
    primary_language=Language.ZH,
    tables=[], texts=texts, charts=[],
)

branches = extract_branch_table(doc)
print(f"Extracted {len(branches)} branches:")
for name, data in sorted(branches.items()):
    print(f"  {name}: count={data['count']}, asset={data['asset']:,.0f}")

assert "北京分行" in branches
assert branches["北京分行"]["asset"] == 810136
assert branches["天津分行"]["asset"] == 101325
assert branches["石家庄分行"]["asset"] == 120269
assert branches["上海分行"]["asset"] == 443188
assert branches["广州分行"]["asset"] == 338488
print("\nAll assertions passed!")
