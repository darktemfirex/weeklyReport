from datetime import date

from hwpx_html import item_lines, render_html
from hwpx_io import Csr, Report
from schedule import Item, Period, Phase

THIS = Period(date(2026, 11, 30), date(2026, 12, 4))
NEXT = Period(date(2026, 12, 7), date(2026, 12, 11))


def make_item(title="축수산 <테스트> 개발", page=1, continued=False, **kw):
    return Item(title=title, continued=continued, reason=["사유 첫 줄", "사유 둘째 줄"], content_label="내용",
                content=["내용 첫 줄", "            내용 둘째 줄"], schedule_label="개발일정",
                phases=[Phase("분석/설계", date(2026, 11, 30), date(2026, 12, 4)), Phase("본적용", date(2026, 12, 21), date(2026, 12, 21))],
                requester="IT지원팀", due=date(2026, 12, 21), page=page, **kw)


def make_report(this, nxt):
    return Report(date(2026, 11, 30), "유통운영1팀", THIS, NEXT, this, nxt,
                  Csr(Period(date(2026, 11, 23), date(2026, 11, 27)), 2, 2, 6, 0, 0))


def test_item_lines_match_hwpx_paragraph_rules():
    lines = item_lines(make_item(continued=True), THIS)
    texts = [l.text for l in lines]
    assert texts[0] == "○ 축수산 <테스트> 개발(계속)"
    assert texts[1] == "  - 사유 : 사유 첫 줄" and texts[2] == " " * 11 + "사유 둘째 줄"
    assert texts[3] == "  - 내용 : 내용 첫 줄"          # 첫 줄이 공백으로 시작하지 않으면 같은 줄에 붙인다
    assert "  - 개발일정" in texts and texts[-1].startswith("  - 완료(예정)일 : ")
    bold = [l.text for l in lines if l.bold]
    assert len(bold) == 1 and bold[0].startswith("    · 분석/설계")   # 그 주에 걸친 일정만 굵게


def test_render_html_structure_and_escaping():
    page = render_html(make_report([make_item(), make_item("두번째", page=2)], [make_item(page=1)]))
    assert page.count('class="page"') == 2
    assert "금 주 계 획 사 항" in page and "(11.30 ~ 12.04)" in page and "(12.07 ~ 12.11)" in page
    assert "유통운영1팀" in page and "7. 농/축/수산물 결함 및 개선사항" in page
    assert "CSR처리현황 및 문의 응대 현황" in page and "100%" in page and "접수기간" in page
    assert "축수산 &lt;테스트&gt; 개발" in page and "<테스트>" not in page   # 입력 글자는 이스케이프된다
    assert "<b>(계속)</b>" not in page                                      # (계속)이 아니면 굵은 표시 없음


def test_render_html_marks_continued_and_raw_items():
    raw = Item("원문 건", True, [], "내용", [], None, [], "", None, page=2,
               raw=['<hp:p xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:run><hp:t>○ 원문 건</hp:t></hp:run></hp:p>'])
    page = render_html(make_report([make_item(continued=True)], [raw]))
    assert "<b>(계속)</b>" in page and "○ 원문 건" in page


def test_raw_item_table_is_rendered_as_table():
    ns = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    cell = lambda r, c, txt: (f'<hp:tc><hp:subList><hp:p><hp:run><hp:t>{txt}</hp:t></hp:run></hp:p></hp:subList>'
                              f'<hp:cellAddr rowAddr="{r}" colAddr="{c}"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    tbl = "<hp:tbl>" + "<hp:tr>" + cell(0, 0, "업무구분") + cell(0, 1, "진척률") + "</hp:tr><hp:tr>" + cell(1, 0, "수산") + cell(1, 1, "100.0") + "</hp:tr></hp:tbl>"
    raw = Item("원문 건", False, [], "내용", [], None, [], "", None, page=1,
               raw=[f'<hp:p {ns}><hp:run><hp:t>○ 원문 건</hp:t></hp:run></hp:p>', f'<hp:p {ns}><hp:run>{tbl}</hp:run></hp:p>'])
    lines = item_lines(raw, THIS)
    assert [l.text for l in lines if l.text] == ["○ 원문 건"]           # 표 안 글자가 본문 줄에 섞이지 않는다
    page = render_html(make_report([], [raw]))
    assert '<table class="raw">' in page and "<td colspan=\"1\" rowspan=\"1\">진척률</td>" in page
