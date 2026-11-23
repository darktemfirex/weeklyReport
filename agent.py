"""작성 에이전트 = LLM + 도구 + 반복 루프(ReAct). 유사한 과거 건을 검색해 타이틀·사유·내용을 과거 문체로 정리한다.
일정·날짜·(계속)은 코드가 처리하므로 모델은 쓰지 않는다."""
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

import tools as tool_funcs
from schedule import Phase, fmt_date, fmt_phase

load_dotenv(Path(__file__).resolve().parent / ".env")

COMPOSE_SYSTEM = """너는 주간보고 작성 에이전트다. 입력(사유, 개발내용, 주요일정, 요청부서, 완료일)을 받아 보고서 문구를 쓴다.
문체: 전주 주간보고처럼 개조식 보고체로 쓴다(체언·명사형 종결, 예: '거래명세서 스캔본 첨부 기능 추가 개발', '수산부 동일 기능 확대 적용 요청').
존댓말(~합니다, ~입니다, ~해요)과 서술형 문장은 쓰지 않는다. 메시지의 '전주 주간보고'가 있으면 그 맥락·문체와 비슷하게 쓴다.
먼저 search_items로 비슷한 과거 건을 찾아 타이틀과 문체를 따른다(예: '축수산 일반매입등록(70-2048) 개선 개발').
타이틀이 주어지면 title에 그대로 쓰고, 비어 있으면 사유를 보고 만든다. 타이틀 형식은 '프로그램명(프로그램코드) 개선 개발' 또는 '프로그램명(프로그램코드) 신규 개발'이다.
프로그램코드가 주어지지 않으면 괄호 없이 '프로그램명 개선 개발'로 쓰고, 코드를 지어내지 않는다.
사유가 비어 있으면 타이틀·내용을 바탕으로 reason을 1~2줄로 짧게 쓴다.
내용이 비어 있으면 사유만 근거로 content를 한 항목(배열 원소 1개)으로 짧게 쓴다(사유에 없는 사실·수치는 쓰지 않는다). 기본은 단일 항목이다.
입력에 없는 숫자, 화면번호, 사실을 쓰지 않는다. 개발내용은 입력 문장을 다듬기만 하고 새 항목·결과(예: 완료, 개선 효과)·요청부서·일정을 덧붙이지 않는다.
일정·날짜·(계속)은 쓰지 않는다.
결과는 JSON 하나만 출력한다: {"title": "...", "reason": ["사유 줄", ...], "content": ["개발내용 줄", ...]}"""

REVIEW_SYSTEM = """너는 주간보고 점검 에이전트다. 새로 등록된 건의 타이틀, 사유, 개발내용을 한 번 더 점검해 고친다.
점검 기준:
1. 문체: 전주 주간보고처럼 개조식 보고체(체언·명사형 종결, 예: '거래명세서 스캔본 첨부 기능 추가 개발', '… 적용 요청')로 쓴다. 존댓말(~합니다, ~입니다, ~해요)과 서술형 문장은 쓰지 않는다.
2. 타이틀 형식: '프로그램명(프로그램코드) 개선 개발' 또는 '프로그램명(프로그램코드) 신규 개발'. 코드가 없으면 괄호 없이 '프로그램명 개선 개발'로 쓰고, 코드를 지어내지 않는다.
3. 사실 유지: 입력 원문에 있는 사실과 숫자는 바꾸지 않고, 없는 사실·숫자·결과(완료, 개선 효과)는 새로 넣지 않는다.
4. 내용의 항목 수(content 배열 원소 수)는 바꾸지 않는다. 항목이 하나면 하나로 돌려준다. 사유와 개발내용이 서로 맞는지 보고, 중복·어색한 표현·오탈자를 고친다. 고칠 곳이 없으면 그대로 돌려준다.
5. 전주 주간보고(맥락)가 주어지면 비슷한 맥락·문체로 맞춘다. 그 안의 숫자·코드는 옮겨 쓰지 않는다.
결과는 JSON 하나만 출력한다: {"title": "...", "reason": ["사유 줄", ...], "content": ["개발내용 줄", ...]}"""


