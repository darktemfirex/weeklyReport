import asyncio
import json
from datetime import date

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from agent import Agent, Composed, ItemInput, compose_item
from schedule import Phase

ANSWER = {"title": "샘플 개선 개발", "reason": ["사유 한 줄"], "content": ["foo", "· bar"]}


class FakeLLM:
    """차례대로 준비된 응답을 돌려주고, 받은 메시지를 기록하는 가짜 모델"""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    async def ainvoke(self, messages):
        self.seen.append(list(messages))
        return self.replies.pop(0) if self.replies else self.replies_last

    replies_last = AIMessage(content="", tool_calls=[{"name": "search_items", "args": {"query": "q"}, "id": "x"}])


def make_tool(counter):
    def search_items(query: str) -> str:
        counter.append(query)
        return "[]"
    return StructuredTool.from_function(func=search_items, name="search_items", description="과거 건 검색")


def sample_input():
    return ItemInput("사유 원문", "내용 원문", [Phase("개발", date(2026, 11, 30), date(2026, 12, 4))], "IT지원팀", date(2026, 12, 7))


def run(coro):
    return asyncio.run(coro)


def test_agent_loop_runs_tool_once_and_composes():
    calls = []
    llm = FakeLLM([AIMessage(content="", tool_calls=[{"name": "search_items", "args": {"query": "q"}, "id": "1"}]),
                   AIMessage(content=json.dumps(ANSWER, ensure_ascii=False))])
    agent = Agent([make_tool(calls)], "시스템", llm=llm)
    result = run(compose_item(sample_input(), agent))
    assert calls == ["q"]
    assert result == Composed("샘플 개선 개발", ["사유 한 줄"], ["foo", "bar"])   # 글머리 기호는 걷어낸다


def test_agent_stops_at_max_steps():
    agent = Agent([make_tool([])], "시스템", max_steps=3, llm=FakeLLM([]))
    out = run(agent.ainvoke({"messages": [("user", "hi")]}))
    assert "반복 상한 도달" in out["messages"][-1].content


def test_compose_passes_feedback():
    llm = FakeLLM([AIMessage(content=json.dumps(ANSWER, ensure_ascii=False))])
    run(compose_item(sample_input(), Agent([make_tool([])], "시스템", llm=llm), feedback="규칙1 입력에 없는 숫자: 2099"))
    user_messages = " ".join(m.content for m in llm.seen[0] if m.type == "human")
    assert "규칙1 입력에 없는 숫자: 2099" in user_messages and "내용 원문" in user_messages


def test_compose_accepts_json_fence():
    llm = FakeLLM([AIMessage(content="```json\n" + json.dumps(ANSWER, ensure_ascii=False) + "\n```")])
    assert run(compose_item(sample_input(), Agent([make_tool([])], "시스템", llm=llm))).title == "샘플 개선 개발"


def test_compose_message_tells_model_the_given_title_and_to_write_empty_content():
    llm = FakeLLM([AIMessage(content=json.dumps(ANSWER, ensure_ascii=False))])
    inp = ItemInput("사유 원문", "", [], "IT지원팀", None, title="직접 입력한 타이틀")
    run(compose_item(inp, Agent([make_tool([])], "시스템", llm=llm)))
    human = " ".join(m.content for m in llm.seen[0] if m.type == "human")
    assert "직접 입력한 타이틀" in human
    assert "개발내용: (비어 있음" in human and "사유를 바탕으로" in human and "만들지 말 것" not in human


def test_compose_message_asks_for_title_when_blank():
    llm = FakeLLM([AIMessage(content=json.dumps(ANSWER, ensure_ascii=False))])
    inp = ItemInput("사유 원문", "내용 원문", [], "IT지원팀", None)
    run(compose_item(inp, Agent([make_tool([])], "시스템", llm=llm)))
    human = " ".join(m.content for m in llm.seen[0] if m.type == "human")
    assert "타이틀: (비어 있음" in human and "사유를 바탕으로" in human


