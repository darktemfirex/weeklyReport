"""Report → HTML 서식 미리보기. 한컴오피스 없이 hwpx와 비슷한 모양(A4 가로, 부서명|금주|차주 표, CSR 표)을 화면에 그린다.
줄 문구·들여쓰기는 hwpx_render._item_paras와 같은 규칙으로 만들고 white-space:pre로 그려, 원본처럼 공백으로 맞춘 정렬이 유지된다.
저장된 hwpx는 read_report로 Report로 읽어 같은 함수로 그린다(굵게 표시한 '금주 추가 줄'은 파일에서 읽지 않으므로 빠진다)."""
import html
from dataclasses import dataclass
from pathlib import Path

from hwpx_io import P, Report, cell_paras, read_report, t_text
import xml.etree.ElementTree as ET

from hwpx_render import fmt_rate
from schedule import Item, Period, fmt_date, fmt_phase, fmt_range, in_week

HEADING = "7. 농/축/수산물 결함 및 개선사항"
CSR_TITLE = "○ CSR처리현황 및 문의 응대 현황"


@dataclass
class Line:
    text: str
    bold: bool = False
    html: str = ""      # 값이 있으면 text 대신 이 HTML을 그대로 쓴다(문단 안의 표)


def _table_html(tbl: ET.Element) -> str:
    """원문 복사 건 안의 표(진척표 등)를 HTML 표로 옮긴다. 합친 칸(colSpan/rowSpan)을 따른다"""
    rows = []
    for tr in tbl.findall(P + "tr"):
        cells = []
        for tc in tr.findall(P + "tc"):
            span = tc.find(P + "cellSpan")
            col, row = (span.get("colSpan", "1"), span.get("rowSpan", "1")) if span is not None else ("1", "1")
            text = "<br>".join(_e("".join(t_text(t) for t in p.iter(P + "t"))) for p in cell_paras(tc))
            cells.append(f'<td colspan="{col}" rowspan="{row}">{text}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table class="raw">' + "".join(rows) + "</table>"


def _raw_lines(p: ET.Element) -> list[Line]:
    """원문 문단 하나 → 줄. 문단 안의 표는 표로, 나머지 글자는 한 줄로"""
    tables = [tb for tb in p.iter(P + "tbl")]
    inside = {id(t) for tb in tables for t in tb.iter(P + "t")}
    text = "".join(t_text(t) for t in p.iter(P + "t") if id(t) not in inside)
    out = [Line(text)] if text.strip() or not tables else []
    return out + [Line("", html=_table_html(tb)) for tb in tables]


def item_lines(item: Item, week: Period) -> list[Line]:
    """건 하나를 hwpx 문단과 같은 줄 목록으로 만든다"""
    if item.raw is not None:
        return [ln for s in item.raw for ln in _raw_lines(ET.fromstring(s))]
    title = "○ " + item.title + ("(계속)" if item.continued else "")
    lines = [Line(title), Line("  - 사유 : " + (item.reason[0] if item.reason else "").rstrip())]
    lines += [Line(" " * 11 + line) for line in item.reason[1:]]
    inline = bool(item.content) and not item.content[0].startswith(" ")
    emphasized = item.emphasis or []
    lines.append(Line(f"  - {item.content_label}" + (f" : {item.content[0]}" if inline else ""),
                      inline and item.content[0] in emphasized))
    lines += [Line(line, line in emphasized) for line in item.content[1 if inline else 0:]]
    if item.schedule_label:
        lines.append(Line(f"  - {item.schedule_label}"))
    for ph in item.phases:        # 그 칸의 주(금주/차주)에 포함되는 일정은 굵게
        lines.append(Line("    · " + fmt_phase(ph), in_week(ph, week)))
    lines.append(Line(f"  - 요청부서 : {item.requester}"))
    lines.append(Line("  - 완료(예정)일" + (f" : {fmt_date(item.due)}" if item.due else "")))
    return lines


def _e(text: str) -> str:
    return html.escape(text, quote=False)


def _lines_html(lines: list[Line]) -> str:
    out = []
    for ln in lines:
        if ln.html:
            out.append(f'<div class="rawtbl">{ln.html}</div>')
            continue
        text = _e(ln.text) or "&nbsp;"
        if ln.text.endswith("(계속)") and ln.text.lstrip().startswith("○"):
            text = _e(ln.text[:-4]) + "<b>(계속)</b>"
        out.append(f'<div class="ln{" b" if ln.bold else ""}">{text}</div>')
    return "".join(out)


def _cell_items(items: list[Item], week: Period) -> str:
    parts = []
    for number, item in enumerate(items):
        if number:
            parts.append('<div class="ln">&nbsp;</div>')
        parts.append(_lines_html(item_lines(item, week)))
    return "".join(parts)


def _csr_html(csr) -> str:
    rate = fmt_rate(csr.handled, csr.requested)
    row = f"<td>{csr.requested}</td><td>{csr.handled}</td><td>{rate}</td><td>{csr.calls}</td><td>{csr.outages}</td><td>{csr.on_time}</td>"
    return ('<table class="csr"><tr><th rowspan="2">업무</th><th colspan="3">CSR처리현황(SLA)</th><th>전화문의</th><th colspan="2">장애현황</th></tr>'
            "<tr><th>요청건수</th><th>처리건수</th><th>처리율</th><th>처리건수</th><th>발생건수</th><th>적기복구</th></tr>"
            f"<tr><th>물류</th>{row}</tr><tr class=\"sum\"><th>총계</th>{row}</tr></table>")


STYLE = """
:root { color-scheme: light; }
body { margin: 0; background: #d9dde5; font-family: "한컴바탕","함초롬바탕","바탕","Batang",serif; color: #000; }
.page { width: 1123px; min-height: 794px; box-sizing: border-box; background: #fff; margin: 14px auto; padding: 64px 68px 26px;
        box-shadow: 0 1px 6px rgba(0,0,0,.25); position: relative; }
table.main { width: 100%; border-collapse: collapse; table-layout: fixed; }
table.main > tbody > tr > th, table.main > tbody > tr > td { border: 1.4px solid #000; padding: 4px 6px; vertical-align: top; }
table.main th { font-size: 18.6px; text-align: center; vertical-align: middle; height: 34px; letter-spacing: .12em; font-weight: 700; }
table.main th small { font-size: 17px; letter-spacing: 0; font-weight: 700; }
td.dept { text-align: center; font-size: 17px; }
.ln { font-size: 17.3px; line-height: 1.5; white-space: pre; }
.ln.b { font-weight: 700; }
table.csr { border-collapse: collapse; margin: 4px 0 8px 28px; font-size: 12.5px; text-align: center; }
table.csr th, table.csr td { border: 1px solid #000; padding: 2px 5px; font-weight: 700; }
table.csr td { font-weight: 400; background: #fff; } table.csr tr.sum td { font-weight: 700; } table.csr th { background: #e8e8e8; }
table.csr tr.sum th, table.csr tr.sum td { background: #e4dccb; }
.rawtbl { margin: 4px 0 4px 28px; }
table.raw { border-collapse: collapse; font-size: 13px; text-align: center; }
table.raw td { border: 1px solid #000; padding: 2px 8px; }
.pno { text-align: center; font-size: 13px; margin-top: 10px; }
"""

SCALE_JS = "function fit(){var z=Math.min(1,(window.innerWidth-12)/1123);document.querySelectorAll('.page').forEach(function(p){p.style.zoom=z})}addEventListener('resize',fit);fit();"


def render_html(report: Report) -> str:
    """Report를 쪽별 표(부서명 | 금주 계획사항 | 차주 계획사항)로 그린 완전한 HTML 문서 문자열"""
    last = max([i.page for i in report.this_items + report.next_items] + [1])
    pages = []
    for number in range(1, last + 1):
        this = [i for i in report.this_items if i.page == number]
        nxt = [i for i in report.next_items if i.page == number]
        left = right = ""
        if number == 1:
            left = (_lines_html([Line(HEADING), Line(CSR_TITLE),
                                 Line(f"  - 접수기간 : {fmt_date(report.csr.period.start)} ~ {fmt_date(report.csr.period.end)}")])
                    + _csr_html(report.csr))
            right = _lines_html([Line(HEADING)])
        left += _cell_items(this, report.this_week)
        right += _cell_items(nxt, report.next_week)
        pages.append(
            '<section class="page"><table class="main"><colgroup><col style="width:11%"><col style="width:44.5%"><col style="width:44.5%"></colgroup><tbody>'
            f'<tr><th>부 서 명</th><th>금 주 계 획 사 항<small>({fmt_range(report.this_week)})</small></th>'
            f'<th>차 주 계 획 사 항<small>({fmt_range(report.next_week)})</small></th></tr>'
            f'<tr><td class="dept">{_e(report.dept)}</td><td>{left}</td><td>{right}</td></tr></tbody></table>'
            f'<div class="pno">- {number} -</div></section>')
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>주간보고 미리보기</title><style>{STYLE}</style></head><body>{''.join(pages)}<script>{SCALE_JS}</script></body></html>")


def render_file_html(path: Path) -> str:
    """저장된 hwpx를 읽어 같은 모양으로 그린다"""
    return render_html(read_report(Path(path)))
