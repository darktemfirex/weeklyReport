"""Report → hwpx. 템플릿(최신 보고서)을 복제하고 section0.xml의 건 영역·기간·CSR 값만 바꾼다.
서식은 템플릿에서 줄 유형별 견본 문단을 복제해 쓰므로 폰트·문단 모양 ID가 그대로 유지된다."""
import copy
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from hwpx_io import (DATE, P, PHASE, Csr, Report, cell_paras, load_section, page_cells, para_text, t_text)
from schedule import Item, Period, fmt_date, fmt_phase, fmt_range, in_week

FIXED = ["유통운영1팀", "7. 농/축/수산물 결함 및 개선사항", "CSR처리현황 및 문의 응대 현황"]
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'


def fmt_rate(handled: int, requested: int) -> str:
    return "-" if requested == 0 else f"{round(handled / requested * 100)}%"


# ---------- 견본 문단 ----------
def _template_paras(pages):
    for cells in pages:
        for key in ("this", "next"):
            for p in cell_paras(cells[key]):
                if p.find(".//" + P + "tbl") is None:
                    yield p


def _prototypes(pages) -> dict[str, ET.Element]:
    rules = {
        "title": lambda t: t.startswith("○") and "CSR" not in t,
        "reason": lambda t: re.match(r"\s*-\s*(개발)?사유", t),
        "content": lambda t: re.match(r"\s*-\s*(개발내용|내용)", t),
        "sched": lambda t: re.match(r"\s*-\s*(주요일정|개발일정)\s*$", t),
        "phase": lambda t: t.strip().startswith("·") and PHASE.search(t),
        "requester": lambda t: re.match(r"\s*-\s*요청부서", t),
        "due": lambda t: re.match(r"\s*-\s*완료\(예정\)일", t),
    }
    protos: dict[str, ET.Element] = {}
    for p in _template_paras(pages):
        text = para_text(p)
        for key, rule in rules.items():
            more_runs = key in ("title", "phase", "requester", "due") and len(p.findall(P + "run")) > len(protos[key].findall(P + "run")) if key in protos else False
            if rule(text) and (key not in protos or more_runs):   # 날짜 등 글자 서식이 나뉜 견본(run이 많은 것)을 선호
                protos[key] = p
    if "reason" not in protos:
        raise ValueError("템플릿에서 사유 줄 견본을 찾지 못함")
    for key in ("title", "content", "sched", "phase", "requester", "due"):
        protos.setdefault(key, protos["reason"])
    return protos


class CharStyles:
    """header.xml의 글자 모양(charPr)을 읽어 굵게/굵지 않게 바꾼 모양의 ID를 돌려준다.
    같은 모양에서 굵기만 다른 것이 이미 있으면 그것을 쓰고, 없으면 새로 만들어 header에 더한다"""
    BOLD = "<hh:bold/>"

    def __init__(self, header: str):
        self.header = header
        self.blocks = {m.group(1): m.group(0) for m in re.finditer(r'<hh:charPr id="(\d+)".*?</hh:charPr>', header, re.S)}
        self.added: list[str] = []

    @staticmethod
    def _shape(block: str) -> str:
        return re.sub(r'id="\d+"', 'id=""', block, count=1).replace(CharStyles.BOLD, "")

    def variant(self, char_pr_id: str, bold: bool) -> str:
        block = self.blocks[char_pr_id]
        if (self.BOLD in block) == bold:
            return char_pr_id
        shape = self._shape(block)
        for other_id, other in self.blocks.items():
            if self._shape(other) == shape and (self.BOLD in other) == bold:
                return other_id
        new_id = str(max(int(i) for i in self.blocks) + 1)
        new = re.sub(r'id="\d+"', f'id="{new_id}"', block, count=1)
        new = new.replace("<hh:underline", self.BOLD + "<hh:underline", 1) if bold else new.replace(self.BOLD, "")
        self.blocks[new_id] = new
        self.added.append(new)
        return new_id

    def patched_header(self) -> str:
        if not self.added:
            return self.header
        header = re.sub(r'(<hh:charProperties itemCnt=")\d+(")', rf"\g<1>{len(self.blocks)}\g<2>", self.header, count=1)
        return header.replace("</hh:charProperties>", "".join(self.added) + "</hh:charProperties>", 1)


