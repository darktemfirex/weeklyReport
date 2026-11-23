from datetime import date

from validation import validate_answer, validate_csr, validate_item_form

OK_PHASES = [{"name": "분석/설계", "start": date(2026, 12, 7), "end": date(2026, 12, 11), "open_ended": False}]
CSR = {"requested": 3, "handled": 3, "calls": 4, "outages": 0, "on_time": 0}


def test_item_form_ok():
    assert validate_item_form("사유", "내용", OK_PHASES, "IT지원팀") == []


def test_item_form_missing_fields():
    errors = validate_item_form("", " ", [], "")
    assert any("요청부서" in e for e in errors) and any("주요일정" in e for e in errors)
    assert any("하나 이상" in e for e in errors)                       # 프로그램명·사유·내용 중 하나는 있어야 AI가 쓸 수 있다
    assert not any("사유를 입력" in e or "내용을 입력" in e for e in errors)   # 사유·내용 각각은 필수가 아니다


def test_item_form_end_before_start():
    bad = [{"name": "개발", "start": date(2026, 12, 11), "end": date(2026, 12, 7), "open_ended": False}]
    assert any("종료일" in e for e in validate_item_form("사유", "내용", bad, "IT지원팀"))


def test_item_form_open_ended_needs_no_end():
    ph = [{"name": "본적용", "start": date(2026, 12, 7), "end": None, "open_ended": True}]
    assert validate_item_form("사유", "내용", ph, "IT지원팀") == []


def test_csr_valid():
    assert validate_csr(0, 0, 0, 0, 0) == []
    assert validate_csr(3, 3, 5, 0, 0) == []


def test_csr_handled_exceeds_requested():
    assert any("처리" in e for e in validate_csr(3, 4, 0, 0, 0))


def test_csr_negative():
    assert validate_csr(-1, 0, 0, 0, 0) != []


# ---- 전체 답 검사 (웹 API가 서버에서 다시 확인한다) ----
CANDS = [{"column": "this", "title": "A 건", "page": 1, "raw": False},
         {"column": "this", "title": "R 건", "page": 2, "raw": True}]
NEW = {"columns": ["next"], "page": 1, "reason": "사유", "content": "내용", "requester": "팀", "due": "2026-12-04",
       "phases": [{"name": "분석/설계", "start": "2026-11-30", "end": "2026-12-04", "open_ended": False}], "continued": None}


def answer(**kw):
    base = {"select": {"this": ["A 건"], "next": []}, "new": [], "csr": CSR, "continued": {}, "add_line": {"A 건": "한 줄"}}
    base.update(kw)
    return base


def test_answer_ok_at_minimum():
    a = answer(new=[dict(NEW), dict(NEW), dict(NEW), dict(NEW)])
    assert validate_answer(a, CANDS, minimum=5) == []


def test_answer_short_count():
    errors = validate_answer(answer(), CANDS, minimum=5)
    assert any("4건 부족" in e for e in errors)


def test_answer_missing_add_line_names_title_but_not_raw_items():
    a = answer(select={"this": ["A 건", "R 건"], "next": []}, add_line={})
    errors = validate_answer(a, CANDS, minimum=1)
    assert any("A 건" in e and "개발내용 1줄" in e for e in errors)
    assert not any("R 건" in e for e in errors)


def test_answer_new_item_errors_are_numbered():
    bad = dict(NEW, requester="")
    errors = validate_answer(answer(new=[dict(NEW), bad]), CANDS, minimum=1)
    assert any(e.startswith("신규 건 2:") and "요청부서" in e for e in errors)


def test_answer_new_item_needs_a_column():
    errors = validate_answer(answer(new=[dict(NEW, columns=[])]), CANDS, minimum=1)
    assert any("칸" in e for e in errors)


def test_answer_bad_date_text():
    bad = dict(NEW, phases=[{"name": "분석", "start": "내일", "end": None, "open_ended": False}])
    errors = validate_answer(answer(new=[bad]), CANDS, minimum=1)
    assert any("날짜 형식" in e for e in errors)


def test_answer_csr_errors():
    errors = validate_answer(answer(csr=dict(CSR, handled=9)), CANDS, minimum=1)
    assert any("처리건수" in e for e in errors)


def test_answer_new_item_must_pick_exactly_one_column():
    both = validate_answer(answer(new=[dict(NEW, columns=["this", "next"])]), CANDS, minimum=1)
    assert any("신규 건 1:" in e and "하나만" in e for e in both)
    assert validate_answer(answer(new=[dict(NEW, columns=["this"])]), CANDS, minimum=1) == []


def test_item_form_content_is_optional():
    assert validate_item_form("사유", "", OK_PHASES, "IT지원팀") == []


