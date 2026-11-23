import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

from hwpx_io import HP, _parse_item, para_text, parse_phase_line, read_report
from schedule import Period, Phase, fmt_phase, is_continuing

DATA = Path(__file__).resolve().parent.parent / "data"
SAMPLES = ["20261109", "20261116"]
TITLE = "축수산 일반매입등록(70-2048) 개선 개발"


def d(m, day):
    return date(2026, m, day)


def load(name):
    return read_report(DATA / f"{name}.hwpx")


def test_dates_0116():
    r = load("20261116")
    assert r.report_date == d(11, 16)
    assert r.this_week == Period(d(11, 16), d(11, 20))
    assert r.next_week == Period(d(11, 23), d(11, 27))
    assert r.dept == "유통운영1팀"
    assert r.csr.period == Period(d(11, 9), d(11, 13))
    assert (r.csr.requested, r.csr.handled, r.csr.calls, r.csr.outages, r.csr.on_time) == (3, 3, 7, 0, 0)


def test_item_0116_page2_structured():
    r = load("20261116")
    it = [i for i in r.this_items if i.page == 2][0]
    assert it.title == TITLE and it.continued is True and it.raw is None
    assert it.requester == "농협유통 IT지원팀" and it.due == d(12, 2)
    assert it.schedule_label == "개발일정" and it.content[0] == "거래명세서 스캔본 첨부 기능 추가 개발"
    assert it.content[1].strip() == "전표 조회 시 첨부여부 확인 항목 추가"   # fwSpace는 공백으로 읽는다
    assert it.phases == [
        Phase("분석/설계", d(11, 9), d(11, 13)),
        Phase("개발/테스트", d(11, 16), d(11, 30)),
        Phase("본적용 및 모니터링", d(12, 2), None, open_ended=True),
    ]


def test_items_by_page_0116():
    r = load("20261116")
    titles = lambda items, page: [i.title for i in items if i.page == page]
    assert titles(r.this_items, 1) == ["매입원가산입 등록(70-2013) 개선 개발"]
    assert titles(r.this_items, 2) == [TITLE, "WMS 연계 신규 인터페이스 개발 진행"]
    assert titles(r.next_items, 1) == ["데몬 에러로그 조회(업무별) 신규 개발", "2026년 하반기 IT위탁업무 점검결과 대응"]
    assert titles(r.next_items, 2) == [TITLE, "하나로유통 경제지주 전환 물류 대응 보고"]
    raw = lambda items: [i.raw is not None for i in items]
    assert raw(r.next_items) == [False, False, False, True]       # 하나로유통: 사유·요청부서가 없는 건은 원문 복사
    assert raw(r.this_items) == [False, False, True]              # WMS: 일정 줄에 메모가 붙은 건은 원문 복사


def test_item_0109_schedule_label_and_single_day():
    it = [i for i in load("20261109").this_items if i.page == 1][0]
    assert it.schedule_label == "주요일정" and it.content_label == "개발내용"
    assert [p.name for p in it.phases] == ["개발일정", "본적용 및 모니터링"]
    assert it.phases[1] == Phase("본적용 및 모니터링", d(11, 16), d(11, 16))
    assert it.due == d(11, 16)


def test_csr_other_week():
    r = load("20261109")
    assert r.csr.calls == 5 and r.csr.period == Period(d(11, 2), d(11, 6))


def titles_of(*item_lists):
    return {i.title for items in item_lists for i in items}


def test_continued_matches_rule_for_all_sample_items():
    """(계속) = 전주 보고서(차주 칸은 같은 보고서의 금주 칸)에 있던 건이고 완료(예정)일이 그 주 이후"""
    r9, r16 = load("20261109"), load("20261116")
    cases = [  # (보고서, 칸, 주, 이전에 있던 건 제목) — 0109의 전주 보고서는 없으므로 있었다고 본다
        (r9.this_items, r9.this_week, None), (r9.next_items, r9.next_week, titles_of(r9.this_items)),
        (r16.this_items, r16.this_week, titles_of(r9.this_items, r9.next_items)),
        (r16.next_items, r16.next_week, titles_of(r16.this_items)),
    ]
    checked = 0
    for items, week, before in cases:
        for it in items:
            if it.raw is None:
                existed = True if before is None else it.title in before
                assert is_continuing(it, week, existed) == it.continued, it.title
                checked += 1
    assert checked == 9   # 구조화된 건 9개(원문 복사 건 제외) 모두가 규칙과 일치


@pytest.mark.parametrize("ph", [
    Phase("분석/설계", d(11, 9), d(11, 13)),
    Phase("본적용 및 모니터링", d(11, 30), None, open_ended=True),
    Phase("본적용 및 모니터링", d(11, 16), d(11, 16)),
])
def test_parse_phase_line_roundtrip(ph):
    assert parse_phase_line(fmt_phase(ph)) == ph


def test_irregular_items_kept_raw():
    # 일정 줄 뒤에 메모가 붙었거나 '개발사유' 머리말을 쓰는 건은 구조화하면 내용이 사라지므로 원문 복사
    r = load("20261116")
    items = [i for i in r.this_items + r.next_items if i.title.startswith("WMS 연계")]
    assert items and all(i.raw is not None for i in items)


# ---- 합성 문단으로 확인하는 읽기 규칙 (샘플에 없는 형태 포함) ----
def para(text_xml):
    return ET.fromstring(f'<hp:p xmlns:hp="{HP}"><hp:run charPrIDRef="12"><hp:t>{text_xml}</hp:t></hp:run></hp:p>')


def test_fwspace_is_read_as_space():
    assert para_text(para("거래명세서 첨부<hp:fwSpace/>기능")) == "거래명세서 첨부 기능"


def test_inline_schedule_header_is_split_from_previous_line():
    paras = [para("○ 제목"), para("  - 사유 : 이유"), para("  - 내용 : 첫줄"),
             para("           둘째 줄    - 개발일정"),
             para("    · 개발 : ‘26.11.23.(월) ~ ‘26.11.27.(금)"),
             para("  - 요청부서 : 팀"), para("  - 완료(예정)일 : ‘26.11.27.(금)")]
    it = _parse_item(paras, 1)
    assert it.raw is None and it.schedule_label == "개발일정"
    assert it.content == ["첫줄", "           둘째 줄"]
    assert it.phases == [Phase("개발", d(11, 23), d(11, 27))] and it.due == d(11, 27)


def test_phase_with_trailing_memo_is_kept_raw():
    paras = [para("○ 제목"), para("  - 사유 : 이유"), para("  - 요청부서 : 팀"),
             para("    · 본적용 : ‘26.11.11.(수)->11.18(수)")]
    assert _parse_item(paras, 1).raw is not None