def _set_bold(p: ET.Element, bold: bool, styles: CharStyles):
    for run in p.findall(P + "run"):
        run.set("charPrIDRef", styles.variant(run.get("charPrIDRef"), bold))


def _set_text(run: ET.Element, text: str):
    t = run.find(P + "t")
    for child in list(t):
        t.remove(child)
    t.text = text


def _para(proto: ET.Element, text: str) -> ET.Element:
    p = copy.deepcopy(proto)
    for lsa in p.findall(P + "linesegarray"):
        p.remove(lsa)
    runs = [r for r in p.findall(P + "run") if r.find(P + "t") is not None]
    for r in p.findall(P + "run"):
        if r is not runs[0]:
            p.remove(r)
    _set_text(runs[0], text)
    return p


def _para_styled(proto: ET.Element, head: str, tail: str) -> ET.Element:
    """머리말은 견본의 첫 글자 서식, 나머지(날짜·값)는 견본의 마지막 글자 서식(좁은 자간)으로 두 run에 나눠 담는다"""
    p = _para(proto, head)
    runs = [r for r in proto.findall(P + "run") if r.find(P + "t") is not None
            and "".join(t_text(t) for t in r.iter(P + "t")).strip()]
    second = copy.deepcopy(runs[-1])
    _set_text(second, tail)
    first = p.find(P + "run")
    p.insert(list(p).index(first) + 1, second)
    return p


def _title_para(proto: ET.Element, item: Item) -> ET.Element:
    p = copy.deepcopy(proto)
    for lsa in p.findall(P + "linesegarray"):
        p.remove(lsa)
    runs = [r for r in p.findall(P + "run") if r.find(P + "t") is not None]
    _set_text(runs[0], "○ " + item.title)
    for extra in runs[2:]:
        p.remove(extra)
    mark = "(계속)" if item.continued else ""
    if len(runs) >= 2:
        _set_text(runs[1], mark)
    elif mark:
        extra = copy.deepcopy(runs[0])
        _set_text(extra, mark)
        runs[0].addnext(extra)
    return p


def _item_paras(item: Item, protos, week: Period, styles: CharStyles) -> list[ET.Element]:
    if item.raw is not None:
        return [ET.fromstring(s) for s in item.raw]
    paras = [_title_para(protos["title"], item)]

    def add(key, text, bold=False):
        p = _para(protos[key], text)
        _set_bold(p, bold, styles)    # 굵게는 강조 줄과 일정 줄만, 나머지 줄은 굵지 않게 통일
        paras.append(p)

    add("reason", "  - 사유 : " + (item.reason[0] if item.reason else "").rstrip())
    for line in item.reason[1:]:
        add("reason", " " * 11 + line)
    inline = bool(item.content) and not item.content[0].startswith(" ")
    emphasized = item.emphasis or []
    add("content", f"  - {item.content_label}" + (f" : {item.content[0]}" if inline else ""),   # 항목이 아래에 이어지면 콜론 없음
        bold=inline and item.content[0] in emphasized)
    for line in item.content[1 if inline else 0:]:
        add("content", line, bold=line in emphasized)
    if item.schedule_label:
        add("sched", f"  - {item.schedule_label}")
    for ph in item.phases:        # 그 칸의 주(금주/차주)에 포함되는 일정은 굵게
        head, tail = fmt_phase(ph).split(" : ", 1)
        p = _para_styled(protos["phase"], "    · " + head, " : " + tail)
        _set_bold(p, in_week(ph, week), styles)
        paras.append(p)
    for key, tail in (("requester", f"요청부서 : {item.requester}"),
                      ("due", "완료(예정)일" + (f" : {fmt_date(item.due)}" if item.due else ""))):
        p = _para_styled(protos[key], "  - ", tail)
        _set_bold(p, False, styles)
        paras.append(p)
    return paras