def test_answer_new_item_without_title_and_content_is_ok():
    assert validate_answer(answer(new=[dict(NEW, content="", title="")]), CANDS, minimum=1) == []


def test_answer_new_item_requires_requester_but_not_reason():
    assert validate_answer(answer(new=[dict(NEW, reason=" ")]), CANDS, minimum=1) == []      # 사유가 비면 AI가 작성
    errors = validate_answer(answer(new=[dict(NEW, requester="")]), CANDS, minimum=1)
    assert any("요청부서" in e for e in errors)


def test_requester_must_contain_hangul():
    errors = validate_item_form("사유", "", OK_PHASES, "IT")
    assert any("요청부서" in e and "한글" in e for e in errors)
    assert validate_item_form("사유", "", OK_PHASES, "농협유통 IT지원팀") == []     # 영문이 섞여도 한글이 있으면 통과
    assert validate_item_form("사유", "", OK_PHASES, "자체개선") == []


def test_empty_requester_reports_only_the_required_message():
    errors = validate_item_form("사유", "", OK_PHASES, "  ")
    assert sum("요청부서" in e for e in errors) == 1 and not any("한글" in e for e in errors)


def test_answer_new_item_requester_hangul_error_is_numbered():
    errors = validate_answer(answer(new=[dict(NEW, requester="ABC")]), CANDS, minimum=1)
    assert any(e.startswith("신규 건 1:") and "한글" in e for e in errors)


def test_phase_messages_use_main_schedule_wording():
    errors = validate_item_form("사유", "", [{"name": " ", "start": None, "end": None, "open_ended": False}], "팀")
    assert errors and all("개발일정" not in e for e in errors) and any("주요일정" in e for e in errors)


# ---- 사유 선택 · 한글 기준 · 타이틀 형식 필드 ----
def test_at_least_one_of_name_reason_content_is_needed():
    empty = dict(NEW, reason="", content="", title="", program_name="")
    errors = validate_answer(answer(new=[empty]), CANDS, minimum=1)
    assert any(e.startswith("신규 건 1:") and "하나 이상" in e for e in errors)
    for field in ({"program_name": "배송지시서"}, {"reason": "사유"}, {"content": "내용"}):
        assert not any("하나 이상" in e for e in validate_answer(answer(new=[dict(empty, **field)]), CANDS, minimum=1))


def test_every_text_field_must_contain_hangul_when_filled():
    cases = {"program_name": "프로그램명", "reason": "사유", "content": "내용", "requester": "요청부서"}
    for field, label in cases.items():
        errors = validate_answer(answer(new=[dict(NEW, **{field: "ABC 123"})]), CANDS, minimum=1)
        assert any(label in e and "한글" in e for e in errors), field
    assert validate_answer(answer(new=[dict(NEW, program_name="매입원가 등록 API", reason="API 연동 오류 개선")]), CANDS, minimum=1) == []


def test_custom_phase_name_must_contain_hangul():
    bad = dict(NEW, phases=[{"name": "QA", "start": "2026-11-30", "end": "2026-12-04", "open_ended": False}])
    assert any("한글" in e and "주요일정" in e for e in validate_answer(answer(new=[bad]), CANDS, minimum=1))


def test_program_code_format():
    for ok in ("70-2048", "A1_02", "7012"):
        assert validate_answer(answer(new=[dict(NEW, program_code=ok)]), CANDS, minimum=1) == [], ok
    errors = validate_answer(answer(new=[dict(NEW, program_code="70 2048!")]), CANDS, minimum=1)
    assert any("프로그램코드" in e for e in errors)


def test_work_type_must_be_known():
    assert validate_answer(answer(new=[dict(NEW, work_type="신규 개발")]), CANDS, minimum=1) == []
    assert validate_answer(answer(new=[dict(NEW, work_type="개선 개발")]), CANDS, minimum=1) == []
    assert any("구분" in e for e in validate_answer(answer(new=[dict(NEW, work_type="기타")]), CANDS, minimum=1))


def test_cutover_phase_end_date_is_not_validated():
    ph = [{"name": "본적용 및 모니터링", "start": date(2026, 12, 7), "end": date(2026, 12, 1), "open_ended": False}]
    assert validate_item_form("사유", "", ph, "IT지원팀") == []
    other = [{"name": "개발/테스트", "start": date(2026, 12, 7), "end": date(2026, 12, 1), "open_ended": False}]
    assert any("종료일" in e for e in validate_item_form("사유", "", other, "IT지원팀"))


def test_messages_call_the_field_dev_content():
    errors = validate_item_form("", "ABC", [], "")
    assert any("내용은" in e and "한글" in e for e in errors)
    assert any("프로그램명, 사유, 내용 중 하나 이상" in e for e in validate_item_form("", "", [], ""))
