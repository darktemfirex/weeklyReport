import dataclasses
import re
import zipfile
from datetime import date
from pathlib import Path

import pytest

from hwpx_io import P, Csr, cell_paras, load_section, page_cells, para_text, read_report
from hwpx_render import fmt_rate, preview_items, preview_text, render_report, verify_hwpx
from schedule import Period

DATA = Path(__file__).resolve().parent.parent / "data"
TEMPLATE = DATA / "20261116.hwpx"
SAMPLES = ["20261109", "20261116"]


def csr_texts(path):
    root, _ = load_section(path)
    tbl = next(t for t in root.iter(P + "tbl") if t.get("colCnt") == "7")
    cells = {(tc.find(P + "cellAddr").get("rowAddr"), tc.find(P + "cellAddr").get("colAddr")): para_text(cell_paras(tc)[0])
             for tr in tbl.findall(P + "tr") for tc in tr.findall(P + "tc")}
    return cells


@pytest.mark.parametrize("name", SAMPLES)
def test_roundtrip_samples(name, tmp_path):
    r = read_report(DATA / f"{name}.hwpx")
    out = tmp_path / f"{name}.hwpx"
    render_report(TEMPLATE, r, out)
    assert read_report(out) == r


def render_variant(tmp_path, **changes):
    r = dataclasses.replace(read_report(TEMPLATE), **changes)
    out = tmp_path / "20261123.hwpx"
    render_report(TEMPLATE, r, out)
    return r, out


def test_verify_passes_on_samples(tmp_path):
    r, out = render_variant(tmp_path)
    assert verify_hwpx(out) == []


def test_styles_come_from_template(tmp_path):
    _, out = render_variant(tmp_path)

    def used(path):
        root, _ = load_section(path)
        return {(p.get("paraPrIDRef"), r.get("charPrIDRef")) for p in root.iter(P + "p") for r in p.findall(P + "run")}

    assert used(out) <= used(TEMPLATE)


def test_prefix_preserved(tmp_path):
    _, out = render_variant(tmp_path)
    xml = zipfile.ZipFile(out).read("Contents/section0.xml")
    assert b"<hp:p " in xml and b"ns0:" not in xml
    assert xml.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hs:sec xmlns:ha=')


def test_mimetype_first_and_stored(tmp_path):
    _, out = render_variant(tmp_path)
    z = zipfile.ZipFile(out)
    assert z.infolist()[0].filename == "mimetype" and z.infolist()[0].compress_type == zipfile.ZIP_STORED


def test_empty_columns(tmp_path):
    r, out = render_variant(tmp_path, this_items=[], next_items=[])
    assert verify_hwpx(out) == []
    back = read_report(out)
    assert back.this_items == [] and back.next_items == []
    root, _ = load_section(out)
    assert "7. 농/축/수산물 결함 및 개선사항" in "".join(para_text(p) for p in root.iter(P + "p"))


def test_xml_escape_and_multiline(tmp_path):
    r = read_report(TEMPLATE)
    item = dataclasses.replace(r.this_items[0], reason=["A&B <C> '따옴표' \"큰\"", "둘째 줄"])
    r2, out = render_variant(tmp_path, this_items=[item])
    assert verify_hwpx(out) == []
    assert read_report(out).this_items[0].reason == ["A&B <C> '따옴표' \"큰\"", "둘째 줄"]


def test_fmt_rate():
    assert fmt_rate(0, 0) == "-"
    assert fmt_rate(3, 3) == "100%"
    assert fmt_rate(5, 7) == "71%"


def test_csr_cells_written(tmp_path):
    r = read_report(TEMPLATE)
    csr = Csr(Period(date(2026, 11, 23), date(2026, 11, 27)), 7, 5, 4, 1, 1)
    _, out = render_variant(tmp_path, csr=csr)
    cells = csr_texts(out)
    assert [cells[("2", str(c))] for c in range(1, 7)] == ["7", "5", "71%", "4", "1", "1"]
    assert [cells[("3", str(c))] for c in range(1, 7)] == ["7", "5", "71%", "4", "1", "1"]
    assert read_report(out).csr == csr


