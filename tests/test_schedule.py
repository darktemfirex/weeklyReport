from datetime import date

from schedule import (Item, Period, Phase, calc_weeks, end_date, fmt_date, fmt_phase, is_continuing,
                       next_report_date, overlaps)


def d(m, day, y=2026):
    return date(y, m, day)


def item(*phases, due=None):
    return Item(title="t", continued=False, reason=[], content_label="내용", content=[],
                schedule_label="개발일정", phases=list(phases), requester="r", due=due)


CUT = Phase("본적용 및 모니터링", d(11, 30), None, open_ended=True)


def test_calc_weeks():
    w = calc_weeks(d(11, 30))
    assert w.this_week == Period(d(11, 30), d(12, 4))
    assert w.next_week == Period(d(12, 7), d(12, 11))
    assert w.csr_week == Period(d(11, 23), d(11, 27))


def test_next_report_date():
    assert next_report_date(d(11, 23)) == d(11, 30)


def test_fmt_date():
    assert fmt_date(d(11, 30)) == "‘26.11.30.(월)"


def test_fmt_phase_three_forms():
    assert fmt_phase(Phase("분석/설계", d(11, 9), d(11, 13))) == "분석/설계 : ‘26.11.09.(월) ~ ‘26.11.13.(금)"
    assert fmt_phase(CUT) == "본적용 및 모니터링 : ‘26.11.30.(월) ~"
    assert fmt_phase(Phase("본적용 및 모니터링", d(11, 16), d(11, 16))) == "본적용 및 모니터링 : ‘26.11.16.(월)"


def test_year_end():
    w = calc_weeks(d(12, 28))
    assert w.this_week.end == date(2027, 1, 1)
    assert fmt_date(w.this_week.end) == "‘27.01.01.(금)"


def test_continuing_when_existed_and_due_after_week():
    it = item(Phase("개발/테스트", d(11, 16), d(11, 30)), due=d(12, 2))
    assert is_continuing(it, Period(d(11, 16), d(11, 20)), existed=True) is True


def test_not_continuing_in_completion_week():
    it = item(Phase("개발일정", d(11, 6), d(11, 12)), due=d(11, 16))
    assert is_continuing(it, Period(d(11, 16), d(11, 20)), existed=True) is False


def test_not_continuing_when_new_this_week():
    it = item(Phase("분석/설계", d(11, 23), d(11, 27)), due=d(12, 16))
    assert is_continuing(it, Period(d(11, 23), d(11, 27)), existed=False) is False


def test_continuing_uses_last_phase_when_no_due():
    it = item(Phase("분석/설계", d(11, 23), d(11, 27)), Phase("개발일정", d(11, 30), d(12, 11)))
    assert is_continuing(it, Period(d(11, 30), d(12, 4)), existed=True) is True
    assert is_continuing(it, Period(d(12, 7), d(12, 11)), existed=True) is False


def test_open_ended_phase_counts_its_start_as_end():
    it = item(Phase("본적용 및 모니터링", d(12, 2), None, open_ended=True))
    assert end_date(it) == d(12, 2)


def test_not_continuing_without_any_date():
    assert is_continuing(item(), Period(d(11, 16), d(11, 20)), existed=True) is False


def test_due_week_boundary_friday_is_completion_week():
    it = item(due=d(11, 20))
    assert is_continuing(it, Period(d(11, 16), d(11, 20)), existed=True) is False


def test_overlaps_includes_cutover():
    it = item(Phase("개발/테스트", d(11, 16), d(11, 27)), CUT)
    assert overlaps(it, Period(d(11, 30), d(12, 4))) is True
    assert overlaps(it, Period(d(11, 2), d(11, 6))) is False


def test_in_week_public_helper():
    from schedule import in_week
    assert in_week(Phase("개발", d(11, 6), d(11, 12)), Period(d(11, 9), d(11, 13))) is True
    assert in_week(Phase("개발", d(11, 6), d(11, 12)), Period(d(11, 16), d(11, 20))) is False
    assert in_week(Phase("본적용", d(11, 30), None, open_ended=True), Period(d(12, 7), d(12, 11))) is True
    assert in_week(Phase("미정", None, None), Period(d(11, 9), d(11, 13))) is False
