"""Trace _fact_diffs for the dividend case."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Monkeypatch to trace
import ahcc.check.bilingual as bilingual
original_single_value_match = bilingual._single_value_match

def traced_single_value_match(a, h, zh_raw="", en_raw="", zh_currency=None, en_currency=None):
    result = original_single_value_match(a, h, zh_raw, en_raw, zh_currency, en_currency)
    print(f"  _single_value_match: a={a} h={h} zh_raw={zh_raw!r} en_raw={en_raw!r} zh_cur={zh_currency} en_cur={en_currency} -> {result}")
    return result

bilingual._single_value_match = traced_single_value_match

original_is_unit_scale_compatible = bilingual._is_unit_scale_compatible

def traced_is_unit_scale_compatible(zh_fact, en_fact, ratio):
    result = original_is_unit_scale_compatible(zh_fact, en_fact, ratio)
    print(f"  _is_unit_scale_compatible: zh_unit={zh_fact.unit} en_unit={en_fact.unit} ratio={ratio} -> {result}")
    return result

bilingual._is_unit_scale_compatible = traced_is_unit_scale_compatible

from ahcc.check.bilingual import _extract_facts, _fact_diffs, _legacy_pairs_from_alignments, _text_unit_alignments, _disclosure_units_from_doc, _blocks_from_doc, _pair_blocks
from ahcc.schemas import Language, ReportDocument, ReportSide, TextSegment


def _doc(doc_id: str, text: str, language: Language, page: int = 1) -> ReportDocument:
    return ReportDocument(
        doc_id=doc_id,
        side=ReportSide.H_SHARE,
        file_path=f"{doc_id}.pdf",
        total_pages=max(page, 1),
        primary_language=language,
        texts=[
            TextSegment(
                segment_id=f"{doc_id}-s1",
                page=page,
                bbox=(0, 0, 1, 1),
                text=text,
                language=language,
                section="notes",
            )
        ],
    )


zh_doc = _doc(
    "h-zh",
    "合併財務報表附註（續） 63 截至2020年12月31日止年度後的非調整事項 (1) 利潤分配 本公司董事會於2021年3月30日提議向全體股東派發現金股利，以本公司股本總額25,039,945千股為基數，向股東分派現金股利每10股人民幣1.00元（含稅），共計股利人民幣2,503,994千元，此項提議尚待股東於應屆年度股東大會上批准。",
    Language.ZH,
    page=453,
)
en_doc = _doc(
    "h-en",
    "Notes to the consolidated financial statements (continued) 63 Non-adjusting events after the year ended 31 December 2020 (1) Profit distribution Pursuant to the resolution of the Board dated 30 March 2021, the Board proposed to distribute cash dividends of RMB1.00 (tax inclusive) per 10 shares to shareholders based on the total outstanding shares of 25,039,945 thousand shares, with total dividends amounting to RMB25,039,945 thousand. The proposal is subject to the approval of the shareholders in the forthcoming annual general meeting.",
    Language.EN,
    page=453,
)

zh_blocks = _blocks_from_doc(zh_doc)
en_blocks = _blocks_from_doc(en_doc)
pairs = _pair_blocks(zh_blocks, en_blocks, 453, 453)
fact_pairs = _legacy_pairs_from_alignments(_text_unit_alignments(
    _disclosure_units_from_doc(zh_doc, zh_blocks),
    _disclosure_units_from_doc(en_doc, en_blocks),
    pairs,
))

diffs, stats = _fact_diffs(fact_pairs, start_index=1)
print("\nDiffs:", len(diffs))
for d in diffs:
    print(f"  sev={d.severity} triage={d.triage}")