def test_csr_zero_requests(tmp_path):
    r = read_report(TEMPLATE)
    csr = Csr(r.csr.period, 0, 0, 0, 0, 0)
    _, out = render_variant(tmp_path, csr=csr)
    assert csr_texts(out)[("2", "3")] == "-"


def test_verify_catches_bad_style(tmp_path):
    _, out = render_variant(tmp_path)
    z = zipfile.ZipFile(out)
    bad = tmp_path / "bad.hwpx"
    with zipfile.ZipFile(bad, "w") as w:
        for info in z.infolist():
            data = z.read(info.filename)
            if info.filename == "Contents/section0.xml":
                data = data.replace(b'paraPrIDRef="33"', b'paraPrIDRef="9999"', 1)
            w.writestr(info, data, compress_type=info.compress_type)
    assert verify_hwpx(bad) != []


def test_year_end_header_dates(tmp_path):
    r = read_report(TEMPLATE)
    out = tmp_path / "20261228.hwpx"
    r2 = dataclasses.replace(r, report_date=date(2026, 12, 28), this_week=Period(date(2026, 12, 28), date(2027, 1, 1)),
                             next_week=Period(date(2027, 1, 4), date(2027, 1, 8)),
                             csr=dataclasses.replace(r.csr, period=Period(date(2026, 12, 21), date(2026, 12, 25))))
    render_report(TEMPLATE, r2, out)
    text = "".join(para_text(p) for p in load_section(out)[0].iter(P + "p"))
    assert "(12.28 ~ 01.01)" in text and "(01.04 ~ 01.08)" in text
    assert "‘26.12.21.(월) ~ ‘26.12.25.(금)" in text


def test_preview_text_mentions_titles():
    r = read_report(TEMPLATE)
    text = preview_text(r)
    assert "금주" in text and "축수산 일반매입등록(70-2048) 개선 개발" in text and "데몬 에러로그" in text


def test_phase_line_keeps_two_run_styles(tmp_path):
    _, out = render_variant(tmp_path)
    root, _ = load_section(out)
    phase = [p for p in root.iter(P + "p") if para_text(p).strip().startswith("· 분석/설계 : ‘26.11.09")][0]
    runs = phase.findall(P + "run")
    assert len(runs) == 2 and runs[0].get("charPrIDRef") != runs[1].get("charPrIDRef")   # 날짜는 견본의 좁은 자간 서식


# ---- 굵게 처리: 금주·차주 일정에 포함되는 일정 줄, 금주에 추가한 개발내용 줄 ----
from bold_utils import bold_flags, bold_of_char_pr  # noqa: E402


def test_phase_lines_in_week_are_bold_others_not(tmp_path):
    r, out = render_variant(tmp_path)
    # 금주(11.16~20) 매입원가 건: 본적용 11.16은 포함, 개발일정 11.06~11.12는 아님
    assert bold_flags(out, "· 본적용 및 모니터링 : ‘26.11.16") == {True}
    assert bold_flags(out, "· 개발일정 : ‘26.11.06") == {False}
    # 차주(11.23~27) 데몬 건: 분석/설계 11.23~11.27은 포함, 개발일정 11.30~은 아님
    assert bold_flags(out, "· 분석/설계 : ‘26.11.23") == {True}
    assert bold_flags(out, "· 개발일정 : ‘26.11.30") == {False}


def test_other_lines_are_not_forced_bold(tmp_path):
    _, out = render_variant(tmp_path)
    assert bold_flags(out, "- 요청부서") == {False}
    assert bold_flags(out, "- 사유") == {False}


def test_emphasis_line_is_bold(tmp_path):
    r = read_report(TEMPLATE)
    base = r.this_items[0]
    line = "    · 이번 주 진행 상황 한 줄"
    item = dataclasses.replace(base, content=[*base.content, line], emphasis=[line])
    _, out = render_variant(tmp_path, this_items=[item])
    assert bold_flags(out, "· 이번 주 진행 상황 한 줄") == {True}
    assert bold_flags(out, base.content[0].strip()) == {False}


