"""Patch pdf_a.py unit detection."""
import re

with open('ahcc/parser/pdf_a.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the function
start_marker = 'def _detect_unit_currency(texts: list[TextSegment]) -> tuple[str | None, Currency | None]:'
end_marker = '    return None, None'

start_idx = content.find(start_marker)
if start_idx < 0:
    print('Start marker not found')
    exit(1)

end_idx = content.find(end_marker, start_idx)
if end_idx < 0:
    print('End marker not found')
    exit(1)

end_idx += len(end_marker)

new_func = '''def _detect_unit_currency(texts: list[TextSegment]) -> tuple[str | None, Currency | None]:
    """从文本中检测金额单位和币种。

    策略：优先从财务报表主表（bs/pl/cf/equity）中检测，
    避免被附注中提及的子公司币种（如港元）干扰。
    A 股默认人民币。
    """
    unit_patterns = [
        (r"人民币[\\s]*千[\\s]*元", "人民币千元", Currency.CNY),
        (r"人民币[\\s]*百[\\s]*万[\\s]*元", "人民币百万元", Currency.CNY),
        (r"人民币[\\s]*万[\\s]*元", "人民币万元", Currency.CNY),
        (r"人民币[\\s]*元", "人民币元", Currency.CNY),
        (r\'RMB[\\s]*[\\\'"](\\d+)[\\s]*million\', \'人民币百万元\', Currency.CNY),
        (r"千元", "千元", Currency.CNY),
        (r"百万元", "百万元", Currency.CNY),
        (r"万元", "万元", Currency.CNY),
        (r"港元", "港元", Currency.HKD),
        (r"港币", "港元", Currency.HKD),
        (r"HKD", "港元", Currency.HKD),
        (r"美元", "美元", Currency.USD),
        (r"USD", "美元", Currency.USD),
    ]

    # 阶段 1: 优先扫描财务报表主表章节（bs/pl/cf/equity）
    financial_sections = ("bs", "pl", "cf", "equity")
    for t in texts:
        if t.section in financial_sections:
            for pattern, unit_str, curr in unit_patterns:
                if re.search(pattern, t.text):
                    return unit_str, curr

    # 阶段 2: 扫描其余文本
    for t in texts:
        for pattern, unit_str, curr in unit_patterns:
            if re.search(pattern, t.text):
                return unit_str, curr

    # 阶段 3: A 股默认人民币元
    return "人民币元", Currency.CNY'''

content = content[:start_idx] + new_func + content[end_idx:]

with open('ahcc/parser/pdf_a.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Patched successfully')
