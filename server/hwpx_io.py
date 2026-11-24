"""hwpx(zip 안의 XML) → Report 파싱. 문서 구조는 샘플 3개 기준:
 최상위 문단마다 표(2행×3열: 부서명 | 금주 | 차주)가 있고 1쪽 칸에는 '7. …' 제목 + CSR 표 + 건들, 2쪽 칸에는 건들이 이어진다."""
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from schedule import Item, Period, Phase, calc_weeks

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
P = "{%s}" % HP
SECTION = "Contents/section0.xml"
DATE = r"‘(\d\d)\.(\d\d)\.(\d\d)\.\([월화수목금토일]\)"
PHASE = re.compile(r"·\s*([^:·]+?)\s*:\s*(" + DATE + r")(?:\s*(~)\s*(?:(" + DATE + r"))?)?")


@dataclass
class Csr:
    period: Period
    requested: int
    handled: int
    calls: int
    outages: int
    on_time: int


@dataclass
class Report:
    report_date: date
    dept: str
    this_week: Period
    next_week: Period
    this_items: list[Item]
    next_items: list[Item]
    csr: Csr


def load_section(path: Path) -> tuple[ET.Element, str]:
    """section0.xml을 읽고 네임스페이스 접두사를 등록한다. (루트, 원문 문자열) 반환"""
    text = zipfile.ZipFile(path).read(SECTION).decode("utf-8")
    for prefix, uri in re.findall(r'xmlns:(\w+)="([^"]+)"', text[:3000]):
        ET.register_namespace(prefix, uri)
    return ET.fromstring(text.encode("utf-8")), text


def t_text(t: ET.Element) -> str:
    """hp:t의 글자. 고정폭 공백(fwSpace)은 공백 하나로 읽는다"""
    out = t.text or ""
    for child in t:
        out += (" " if child.tag == P + "fwSpace" else "") + (child.tail or "")
    return out


def para_text(p: ET.Element) -> str:
    return "".join(t_text(t) for t in p.iter(P + "t"))


def cell_paras(tc: ET.Element) -> list[ET.Element]:
    return tc.find(P + "subList").findall(P + "p")


def page_cells(root: ET.Element) -> list[dict[str, ET.Element]]:
    """쪽마다 {'dept','this','next'} 칸을 돌려준다 (3열 표를 가진 최상위 문단 순서대로)"""
    pages = []
    for p in root.findall(P + "p"):
        for tb in p.iter(P + "tbl"):
            if tb.get("colCnt") != "3":
                continue
            cells = {(tc.find(P + "cellAddr").get("rowAddr"), tc.find(P + "cellAddr").get("colAddr")): tc
                     for tr in tb.findall(P + "tr") for tc in tr.findall(P + "tc")}  # 안쪽 표의 칸과 섞이지 않게 직계만
            pages.append({"dept": cells[("1", "0")], "this": cells[("1", "1")], "next": cells[("1", "2")],
                          "head_this": cells[("0", "1")], "head_next": cells[("0", "2")]})
            break
    return pages


def _date(m) -> date:
    return date(2000 + int(m[0]), int(m[1]), int(m[2]))


def _phase_from(m: re.Match) -> Phase:
    start = _date(re.match(DATE, m.group(2)).groups())
    end_text = m.group(7)
    if end_text:
        return Phase(m.group(1).strip(), start, _date(re.match(DATE, end_text).groups()))
    if m.group(6):
        return Phase(m.group(1).strip(), start, None, open_ended=True)
    return Phase(m.group(1).strip(), start, start)


def parse_phase_line(text: str) -> Phase:
    m = PHASE.search("· " + text)
    if not m:
        raise ValueError(f"일정 줄이 아님: {text}")
    return _phase_from(m)