def test_same_item_bold_depends_on_column_week(tmp_path):
    r = read_report(TEMPLATE)
    item = r.next_items[0]                    # 분석/설계 11.23~27: 차주 주간에만 포함
    _, out = render_variant(tmp_path, this_items=[item], next_items=[item])
    texts = [(t, k) for t, k in __import__("bold_utils").paragraph_bold(out) if t.strip().startswith("· 분석/설계 : ‘26.11.23")]
    assert [k for _, k in texts] == [{False}, {True}]    # 금주 칸 False, 차주 칸 True


def test_header_stays_valid_after_new_bold_styles(tmp_path):
    import re
    _, out = render_variant(tmp_path)
    header = zipfile.ZipFile(out).read("Contents/header.xml").decode("utf-8")
    count = int(re.search(r'<hh:charProperties itemCnt="(\d+)"', header).group(1))
    assert count == len(re.findall(r'<hh:charPr id="\d+"', header)) == len(bold_of_char_pr(out))
    assert verify_hwpx(out) == []


# ---- 머리 줄의 불필요한 콜론 ----
def texts(path):
    from bold_utils import paragraph_bold
    return [t.strip() for t, _ in paragraph_bold(path)]


def test_content_header_has_no_colon_when_bullets_follow(tmp_path):
    r = read_report(TEMPLATE)
    item = dataclasses.replace(r.this_items[0], content_label="내용", content=["    · 첫 항목", "    · 둘째 항목"])
    _, out = render_variant(tmp_path, this_items=[item])
    lines = texts(out)
    assert "- 내용" in lines and "- 내용 :" not in lines          # 머리 줄만 있는 "- 내용 :"은 없다(같은 줄 내용은 다른 건에서 허용)
    assert read_report(out).this_items[0].content_label == "내용"
    assert [c.strip() for c in read_report(out).this_items[0].content] == ["· 첫 항목", "· 둘째 항목"]


def test_content_header_keeps_colon_when_text_is_on_the_same_line(tmp_path):
    r = read_report(TEMPLATE)
    page2 = [i for i in r.this_items if i.page == 2][0]          # 샘플의 '- 내용 : 거래명세서…' 형태
    _, out = render_variant(tmp_path, this_items=[page2])
    assert "- 내용 : 거래명세서 스캔본 첨부 기능 추가 개발" in texts(out)


def test_empty_due_has_no_dangling_colon_and_reads_back(tmp_path):
    r = read_report(TEMPLATE)
    item = dataclasses.replace(r.this_items[0], due=None)
    _, out = render_variant(tmp_path, this_items=[item])
    lines = texts(out)
    assert "- 완료(예정)일" in lines and not any(t.startswith("- 완료(예정)일 :") and t.endswith(":") for t in lines)
    assert read_report(out).this_items[0].due is None
    assert any(t.startswith("- 완료(예정)일 : ‘26.") for t in texts(render_variant(tmp_path)[1]))   # 날짜가 있으면 그대로


# ---- 승인 화면용 건별 미리보기: 신규 여부와 확인에 필요한 필드 ----
def test_preview_items_flag_new_items_and_expose_the_fields_to_check():
    r = read_report(TEMPLATE)
    items = preview_items(r, {("next", 0)})
    assert len(items) == len(r.this_items) + len(r.next_items)
    this = [i for i in items if i["column"] == "this"]
    nxt = [i for i in items if i["column"] == "next"]
    assert not any(i["new"] for i in this)
    assert [i["new"] for i in nxt] == [True] + [False] * (len(nxt) - 1)
    first = nxt[0]
    assert first["title"] == r.next_items[0].title and first["requester"] == r.next_items[0].requester
    assert first["due"].startswith("‘26.") and first["phases"][0].startswith(r.next_items[0].phases[0].name)
    assert first["reason"] == r.next_items[0].reason and first["content"] == r.next_items[0].content


def test_preview_items_mark_raw_items_and_added_lines():
    r = read_report(TEMPLATE)
    raw = [i for i in preview_items(r, set()) if i["raw"]]
    assert raw and all(i["reason"] == [] and i["phases"] == [] for i in raw)
    base = r.this_items[0]
    line = "    · 새로 추가한 줄"
    item = dataclasses.replace(base, content=[*base.content, line], emphasis=[line])
    assert preview_items(dataclasses.replace(r, this_items=[item]), set())[0]["added"] == [line]
