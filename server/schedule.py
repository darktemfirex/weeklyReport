"""일정·날짜 로직: 모델, 날짜 표기, 주차 계산, 겹침·(계속) 판정 (모두 코드, LLM 없음)"""
from dataclasses import dataclass
from datetime import date, timedelta

WEEKDAYS = "월화수목금토일"


@dataclass(frozen=True)
class Period:
    start: date
    end: date


@dataclass(frozen=True)
class Phase:
    name: str
    start: date | None
    end: date | None
    open_ended: bool = False  # end==start 이고 open_ended가 False면 하루짜리


@dataclass
class Item:
    title: str            # (계속) 제외한 타이틀
    continued: bool
    reason: list[str]
    content_label: str    # "내용" 또는 "개발내용"
    content: list[str]
    schedule_label: str | None   # "개발일정" / "주요일정" / 머리 줄이 없으면 None
    phases: list[Phase]
    requester: str
    due: date | None
    page: int = 1                 # 1 = '7. 농/축/수산물 결함 및 개선사항' 구역, 2 = 2쪽
    raw: list[str] | None = None  # 형식이 다른 건: 문단 XML을 그대로 보관해 복사
    emphasis: list[str] | None = None   # 굵게 표시할 개발내용 줄들(금주에 추가한 줄). 렌더링 때만 쓰고 파일에서는 읽지 않는다


@dataclass(frozen=True)
class Weeks:
    report_date: date
    this_week: Period
    next_week: Period
    csr_week: Period


def week_of(d: date) -> Period:
    monday = d - timedelta(days=d.weekday())
    return Period(monday, monday + timedelta(days=4))


def next_report_date(latest: date) -> date:
    return latest + timedelta(days=7)


def calc_weeks(report_date: date) -> Weeks:
    this = week_of(report_date)
    shift = lambda p, n: Period(p.start + timedelta(days=n), p.end + timedelta(days=n))
    return Weeks(report_date, this, shift(this, 7), shift(this, -7))


def fmt_date(d: date) -> str:
    return f"‘{d:%y.%m.%d}.({WEEKDAYS[d.weekday()]})"


def fmt_range(p: Period) -> str:
    return f"{p.start:%m.%d} ~ {p.end:%m.%d}"


def fmt_phase(ph: Phase) -> str:
    text = f"{ph.name} : {fmt_date(ph.start)}"
    if ph.open_ended:
        return text + " ~"
    if ph.end and ph.end != ph.start:
        return f"{text} ~ {fmt_date(ph.end)}"
    return text


def in_week(ph: Phase, week: Period) -> bool:
    """일정 단계가 그 주(월~금)와 하루라도 겹치는지. 종료일 없는 단계는 끝없음으로 본다"""
    if not ph.start:
        return False
    end = date.max if ph.open_ended else (ph.end or ph.start)
    return ph.start <= week.end and end >= week.start


def overlaps(item: Item, week: Period) -> bool:
    return any(in_week(p, week) for p in item.phases)


def end_date(item: Item) -> date | None:
    """건이 끝나는 날: 완료(예정)일, 없으면 일정 단계 중 가장 늦은 날(종료일 없는 단계는 시작일)"""
    if item.due:
        return item.due
    days = [(p.end or p.start) for p in item.phases if p.start]
    return max(days) if days else None


def is_continuing(item: Item, week: Period, existed: bool = True) -> bool:
    """(계속) 판정: 이전 보고(금주는 전주 보고서, 차주는 같은 보고서의 금주)에 있던 건이고
    끝나는 날이 그 주 이후이면 True. 끝나는 주(완료(예정)일이 속한 주)와 새로 시작한 건은 False"""
    end = end_date(item)
    return existed and end is not None and end > week.end