@dataclass
class ItemInput:
    reason: str
    content: str
    phases: list[Phase]
    requester: str
    due: date | None
    title: str = ""     # 사용자가 쓴(또는 프로그램명·코드·구분으로 조립한) 타이틀. 비어 있으면 모델이 만든다
    program_code: str = ""   # 프로그램명이 비어 모델이 타이틀을 만들 때 쓰는 힌트
    work_type: str = ""      # "개선 개발" 또는 "신규 개발"
    context: str = ""        # 전주 주간보고 건 요약(맥락·문체 참고용)


@dataclass
class Composed:
    title: str
    reason: list[str]
    content: list[str]


def _text(message) -> str:
    c = message.content
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c if isinstance(b, dict))


class Agent:
    """에이전트 = LLM(도구 붙임) + 반복 루프. on_event로 계획·도구 요청·결과를 밖에 알린다.
    llm에는 이미 도구가 붙은 모델(또는 테스트용 가짜)을 줄 수 있다"""

    def __init__(self, tools, system: str, max_steps: int = 8, llm=None):
        if llm is None:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=os.getenv("CHAT_MODEL"), temperature=0).bind_tools(tools, parallel_tool_calls=False)
        self.llm, self.tools = llm, {t.name: t for t in tools}
        self.system, self.max_steps, self.callbacks = system, max_steps, None  # callbacks: 실행 로그(RunLog) 등

    async def ainvoke(self, inp: dict, on_event=None) -> dict:
        emit = on_event or (lambda *a, **k: None)
        messages = [SystemMessage(self.system)] + [HumanMessage(c) for _, c in inp["messages"]]
        for _ in range(self.max_steps):                     # 상한이 있어야 무한 반복을 막는다
            kw = {"config": {"callbacks": self.callbacks}} if self.callbacks else {}
            ai = await self.llm.ainvoke(messages, **kw)      # 1) LLM: 다음 행동(도구 요청 또는 답)
            messages.append(ai)
            if _text(ai) and ai.tool_calls:
                emit("think", text=_text(ai))
            if not ai.tool_calls:
                break
            for call in ai.tool_calls:                       # 2) 프로그램: 요청받은 도구를 실제로 실행
                emit("tool", name=call["name"], input=call["args"])
                out = await self.tools[call["name"]].ainvoke(call["args"])
                emit("tool_result", name=call["name"], output=str(out)[:600])
                messages.append(ToolMessage(str(out), tool_call_id=call["id"], name=call["name"]))  # 3) 결과를 다시 LLM에
        else:
            messages.append(AIMessage(content="반복 상한 도달 - 담당자 확인 필요"))
        return {"messages": messages}


def search_tools() -> list:
    """작성 에이전트에 붙이는 도구: 유사한 과거 건 검색"""
    return [StructuredTool.from_function(
        func=tool_funcs.tool_search_items, name="search_items",
        description="과거 주간보고에서 비슷한 건(타이틀·사유·내용)을 찾을 때 쓴다. query는 건의 주제를 한 줄로.")]


def plain_lines(lines) -> list[str]:
    """모델이 쓴 줄에서 글머리 기호(·, -)를 걷어낸다. 보고서에서는 '- 개발내용 : ' 뒤에 이어 쓴다"""
    cleaned = [re.sub(r"^[\s·\-]+", "", str(s)).strip() for s in lines]
    return [s for s in cleaned if s]