# ---------- 칸 다시 채우기 ----------
def _refill(tc: ET.Element, keep_until, items: list[Item], protos, week: Period, styles: CharStyles):
    sub = tc.find(P + "subList")
    paras = cell_paras(tc)
    keep = []
    for p in paras:
        keep.append(p)
        if keep_until(p):
            break
    else:
        keep = []
    for p in paras:
        if p not in keep:
            sub.remove(p)
    for number, item in enumerate(items):
        if number:
            sub.append(_para(protos["reason"], ""))
        for p in _item_paras(item, protos, week, styles):
            sub.append(p)
    if not items and not keep:
        sub.append(_para(protos["reason"], ""))


def _set_periods(head_cell: ET.Element, period: Period):
    runs = cell_paras(head_cell)[0].findall(P + "run")
    _set_text([r for r in runs if r.find(P + "t") is not None][-1], f"({fmt_range(period)})")


def _fill_csr(tc: ET.Element, csr: Csr):
    for p in cell_paras(tc):
        if "접수기간" in para_text(p):
            run = [r for r in p.findall(P + "run") if r.find(P + "t") is not None][0]
            _set_text(run, f"  - 접수기간 : {fmt_date(csr.period.start)} ~ {fmt_date(csr.period.end)}")
        for tbl in p.iter(P + "tbl"):
            if tbl.get("colCnt") != "7":
                continue
            values = [str(csr.requested), str(csr.handled), fmt_rate(csr.handled, csr.requested),
                      str(csr.calls), str(csr.outages), str(csr.on_time)]
            for tr in tbl.findall(P + "tr"):
                for cell in tr.findall(P + "tc"):
                    a = cell.find(P + "cellAddr")
                    if a.get("rowAddr") in ("2", "3") and a.get("colAddr") != "0":
                        _set_text(cell_paras(cell)[0].find(P + "run"), values[int(a.get("colAddr")) - 1])


def preview_text(report: Report) -> str:
    lines = [f"[{report.dept}] 금주 {fmt_range(report.this_week)} / 차주 {fmt_range(report.next_week)}"]
    c = report.csr
    lines.append(f"CSR 접수기간 {fmt_date(c.period.start)} ~ {fmt_date(c.period.end)}: 요청 {c.requested}, 처리 {c.handled}"
                 f"({fmt_rate(c.handled, c.requested)}), 전화문의 {c.calls}, 장애 {c.outages}, 적기복구 {c.on_time}")
    for label, items in (("금주", report.this_items), ("차주", report.next_items)):
        lines.append(f"\n== {label} ==")
        for it in items:
            lines.append(f"{it.page}쪽 ○ {it.title}{' (계속)' if it.continued else ''}")
            if it.raw is None:
                lines += [f"   사유: {' '.join(it.reason)}", f"   {it.content_label}: {' / '.join(s.strip() for s in it.content)}"]
                lines += [f"   · {fmt_phase(p)}" for p in it.phases]
                lines.append(f"   요청부서: {it.requester}  완료(예정)일: {fmt_date(it.due) if it.due else ''}")
            else:
                lines.append("   (형식이 다른 건: 직전 보고서 내용을 그대로 복사)")
    return "\n".join(lines)


