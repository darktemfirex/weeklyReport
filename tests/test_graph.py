import asyncio
import json
import time
from pathlib import Path

import pytest

import graph
import tools
from agent import Composed
from hwpx_io import P, load_section, para_text, read_report

TITLE = "축수산 일반매입등록(70-2048) 개선 개발"
CSR = {"requested": 4, "handled": 4, "calls": 3, "outages": 0, "on_time": 0}
NEW = {"columns": ["next"], "page": 1, "reason": "사유 원문", "content": "내용 원문",
       "phases": [{"name": "분석/설계", "start": "2026-11-30", "end": "2026-12-04", "open_ended": False}],
       "requester": "IT지원팀", "due": "2026-12-04"}

THIS_ONLY = {"phases": [{"name": "분석/설계", "start": "2026-11-23", "end": "2026-11-27", "open_ended": False}], "due": "2026-11-27"}      # 금주 안에 끝나는 건: 차주로 복사되지 않는다


def confirm(new=None, select=None):
    select = select if select is not None else {"this": [TITLE], "next": []}
    return {"select": select, "new": [NEW] if new is None else new, "csr": CSR, "continued": {},
            "add_line": {t: "금주 진행 상황" for t in select["this"]}}     # 금주 이월 건은 개발내용 1줄이 필수


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(graph, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(graph, "MIN_ITEMS", 1)     # 최소 건수 규칙은 전용 테스트에서만 켠다
    return tmp_path


class Compose:
    def __init__(self, title="신규 테스트 개발", echo=True):
        self.title, self.calls, self.feedbacks = title, 0, []

    async def __call__(self, inp, feedback=""):
        self.calls += 1
        self.feedbacks.append(feedback)
        self.contexts = getattr(self, "contexts", []) + [inp.context]
        body = inp.content or f"{inp.reason} 관련 내용을 정리함"     # 내용이 비면 사유를 보고 쓴 내용(AI 역할)
        reason = inp.reason or "내용을 바탕으로 AI가 쓴 사유"            # 사유가 비면 AI가 작성
        return Composed(self.title, [reason], [body])


def drive(compose, answers, report_date=None, prev_name=None, review=None):
    app = graph.build_graph(compose, tools.call_local, review=review)
    return asyncio.run(graph.run(app, report_date, list(answers), show=False, prev_name=prev_name))


def test_happy_path(env):
    path, final, _ = drive(Compose(), [confirm(), "승인"])
    assert path == ["직전보고 읽기", "주차 계산", "이월 후보", "사용자 확인", "초안", "AI 점검", "계속 표시", "검증", "사용자 승인", "저장"]
    out = env / "reports" / "20261123.hwpx"
    assert out.exists() and "저장 완료" in final["result"]
    r = read_report(out)
    assert str(r.this_week.start) == "2026-11-23" and str(r.next_week.end) == "2026-12-04"
    assert [i.title for i in r.this_items] == [TITLE]
    assert [i.title for i in r.next_items if i.page == 1] == ["신규 테스트 개발"]
    assert r.csr.requested == 4 and str(r.csr.period.start) == "2026-11-16"


def test_continued_marks_follow_previous_report_and_due(env):
    answer = confirm(select={"this": [TITLE], "next": [TITLE]})
    drive(Compose(), [answer, "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert r.this_items[0].continued is True        # 전주 보고서에 있었고 완료(예정)일 12.02가 금주(11.23~27) 이후
    next_titles = [(i.title, i.continued) for i in r.next_items]
    assert (TITLE, False) in next_titles            # 12.02는 차주(11.30~12.04)에 속함 = 끝나는 주
    assert ("신규 테스트 개발", False) in next_titles  # 새로 시작한 건


def test_new_item_continued_override(env):
    forced = dict(NEW, continued=True)
    drive(Compose(), [confirm(new=[forced]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert r.next_items[0].continued is True


def test_carry_candidates(env):
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    paused = asyncio.run(graph.advance(app, config, {"report_date": None}, []))
    this = [c["title"] for c in paused["후보"] if c["column"] == "this"]
    assert TITLE in this
    assert paused["주차"]["this_week"]["start"] == "2026-11-23"


def test_invented_number_retries_then_handoff(env):
    compose = Compose(title="신규 2099 개발")
    path, final, _ = drive(compose, [confirm()])
    assert path.count("초안") == 1 + graph.MAX_RETRY and compose.calls == 1 + graph.MAX_RETRY
    assert "규칙1" in compose.feedbacks[1]
    assert final["result"].startswith("사람 이관")
    assert not (env / "reports").exists() or list((env / "reports").glob("*.hwpx")) == []


def test_reject_twice_handoff(env):
    path, final, _ = drive(Compose(), [confirm(), "반려: 문구 수정", confirm(), "반려: 다시"])
    assert path.count("초안") == 2 and path.count("사용자 확인") == 2 and final["result"].startswith("사람 이관")
    assert "2번 반려" in final["result"]


def test_existing_file_needs_overwrite(env):
    (env / "reports").mkdir()
    old = env / "reports" / "20261123.hwpx"
    old.write_bytes(b"old")
    _, final, _ = drive(Compose(), [confirm(), "승인"])
    assert final["result"].startswith("사람 이관") and old.read_bytes() == b"old"
    _, final2, _ = drive(Compose(), [confirm(), "승인 덮어쓰기"])
    assert "저장 완료" in final2["result"] and old.read_bytes() != b"old"


def test_pii_masked_in_log_and_output(env):
    pii = dict(NEW, content="담당 010-1234-5678 문의")
    _, _, log_path = drive(Compose(), [confirm(new=[pii]), "승인"])
    assert "010-1234-5678" not in log_path.read_text(encoding="utf-8")
    root, _ = load_section(env / "reports" / "20261123.hwpx")
    assert "010-1234-5678" not in "".join(para_text(p) for p in root.iter(P + "p"))


def test_log_records_tool_calls_and_approval(env):
    _, _, log_path = drive(Compose(), [confirm(), "승인"])
    kinds = [json.loads(l)["종류"] for l in log_path.read_text(encoding="utf-8").splitlines()]
    assert {"시작", "도구", "승인", "끝"} <= set(kinds)


def test_save_blocked_without_approval(env):
    # 권한: 승인 전에는 저장 도구가 실행되지 않는다
    app = graph.build_graph(Compose(), tools.call_local)
    config, log = graph.new_run(None)
    state = {"approved": False}
    out = asyncio.run(graph.guarded_call(tools.call_local, "save_report", {"report_json": "{}", "out_name": "x.hwpx"}, state, log))
    assert out is None


def test_raw_candidates_only_in_original_column(env):
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    paused = asyncio.run(graph.advance(app, config, {"report_date": None}, []))
    cols = [c["column"] for c in paused["후보"] if c["title"].startswith("하나로유통")]
    assert cols == ["next"]      # 샘플에서 차주 칸에만 있던 건: 두 칸에 중복 제안하지 않는다


def test_pii_in_reject_reason_not_sent_to_compose(env):
    compose = Compose()
    drive(compose, [confirm(), "반려: 담당010-1234-5678 확인", confirm(), "승인"])
    assert all("1234-5678" not in f.replace("****", "") for f in compose.feedbacks)
    assert any("010-****-5678" in f for f in compose.feedbacks)


def test_attached_previous_report_is_used(env, monkeypatch):
    monkeypatch.setattr(tools, "UPLOADS_DIR", env / "uploads")
    monkeypatch.setattr(tools, "_extra", [])
    monkeypatch.setattr(tools, "_store", None)
    tools.save_upload("20261109.hwpx", (Path(__file__).resolve().parent.parent / "data" / "20261109.hwpx").read_bytes())
    answer = confirm(select={"this": ["매입원가산입 등록(70-2013) 개선 개발"], "next": []})
    _, final, _ = drive(Compose(), [answer, "승인"], prev_name="20261109.hwpx")
    out = env / "reports" / "20261116.hwpx"          # 첨부한 0109 + 7일, data의 최신(0116)+7일(0123)이 아님
    assert out.exists() and "저장 완료" in final["result"]
    r = read_report(out)
    assert str(r.this_week.start) == "2026-11-16" and verify_ok(out)
    assert [i.title for i in r.this_items] == ["매입원가산입 등록(70-2013) 개선 개발"]


def verify_ok(path):
    from hwpx_render import verify_hwpx
    return verify_hwpx(path) == []


def test_add_line_applies_to_this_week_only(env):
    answer = dict(confirm(select={"this": [TITLE], "next": [TITLE]}), add_line={TITLE: "금주 진행 상황 한 줄"})
    drive(Compose(), [answer, "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    this_item = [i for i in r.this_items if i.title == TITLE][0]
    next_item = [i for i in r.next_items if i.title == TITLE][0]
    assert this_item.content[-1].strip() == "금주 진행 상황 한 줄"          # 기존 건이 글머리 없이 이어 쓰는 형식이라 같은 형식
    assert not any("금주 진행 상황" in line for line in next_item.content)
    assert len(this_item.content) == len(next_item.content) + 1


def test_add_line_masks_pii(env):
    answer = dict(confirm(select={"this": [TITLE], "next": []}), add_line={TITLE: "담당 010-1234-5678"})
    drive(Compose(), [answer, "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert [i for i in r.this_items if i.title == TITLE][0].content[-1].strip() == "담당 010-****-5678"


def test_min_items_asks_again_until_enough(env, monkeypatch):
    monkeypatch.setattr(graph, "MIN_ITEMS", 5)
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    path = []
    asyncio.run(graph.advance(app, config, {"report_date": None}, path))
    from langgraph.types import Command
    paused = asyncio.run(graph.advance(app, config, Command(resume=confirm()), path))   # 2건뿐
    assert "3건 부족" in paused["오류"]
    titles = [TITLE, "데몬 에러로그 조회(업무별) 신규 개발", "2026년 하반기 IT위탁업무 점검결과 대응", "매입원가산입 등록(70-2013) 개선 개발"]
    enough = confirm(select={"this": titles[:3], "next": [titles[1]]})               # 3+1+신규1 = 5건(경계)
    paused = asyncio.run(graph.advance(app, config, Command(resume=enough), path))
    assert "오류" not in paused and "미리보기" in paused


def test_count_items_counts_both_columns():
    answer = {"select": {"this": ["a", "b"], "next": ["a"]}, "new": [dict(NEW, columns=["this", "next"]), dict(NEW)]}
    assert graph.count_items(answer) == 3 + 2 + 1


def test_candidates_flag_raw_items(env):
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    paused = asyncio.run(graph.advance(app, config, {"report_date": None}, []))
    flags = {c["title"]: c["raw"] for c in paused["후보"]}
    assert flags["하나로유통 경제지주 전환 물류 대응 보고"] is True and flags[TITLE] is False


def test_missing_add_lines_helper():
    cands = [{"column": "this", "title": "A", "page": 1, "raw": False}, {"column": "this", "title": "R", "page": 2, "raw": True}]
    answer = {"select": {"this": ["A", "R"], "next": ["A"]}, "add_line": {}}
    assert graph.missing_add_lines(answer, cands) == ["A"]            # 원문 복사 건(R)과 차주 칸은 제외
    answer["add_line"] = {"A": "  한 줄  "}
    assert graph.missing_add_lines(answer, cands) == []
    assert graph.missing_add_lines({"select": {"this": ["A"]}, "add_line": {"A": "   "}}, cands) == ["A"]


def test_this_week_item_without_add_line_is_asked_again(env):
    from langgraph.types import Command
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    path = []
    asyncio.run(graph.advance(app, config, {"report_date": None}, path))
    no_line = dict(confirm(), add_line={})
    paused = asyncio.run(graph.advance(app, config, Command(resume=no_line), path))
    assert "개발내용 1줄" in paused["오류"] and TITLE in paused["오류"]
    paused = asyncio.run(graph.advance(app, config, Command(resume=confirm()), path))
    assert "오류" not in paused and "미리보기" in paused


def test_raw_item_needs_no_add_line(env):
    from langgraph.types import Command
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    path = []
    asyncio.run(graph.advance(app, config, {"report_date": None}, path))
    raw_only = {"select": {"this": ["하나로유통 경제지주 전환 물류 대응 보고"], "next": []}, "new": [], "csr": CSR,
                "continued": {}, "add_line": {}}
    paused = asyncio.run(graph.advance(app, config, Command(resume=raw_only), path))
    assert "오류" not in paused and "미리보기" in paused


def test_added_line_is_bold_in_output(env):
    from bold_utils import bold_flags
    answer = dict(confirm(select={"this": [TITLE], "next": [TITLE]}), add_line={TITLE: "금주 진행 상황 한 줄"})
    drive(Compose(), [answer, "승인"])
    out = env / "reports" / "20261123.hwpx"
    assert bold_flags(out, "금주 진행 상황 한 줄") == {True}       # 금주 칸에만 있고 굵게
    assert bold_flags(out, "· 거래명세서") == set() or bold_flags(out, "· 거래명세서") == {False}


def test_default_minimum_is_five_and_boundary():
    assert graph.MIN_ITEMS == 5     # 다른 테스트의 monkeypatch는 각자 되돌려진다
    four = {"select": {"this": ["a", "b", "c"], "next": ["a"]}, "new": []}
    five = {"select": {"this": ["a", "b", "c"], "next": ["a", "b"]}, "new": []}
    assert graph.count_items(four) < graph.MIN_ITEMS <= graph.count_items(five)


def test_user_title_is_used_and_its_digits_are_allowed(env):
    titled = dict(NEW, title="배송지시서 출력(70-9999) 개선 개발")
    path, final, _ = drive(Compose(), [confirm(new=[titled]), "승인"])
    assert path.count("초안") == 1 and "저장 완료" in final["result"]
    r = read_report(env / "reports" / "20261123.hwpx")
    assert "배송지시서 출력(70-9999) 개선 개발" in [i.title for i in r.next_items]
    assert "신규 테스트 개발" not in [i.title for i in r.next_items]       # 모델이 만든 타이틀은 쓰지 않는다


def test_empty_content_is_written_by_ai_from_the_reason(env):
    no_content = dict(NEW, content="")
    drive(Compose(), [confirm(new=[no_content]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    item = [i for i in r.next_items if i.title == "신규 테스트 개발"][0]
    assert item.reason == ["사유 원문"]
    assert [line.strip() for line in item.content] == ["사유 원문 관련 내용을 정리함"]


def test_blank_title_and_content_both_filled_by_ai(env):
    bare = dict(NEW, content="", title="")
    drive(Compose(), [confirm(new=[bare]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    item = [i for i in r.next_items if i.title == "신규 테스트 개발"][0]
    assert item.content and item.title == "신규 테스트 개발"


def test_new_item_without_page_goes_to_page_one(env):
    page_less = {k: v for k, v in NEW.items() if k != "page"}
    drive(Compose(), [confirm(new=[page_less]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert [i.page for i in r.next_items if i.title == "신규 테스트 개발"] == [1]


def test_new_item_schedule_header_is_main_schedule(env):
    drive(Compose(), [confirm(), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    item = [i for i in r.next_items if i.title == "신규 테스트 개발"][0]
    assert item.schedule_label == "주요일정"


def test_title_is_assembled_from_program_name_code_and_type(env):
    item = {k: v for k, v in NEW.items() if k != "title"}
    item.update(program_name="배송지시서 출력", program_code="70-1234", work_type="신규 개발")
    path, final, _ = drive(Compose(), [confirm(new=[item]), "승인"])
    assert path.count("초안") == 1 and "저장 완료" in final["result"]       # 코드의 숫자는 입력이라 허용된다
    r = read_report(env / "reports" / "20261123.hwpx")
    assert "배송지시서 출력(70-1234) 신규 개발" in [i.title for i in r.next_items]


def test_title_without_code_and_default_type(env):
    item = dict(NEW, program_name="배송지시서 출력")
    drive(Compose(), [confirm(new=[item]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert "배송지시서 출력 개선 개발" in [i.title for i in r.next_items]


def test_explicit_title_key_still_wins(env):
    item = dict(NEW, title="직접 쓴 타이틀", program_name="무시될 이름")
    drive(Compose(), [confirm(new=[item]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert "직접 쓴 타이틀" in [i.title for i in r.next_items]


def test_blank_reason_is_written_by_ai(env):
    item = dict(NEW, reason="", content="내용만 입력")
    drive(Compose(), [confirm(new=[item]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    new = [i for i in r.next_items if i.title == "신규 테스트 개발"][0]
    assert new.reason == ["내용을 바탕으로 AI가 쓴 사유"]


def test_reject_goes_back_to_user_confirm_and_shows_the_reason(env):
    from langgraph.types import Command
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    path = []
    asyncio.run(graph.advance(app, config, {"report_date": None}, path))
    paused = asyncio.run(graph.advance(app, config, Command(resume=confirm()), path))
    assert "미리보기" in paused                                                   # 3단계(승인)
    back = asyncio.run(graph.advance(app, config, Command(resume="반려: 사유를 더 구체적으로"), path))
    assert "후보" in back and back["반려사유"] == "사유를 더 구체적으로"          # 2단계(내용 입력)로 돌아온다
    again = asyncio.run(graph.advance(app, config, Command(resume=confirm()), path))
    assert "미리보기" in again and "반려사유" not in again                       # 다시 생성하면 승인 단계


def test_reject_count_survives_reconfirm_so_second_reject_hands_off(env):
    path, final, _ = drive(Compose(), [confirm(), "반려: 하나", confirm(), "반려: 둘"])
    assert final["result"].startswith("사람 이관") and path[-1] == "사람 전담"


def test_rejection_reason_reaches_compose_on_regeneration(env):
    compose = Compose()
    drive(compose, [confirm(), "반려: 문구를 짧게", confirm(), "승인"])
    assert compose.feedbacks[0] == "" and "문구를 짧게" in compose.feedbacks[1]


# ---- 개발내용 이름 · 본적용 종료일 · AI 점검 · 보고체 · 전주 맥락 ----
class Review:
    """점검 단계 가짜: 타이틀 끝에 ' 점검'을 붙이고, 받은 입력·맥락을 기록한다"""

    def __init__(self, fail=False):
        self.calls, self.fail, self.seen = 0, fail, []

    async def __call__(self, inp, composed):
        self.calls += 1
        self.seen.append((inp.reason, inp.context, composed.title))
        if self.fail:
            raise ValueError("점검 결과를 읽을 수 없음")
        return Composed(composed.title + " 점검", composed.reason, composed.content)


def test_new_item_content_header_is_dev_content(env):
    drive(Compose(), [confirm(), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert [i.content_label for i in r.next_items if i.title == "신규 테스트 개발"] == ["내용"]      # 단일 항목은 형식 A


def test_ai_review_runs_after_draft_for_new_items_only(env):
    review = Review()
    path, final, _ = drive(Compose(), [confirm(), "승인"], review=review)
    assert path[path.index("초안") + 1] == "AI 점검"
    assert review.calls == 1                                       # 신규 건 1개만, 이월 건은 점검하지 않는다
    r = read_report(env / "reports" / "20261123.hwpx")
    assert "신규 테스트 개발 점검" in [i.title for i in r.next_items]
    assert TITLE in [i.title for i in r.this_items]                # 이월 건은 그대로


def test_ai_review_failure_keeps_the_draft(env):
    path, final, _ = drive(Compose(), [confirm(), "승인"], review=Review(fail=True))
    assert "저장 완료" in final["result"]
    r = read_report(env / "reports" / "20261123.hwpx")
    assert "신규 테스트 개발" in [i.title for i in r.next_items]


def test_ai_review_also_checks_a_user_typed_title(env):
    typed = dict(NEW, title="사용자가 쓴 타이틀")
    review = Review()
    drive(Compose(), [confirm(new=[typed]), "승인"], review=review)
    assert review.seen[0][2] == "사용자가 쓴 타이틀"                 # 점검기는 사용자 타이틀도 받아 본다
    r = read_report(env / "reports" / "20261123.hwpx")
    assert "사용자가 쓴 타이틀 점검" in [i.title for i in r.next_items]


def test_previous_report_context_is_given_to_compose_and_review(env):
    compose, review = Compose(), Review()
    drive(compose, [confirm(), "승인"], review=review)
    assert TITLE in compose.contexts[0] and "개발내용" in compose.contexts[0]
    assert compose.contexts[0] == review.seen[0][1]
    assert "하나로유통" not in compose.contexts[0]                  # 원문 복사 건은 맥락에서 제외


def test_polite_text_is_rejected_by_rule3_and_rewritten(env):
    class Polite(Compose):
        async def __call__(self, inp, feedback=""):
            out = await super().__call__(inp, feedback)
            if "규칙3" not in feedback:
                return Composed(out.title, ["이유를 설명드립니다"], out.content)
            return out

    compose = Polite()
    path, final, _ = drive(compose, [confirm(), "승인"])
    assert compose.calls == 2 and "규칙3" in compose.feedbacks[1] and "저장 완료" in final["result"]
    assert path.count("초안") == 2


def test_cutover_phase_ignores_end_date_and_is_single_day(env):
    phases = [{"name": "분석/설계", "start": "2026-11-30", "end": "2026-12-01", "open_ended": False},
              {"name": "본적용 및 모니터링", "start": "2026-12-02", "end": "2026-12-04", "open_ended": False}]
    drive(Compose(), [confirm(new=[dict(NEW, phases=phases)]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    item = [i for i in r.next_items if i.title == "신규 테스트 개발"][0]
    cut = [p for p in item.phases if p.name.startswith("본적용")][0]
    assert cut.start == cut.end and cut.open_ended is False and str(cut.start) == "2026-12-02"
    assert [p.end for p in item.phases if not p.name.startswith("본적용")][0].isoformat() == "2026-12-01"


def test_cutover_open_ended_flag_is_also_normalized(env):
    phases = [{"name": "본적용 및 모니터링", "start": "2026-12-02", "end": None, "open_ended": True}]
    drive(Compose(), [confirm(new=[dict(NEW, phases=phases)]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    item = [i for i in r.next_items if i.title == "신규 테스트 개발"][0]
    assert item.phases[0].open_ended is False and item.phases[0].end == item.phases[0].start


# ---- 줄바꿈·들여쓰기, 개발내용 콜론 형식, 맨 아래 추가 ----
from bold_utils import paragraph_bold  # noqa: E402
from layout import LIMIT, text_width  # noqa: E402


def raw_lines(path):
    return [text for text, _ in paragraph_bold(path)]


def test_long_reason_is_wrapped_at_word_boundaries_with_hanging_indent(env):
    long_reason = "배송 지시서를 출력할 때 일부 상품명이 잘려서 출력되어 현장에서 문의가 계속 발생하고 있어 출력 항목 길이를 보완하고 줄바꿈 처리를 추가하려는 건"
    drive(Compose(), [confirm(new=[dict(NEW, reason=long_reason)]), "승인"])
    lines = raw_lines(env / "reports" / "20261123.hwpx")
    start = next(i for i, t in enumerate(lines) if t.startswith("  - 사유 : 배송 지시서를"))
    block, j = [lines[start]], start + 1
    while lines[j].startswith(" " * 11) and not lines[j].lstrip().startswith("-"):
        block.append(lines[j])
        j += 1
    assert len(block) >= 3
    assert all(text_width(line) <= LIMIT for line in block)
    assert all(line.startswith(" " * 11) and not line.startswith(" " * 12) for line in block[1:])      # 이어지는 줄은 11칸 들여쓰기
    joined = " ".join([block[0].split(" : ", 1)[1]] + [line.strip() for line in block[1:]])
    assert joined == long_reason                                                                      # 단어가 끊기지 않음


def test_dev_content_is_inline_after_colon_without_bullets(env):
    drive(Compose(), [confirm(new=[dict(NEW, content="원가산입 내역 조회 시 합계 누락")]), "승인"])
    lines = raw_lines(env / "reports" / "20261123.hwpx")
    at = lines.index("  - 내용 : 원가산입 내역 조회 시 합계 누락")           # 형식 A: 항목이 하나면 '내용 :' 뒤에 이어 쓴다
    assert lines[at + 1].startswith("  - 주요일정")                      # 다음 줄은 글머리가 아니라 다음 항목
    assert "  - 개발내용" not in lines


def test_multi_item_dev_content_uses_form_b_bullets(env):
    drive(Compose(), [confirm(new=[dict(NEW, content="첫 항목 내용\n둘째 항목 내용")]), "승인"])
    out = env / "reports" / "20261123.hwpx"
    lines = raw_lines(out)
    at = lines.index("  - 개발내용")                                    # 형식 B: 머리 줄 아래에 · 항목
    assert lines[at + 1] == "    · 첫 항목 내용" and lines[at + 2] == "    · 둘째 항목 내용"
    item = [i for i in read_report(out).next_items if i.title == "신규 테스트 개발"][0]
    assert item.content_label == "개발내용" and item.content == ["    · 첫 항목 내용", "    · 둘째 항목 내용"]   # 다시 읽어도 같은 구조


def test_new_items_are_appended_at_the_bottom_of_their_column(env):
    title1 = "매입원가산입 등록(70-2013) 개선 개발"           # 직전 보고서에서 1쪽, TITLE은 2쪽
    sel = {"this": [title1, TITLE], "next": [TITLE]}
    new_this, new_next = dict(NEW, columns=["this"], **THIS_ONLY), dict(NEW, columns=["next"])
    drive(Compose(), [confirm(select=sel, new=[new_this, new_next]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert [i.title for i in r.this_items] == [title1, TITLE, "신규 테스트 개발"]
    assert [i.page for i in r.this_items] == [1, 2, 2]                 # 이어지는 건의 가장 아래 = 마지막 건과 같은 쪽
    assert [i.title for i in r.next_items] == [TITLE, "신규 테스트 개발"]
    assert [i.page for i in r.next_items] == [2, 2]


def test_new_items_keep_their_entry_order_at_the_bottom(env):
    first, second = dict(NEW, program_name="첫째 프로그램"), dict(NEW, program_name="둘째 프로그램")
    drive(Compose(), [confirm(select={"this": [TITLE], "next": []}, new=[first, second]), "승인"])
    r = read_report(env / "reports" / "20261123.hwpx")
    assert [i.title for i in r.next_items] == ["첫째 프로그램 개선 개발", "둘째 프로그램 개선 개발"]


def test_added_line_for_a_carried_item_is_wrapped_and_all_lines_are_bold(env):
    from bold_utils import bold_flags
    long_line = "이번 주에는 거래명세서 첨부 화면의 수산부 적용 범위를 확대하고 전표 조회 항목을 추가하는 작업을 진행하여 현장 확인까지 마침"
    answer = dict(confirm(), add_line={TITLE: long_line})
    drive(Compose(), [answer, "승인"])
    out = env / "reports" / "20261123.hwpx"
    item = [i for i in read_report(out).this_items if i.title == TITLE][0]
    added = [line for line in item.content[2:]]                              # 기존 2줄 뒤에 붙은 줄들
    assert len(added) >= 2 and all(text_width(line) <= LIMIT for line in added)
    assert " ".join(line.strip() for line in added) == long_line
    for line in added:
        assert bold_flags(out, line.strip()) == {True}


def test_dev_content_that_does_not_fit_wraps_at_a_word_under_the_text(env):
    drive(Compose(), [confirm(new=[dict(NEW, content="원가산입등록 내역 조회 시 합계가 누락되는 현상 보완")]), "승인"])
    lines = raw_lines(env / "reports" / "20261123.hwpx")
    at = lines.index("  - 내용 : 원가산입등록 내역 조회 시 합계가")        # 단어('누락되는')를 끊지 않고 다음 줄로
    assert lines[at + 1] == " " * 15 + "누락되는 현상 보완"
    assert all(text_width(line) <= LIMIT for line in lines[at:at + 2])


# ---- 승인 미리보기: 신규 건 표시 ----
def test_approval_payload_marks_new_items_and_added_lines(env):
    from langgraph.types import Command
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    path = []
    asyncio.run(graph.advance(app, config, {"report_date": None}, path))
    paused = asyncio.run(graph.advance(app, config, Command(resume=confirm()), path))
    items = paused["항목"]
    carried = [i for i in items if i["title"] == TITLE and i["column"] == "this"][0]
    new = [i for i in items if i["title"] == "신규 테스트 개발"][0]
    assert new["new"] is True and new["column"] == "next" and new["requester"] == "IT지원팀"
    assert carried["new"] is False and carried["added"]                 # 이월 건: 신규는 아니지만 추가한 줄은 따로 표시
    assert sum(i["new"] for i in items) == 1


def test_two_new_items_in_both_columns_are_both_flagged(env):
    from langgraph.types import Command
    app = graph.build_graph(Compose(), tools.call_local)
    config, _ = graph.new_run(None)
    path = []
    asyncio.run(graph.advance(app, config, {"report_date": None}, path))
    answer = confirm(new=[dict(NEW, columns=["this"], program_name="금주 신규", **THIS_ONLY), dict(NEW, columns=["next"], program_name="차주 신규")])
    paused = asyncio.run(graph.advance(app, config, Command(resume=answer), path))
    flagged = sorted((i["column"], i["title"]) for i in paused["항목"] if i["new"])
    assert flagged == [("next", "차주 신규 개선 개발"), ("this", "금주 신규 개선 개발")]


# ---- 금주 신규 건이 차주로 이어지면 차주에도 복사하고 (계속)을 판정한다 ----
LONG = [{"name": "개발/테스트", "start": "2026-11-23", "end": "2026-12-11", "open_ended": False}]


def _new(columns, due, phases=LONG):
    return dict(NEW, columns=columns, due=due, phases=phases)


def _written(env):
    return read_report(env / "reports" / "20261123.hwpx")


def test_this_week_new_item_running_past_this_week_is_copied_to_next_week_with_continued(env):
    drive(Compose(), [confirm(select={"this": [TITLE], "next": []}, new=[_new(["this"], "2026-12-11")]), "승인"])
    r = _written(env)
    this = [i for i in r.this_items if i.title == "신규 테스트 개발"]
    nxt = [i for i in r.next_items if i.title == "신규 테스트 개발"]
    assert len(this) == 1 and this[0].continued is True           # 금주 이후(12.11)까지 이어지므로 금주에도 (계속)
    assert len(nxt) == 1 and nxt[0].continued is True             # 차주로 이어지는 건: 그대로 복사 + (계속)
    assert nxt[0].reason == this[0].reason and nxt[0].due == this[0].due and nxt[0].requester == this[0].requester


def test_this_week_new_item_ending_this_week_is_not_copied(env):
    short = [{"name": "분석/설계", "start": "2026-11-23", "end": "2026-11-27", "open_ended": False}]
    drive(Compose(), [confirm(select={"this": [TITLE], "next": []}, new=[_new(["this"], "2026-11-27", short)]), "승인"])
    r = _written(env)
    assert [i.title for i in r.next_items if i.title == "신규 테스트 개발"] == []


def test_new_item_in_both_columns_is_continued_in_next_week_when_it_runs_beyond_it(env):
    drive(Compose(), [confirm(select={"this": [TITLE], "next": []}, new=[_new(["this", "next"], "2026-12-11")]), "승인"])
    r = _written(env)
    assert [i.continued for i in r.next_items if i.title == "신규 테스트 개발"] == [True]
    assert len([i for i in r.next_items if i.title == "신규 테스트 개발"]) == 1     # 이중 복사 없음


def test_next_only_new_item_is_not_copied_backwards(env):
    drive(Compose(), [confirm(select={"this": [TITLE], "next": []}, new=[_new(["next"], "2026-12-11")]), "승인"])
    r = _written(env)
    assert [i.title for i in r.this_items if i.title == "신규 테스트 개발"] == []


# ---- 신규 건의 (계속): 금주 신규도 실제 완료일이 그 주 이후면 붙고, 그 주에 끝나면 붙지 않는다 ----
def test_this_week_new_item_running_beyond_this_week_is_marked_continued(env):
    drive(Compose(), [confirm(select={"this": [TITLE], "next": []}, new=[_new(["this"], "2026-12-11")]), "승인"])
    r = _written(env)
    assert [i.continued for i in r.this_items if i.title == "신규 테스트 개발"] == [True]
    assert [i.continued for i in r.next_items if i.title == "신규 테스트 개발"] == [True]


def test_new_item_ending_within_its_week_is_not_marked_continued(env):
    drive(Compose(), [confirm(select={"this": [TITLE], "next": []}, new=[_new(["this"], "2026-12-04")]), "승인"])
    r = _written(env)
    assert [i.continued for i in r.this_items if i.title == "신규 테스트 개발"] == [True]    # 금주 이후(차주)까지 이어짐
    assert [i.continued for i in r.next_items if i.title == "신규 테스트 개발"] == [False]   # 차주에 끝남


def test_ai_written_content_is_a_single_item_in_form_a(env):
    class Multi(Compose):
        async def __call__(self, inp, feedback=""):
            c = await super().__call__(inp, feedback)
            return Composed(c.title, c.reason, ["첫째 줄 내용", "둘째 줄 내용"])      # 모델이 여러 줄로 써도

    drive(Multi(), [confirm(new=[dict(NEW, content="")]), "승인"])              # 내용을 비우면 AI가 작성 -> 기본은 형식 A
    item = [i for i in _written(env).next_items if i.title == "신규 테스트 개발"][0]
    assert item.content_label == "내용" and " ".join(l.strip() for l in item.content) == "첫째 줄 내용 둘째 줄 내용"