def _message(inp: ItemInput, feedback: str) -> str:
    hints = ([f"프로그램코드: {inp.program_code}"] if inp.program_code.strip() else []) + \
            ([f"구분: {inp.work_type}"] if inp.work_type.strip() else [])
    lines = hints + [
             f"타이틀(그대로 사용): {inp.title}" if inp.title.strip() else
             "타이틀: (비어 있음 - 사유를 바탕으로 '프로그램명(프로그램코드) 개선 개발/신규 개발' 형식으로 작성할 것)",
             f"사유: {inp.reason}" if inp.reason.strip() else "사유: (비어 있음 - 타이틀/내용을 바탕으로 작성할 것)",
             f"개발내용: {inp.content}" if inp.content.strip() else "개발내용: (비어 있음 - 사유를 바탕으로 내용 줄을 작성할 것)",
             "주요일정: " + (", ".join(fmt_phase(p) for p in inp.phases) or "없음"),
             f"요청부서: {inp.requester}", f"완료(예정)일: {fmt_date(inp.due) if inp.due else '없음'}"]
    if inp.context.strip():
        lines.append("\n전주 주간보고(맥락·문체 참고. 여기의 숫자·코드는 옮겨 쓰지 말 것):\n" + inp.context)
    if feedback:
        lines.append(f"\n이전 결과는 검증에서 거절됐다: {feedback}\n이 문제를 고쳐서 다시 쓴다.")
    return "\n".join(lines)


async def compose_item(inp: ItemInput, agent: Agent, feedback: str = "") -> Composed:
    out = await agent.ainvoke({"messages": [("user", _message(inp, feedback))]})
    raw = _text(out["messages"][-1]).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return Composed(str(data["title"]).strip(), [str(s).strip() for s in data["reason"]],
                        plain_lines(data["content"]))
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"작성 결과를 읽을 수 없음: {raw[:100]}") from e


def _review_message(inp: ItemInput, composed: Composed) -> str:
    original = [f"타이틀: {inp.title or '(없음)'}"] + ([f"프로그램코드: {inp.program_code}"] if inp.program_code.strip() else []) + \
               ([f"구분: {inp.work_type}"] if inp.work_type.strip() else []) + \
               [f"사유: {inp.reason or '(없음)'}", f"개발내용: {inp.content or '(없음)'}"]
    draft = [f"타이틀: {composed.title}", "사유: " + " / ".join(composed.reason),
             "개발내용: " + " / ".join(s.strip().lstrip("·").strip() for s in composed.content)]
    parts = ["입력 원문:", *original, "", "점검할 초안:", *draft]
    if inp.context.strip():
        parts += ["", "전주 주간보고(맥락·문체 참고):", inp.context]
    return "\n".join(parts)


async def review_item(inp: ItemInput, composed: Composed, llm=None, callbacks=None) -> Composed:
    """AI 점검: 작성된(또는 사용자가 쓴) 타이틀·사유·개발내용을 한 번 더 보고 고친다. 읽을 수 없으면 ValueError"""
    if llm is None:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=os.getenv("CHAT_MODEL"), temperature=0)
    kw = {"config": {"callbacks": callbacks}} if callbacks else {}
    reply = await llm.ainvoke([SystemMessage(REVIEW_SYSTEM), HumanMessage(_review_message(inp, composed))], **kw)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", _text(reply).strip())
    try:
        data = json.loads(raw)
        return Composed(str(data["title"]).strip(), [str(s).strip() for s in data["reason"]], plain_lines(data["content"]))
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"점검 결과를 읽을 수 없음: {raw[:100]}") from e


def make_review(callbacks=None):
    """그래프가 쓰는 review 함수(ItemInput, Composed → Composed)를 만든다"""
    async def review(inp: ItemInput, composed: Composed) -> Composed:
        return await review_item(inp, composed, callbacks=callbacks)
    return review


def make_compose(agent: Agent):
    """그래프가 쓰는 compose 함수(ItemInput, feedback → Composed)를 만든다"""
    async def compose(inp: ItemInput, feedback: str = "") -> Composed:
        return await compose_item(inp, agent, feedback)
    return compose