def preview_items(report: Report, new_marks: set) -> list[dict]:
    """승인 화면용 건별 정보. new_marks는 새로 등록한 건의 (칸, 그 칸에서의 순번). 신규 여부와 확인에 필요한 필드를 담는다"""
    out = []
    for column, items in (("this", report.this_items), ("next", report.next_items)):
        for index, it in enumerate(items):
            out.append({"column": column, "page": it.page, "title": it.title, "continued": it.continued,
                        "new": (column, index) in new_marks, "raw": it.raw is not None,
                        "reason": list(it.reason), "content_label": it.content_label, "content": list(it.content),
                        "added": list(it.emphasis or []), "schedule_label": it.schedule_label,
                        "phases": [fmt_phase(p) for p in it.phases], "requester": it.requester,
                        "due": fmt_date(it.due) if it.due else ""})
    return out


def render_report(template: Path, report: Report, out: Path) -> None:
    template, out = Path(template), Path(out)
    root, original = load_section(template)
    pages = page_cells(root)
    protos = _prototypes(pages)
    src = zipfile.ZipFile(template)
    styles = CharStyles(src.read("Contents/header.xml").decode("utf-8"))
    is_heading = lambda p: para_text(p).strip().startswith("7.")
    has_csr_tbl = lambda p: p.find(".//" + P + "tbl") is not None and any(
        t.get("colCnt") == "7" for t in p.iter(P + "tbl"))
    for number, cells in enumerate(pages, 1):
        _set_periods(cells["head_this"], report.this_week)
        _set_periods(cells["head_next"], report.next_week)
        mine = lambda items: [i for i in items if i.page == number]
        if number == 1:
            _refill(cells["this"], has_csr_tbl, mine(report.this_items), protos, report.this_week, styles)
            _fill_csr(cells["this"], report.csr)
            _refill(cells["next"], is_heading, mine(report.next_items), protos, report.next_week, styles)
        else:
            _refill(cells["this"], lambda p: False, mine(report.this_items), protos, report.this_week, styles)
            _refill(cells["next"], lambda p: False, mine(report.next_items), protos, report.next_week, styles)
    body = ET.tostring(root, encoding="unicode")
    body = re.sub(r"^<hs:sec[^>]*>", re.search(r"<hs:sec[^>]*>", original).group(0), body, count=1)
    section = (DECL + body).encode("utf-8")
    preview = preview_text(report).encode("utf-8")
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "Contents/section0.xml":
                data = section
            elif info.filename == "Contents/header.xml":
                data = styles.patched_header().encode("utf-8")
            elif info.filename == "Preview/PrvText.txt":
                data = preview
            dst.writestr(info.filename, data,
                         compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)


def verify_hwpx(path: Path) -> list[str]:
    """구조 검증. 위반 사항 목록(빈 목록 = 통과)"""
    problems = []
    path = Path(path)
    if not zipfile.is_zipfile(path):
        return ["zip 파일이 아님"]
    z = zipfile.ZipFile(path)
    first = z.infolist()[0]
    if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
        problems.append("mimetype이 첫 항목·무압축이 아님")
    elif z.read("mimetype") != b"application/hwp+zip":
        problems.append("mimetype 값 이상")
    try:
        root = ET.fromstring(z.read("Contents/section0.xml"))
    except (ET.ParseError, KeyError) as e:
        return problems + [f"section0.xml 오류: {e}"]
    header = z.read("Contents/header.xml").decode("utf-8")
    para_ids = set(re.findall(r'<hh:paraPr id="(\d+)"', header))
    char_ids = set(re.findall(r'<hh:charPr id="(\d+)"', header))
    for p in root.iter(P + "p"):
        if p.get("paraPrIDRef") not in para_ids:
            problems.append(f"없는 문단 모양 ID: {p.get('paraPrIDRef')}")
        for r in p.findall(P + "run"):
            if r.get("charPrIDRef") not in char_ids:
                problems.append(f"없는 글자 모양 ID: {r.get('charPrIDRef')}")
    text = "".join(para_text(p) for p in root.iter(P + "p"))
    problems += [f"고정 문구 없음: {s}" for s in FIXED if s not in text]
    return sorted(set(problems))