def test_compose_message_uses_main_schedule_wording():
    llm = FakeLLM([AIMessage(content=json.dumps(ANSWER, ensure_ascii=False))])
    run(compose_item(sample_input(), Agent([make_tool([])], "시스템", llm=llm)))
    human = " ".join(m.content for m in llm.seen[0] if m.type == "human")
    assert "주요일정:" in human and "개발일정:" not in human


def test_compose_message_asks_for_reason_when_blank_and_hints_title_format():
    llm = FakeLLM([AIMessage(content=json.dumps(ANSWER, ensure_ascii=False))])
    inp = ItemInput("", "내용 원문", [], "IT지원팀", None, program_code="70-1234", work_type="신규 개발")
    run(compose_item(inp, Agent([make_tool([])], "시스템", llm=llm)))
    human = " ".join(m.content for m in llm.seen[0] if m.type == "human")
    assert "사유: (비어 있음" in human and "타이틀/내용을 바탕으로" in human
    assert "프로그램코드: 70-1234" in human and "구분: 신규 개발" in human
    assert "프로그램명(프로그램코드) 개선 개발" in human or "개선 개발/신규 개발" in human


def test_system_prompt_states_title_format_and_no_invented_code():
    from agent import COMPOSE_SYSTEM
    assert "프로그램명(프로그램코드) 개선 개발" in COMPOSE_SYSTEM and "신규 개발" in COMPOSE_SYSTEM
    assert "코드를 지어내지" in COMPOSE_SYSTEM


def test_compose_message_includes_previous_report_context_and_dev_content_wording():
    llm = FakeLLM([AIMessage(content=json.dumps(ANSWER, ensure_ascii=False))])
    inp = ItemInput("사유 원문", "내용 원문", [], "IT지원팀", None, context="- 축수산 일반매입등록(70-2048) 개선 개발: 사유 확대 적용 요청; 개발내용 첨부 기능 추가")
    run(compose_item(inp, Agent([make_tool([])], "시스템", llm=llm)))
    human = " ".join(m.content for m in llm.seen[0] if m.type == "human")
    assert "전주 주간보고" in human and "축수산 일반매입등록(70-2048) 개선 개발" in human
    assert "개발내용: 내용 원문" in human and "\n내용:" not in human


def test_system_prompt_requires_report_style_not_polite_speech():
    from agent import COMPOSE_SYSTEM
    assert "개조식" in COMPOSE_SYSTEM and "존댓말" in COMPOSE_SYSTEM and "전주 주간보고" in COMPOSE_SYSTEM


# ---- AI 점검(review) ----
from agent import REVIEW_SYSTEM, Composed, review_item  # noqa: E402


def test_review_item_returns_corrected_text_with_bullets():
    fixed = {"title": "점검된 타이틀 개선 개발", "reason": ["점검된 사유"], "content": ["항목 하나", "· 항목 둘"]}
    llm = FakeLLM([AIMessage(content=json.dumps(fixed, ensure_ascii=False))])
    out = run(review_item(sample_input(), Composed("초안 타이틀", ["초안 사유"], ["    · 초안 내용"]), llm))
    assert out == Composed("점검된 타이틀 개선 개발", ["점검된 사유"], ["항목 하나", "항목 둘"])
    human = " ".join(m.content for m in llm.seen[0] if m.type == "human")
    assert "초안 타이틀" in human and "초안 사유" in human and "초안 내용" in human and "사유 원문" in human   # 초안과 입력 원문을 함께 본다


def test_review_item_bad_json_raises_value_error():
    llm = FakeLLM([AIMessage(content="점검 결과 없음")])
    import pytest
    with pytest.raises(ValueError):
        run(review_item(sample_input(), Composed("t", ["r"], []), llm))


def test_review_system_prompt_rules():
    for word in ("개조식", "존댓말", "프로그램명(프로그램코드) 개선 개발", "신규 개발", "사실", "숫자"):
        assert word in REVIEW_SYSTEM