def _parse_item(paras: list[ET.Element], page: int) -> Item:
    # 한 문단 끝에 '    - 개발일정' 같은 머리 줄이 이어 붙은 경우가 있어 줄을 나눈다
    split = lambda text: re.split(r"(?<=\S) {3,}(?=- (?:개발|주요|사유|내용|요청|완료))", text.rstrip())
    lines = [line.rstrip() for p in paras for line in split(para_text(p))]
    head = lines[0][1:].strip()
    continued = head.endswith("(계속)")
    title = head[: -len("(계속)")].rstrip() if continued else head
    body = [l for l in lines[1:] if l.strip()]
    structured = any(re.match(r"\s*-\s*요청부서", l) for l in body) and any(re.match(r"\s*-\s*(개발)?사유", l) for l in body)
    # 일정 줄 뒤에 메모가 붙었거나 '개발사유' 머리말을 쓰는 건은 구조화하면 내용이 사라지므로 원문 복사로 둔다
    irregular = any(re.match(r"\s*-\s*개발사유", l) for l in body)
    for l in body:
        if PHASE.search(l) and re.sub(r"[\s·~]", "", PHASE.sub("", l)):
            irregular = True
    structured = structured and not irregular
    if not structured:
        return Item(title, continued, [], "", [], None, [], "", None, page,
                    raw=[ET.tostring(p, encoding="unicode") for p in paras])
    reason, content, phases = [], [], []
    content_label, schedule_label, requester, due, section = "내용", None, "", None, "reason"
    for line in body:
        if m := re.match(r"\s*-\s*(?:개발)?사유\s*:?\s*(.*)", line):
            section = "reason"
            if m.group(1).strip():
                reason.append(m.group(1).strip())
        elif m := re.match(r"\s*-\s*(개발내용|내용)\s*:?\s*(.*)", line):
            section, content_label = "content", m.group(1)
            if m.group(2).strip():
                content.append(m.group(2).strip())
        elif m := re.match(r"\s*-\s*(주요일정|개발일정)\s*$", line):
            section, schedule_label = "schedule", m.group(1)
        elif m := re.match(r"\s*-\s*요청부서\s*:\s*(.*)", line):
            requester, section = m.group(1).strip(), "other"
        elif m := re.match(r"\s*-\s*완료\(예정\)일\s*:?\s*(.*)", line):
            dm = re.search(DATE, m.group(1))
            due, section = (_date(dm.groups()) if dm else None), "other"
        elif found := list(PHASE.finditer(line)):
            phases += [_phase_from(f) for f in found]
        elif section == "reason":
            reason.append(line.strip())
        elif section == "content":
            content.append(line)
    return Item(title, continued, reason, content_label, content, schedule_label, phases, requester, due, page)


def _parse_cell(tc: ET.Element, page: int):
    """칸 하나를 읽어 (건 목록, CSR 문단들)을 돌려준다. CSR 표는 1쪽 금주 칸에만 있다"""
    items, current, csr_zone, csr_period, csr_tbl = [], None, False, None, None
    for p in cell_paras(tc):
        text = para_text(p).strip()
        if csr_zone:
            if m := re.search(r"접수기간\s*:\s*(" + DATE + r")\s*~\s*(" + DATE + ")", text):
                s, e = re.findall(DATE, text)[:2]
                csr_period = Period(_date(s), _date(e))
            if p.find(".//" + P + "tbl") is not None:
                csr_tbl, csr_zone = p.find(".//" + P + "tbl"), False
            continue
        if text.startswith("○"):
            if "CSR처리현황" in text:
                csr_zone = True
                continue
            current = [p]
            items.append(current)
        elif current is not None and (text or p.find(".//" + P + "tbl") is not None):
            current.append(p)
        elif current is not None:
            current.append(p)  # 빈 문단은 건에 붙였다가 끝에서 뗀다
    result = []
    for paras in items:
        while len(paras) > 1 and not para_text(paras[-1]).strip() and paras[-1].find(".//" + P + "tbl") is None:
            paras.pop()
        result.append(_parse_item(paras, page))
    return result, csr_period, csr_tbl


def _read_csr(period: Period, tbl: ET.Element) -> Csr:
    cells = {(tc.find(P + "cellAddr").get("rowAddr"), tc.find(P + "cellAddr").get("colAddr")):
             para_text(cell_paras(tc)[0]).strip() for tc in tbl.iter(P + "tc")}
    n = lambda c: int(cells[("2", str(c))])
    return Csr(period, n(1), n(2), n(4), n(5), n(6))


def read_report(path: Path) -> Report:
    path = Path(path)
    report_date = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
    weeks = calc_weeks(report_date)
    root, _ = load_section(path)
    pages = page_cells(root)
    dept = "".join(para_text(p) for p in cell_paras(pages[0]["dept"])).strip()
    this_items, next_items, csr = [], [], None
    for number, cells in enumerate(pages, 1):
        items, period, tbl = _parse_cell(cells["this"], number)
        this_items += items
        if tbl is not None:
            csr = _read_csr(period, tbl)
        next_items += _parse_cell(cells["next"], number)[0]
    return Report(report_date, dept, weeks.this_week, weeks.next_week, this_items, next_items, csr)
