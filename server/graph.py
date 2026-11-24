"""LangGraph: 직전 보고 읽기 → 주차 계산 → 이월 후보 → 사용자 확인 → 초안 → (계속) 표시 → 검증 → 사용자 승인 → 저장
- 사용자 확인·승인은 interrupt()로 멈추고 Command(resume=…)로 이어 간다 (체크포인트 InMemorySaver)
- 로그: 도구·승인·차단·LLM 호출·토큰을 logs/ 파일에 남긴다(개인정보는 가림)
- 권한: 저장은 승인 노드를 통과한 뒤에만 실행. 가드레일: guardrails.py
상태에는 JSON으로 바꿀 수 있는 값(문자열·dict)만 둔다."""
import dataclasses
import json
import time
from datetime import date, datetime
from typing import TypedDict

from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import tools
from agent import Composed, ItemInput
from guardrails import check, check_facts, check_tone, mask
from layout import content_items, format_added_line, format_content, wrap_reason
from hwpx_io import Csr, Report
from hwpx_render import preview_items, preview_text, render_report, verify_hwpx
from schedule import Item, Period, Phase, Weeks, end_date, fmt_date, fmt_phase, is_continuing, is_finished_before, next_report_date, overlaps

MAX_RETRY = 2    # 검증 실패 뒤 다시 쓰는 횟수 상한
MAX_REJECT = 2   # 반려 상한
MIN_ITEMS = 5    # 금주+차주 합계 최소 건수. 모자라면 신규 건을 더 받는다
LOGS_DIR = tools.ROOT / "logs"


class RunLog(AsyncCallbackHandler):
    """실행 로그: logs/run-시각-보고일.jsonl 한 줄에 사건 하나. LLM 호출은 콜백으로 자동 기록"""

    def __init__(self, tag: str, guards: bool = True):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOGS_DIR / f"run-{datetime.now():%Y%m%d-%H%M%S}-{tag}.jsonl"
        self.guards, self.llm_calls, self.tokens = guards, 0, 0

    def event(self, kind: str, **data):
        line = json.dumps({"시각": datetime.now().isoformat(timespec="seconds"), "종류": kind, **data},
                          ensure_ascii=False, default=str)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write((mask(line) if self.guards else line) + "\n")

    async def on_llm_end(self, response, **kw):
        self.llm_calls += 1
        message = getattr(response.generations[0][0], "message", None)
        usage = getattr(message, "usage_metadata", None) or {}
        self.tokens += usage.get("total_tokens", 0)
        self.event("LLM", 호출번호=self.llm_calls, 토큰=usage.get("total_tokens", 0))


async def guarded_call(call_tool, name: str, args: dict, state: dict, log: RunLog):
    """권한을 통과해야만 도구를 실행한다. 막히면 차단 기록을 남기고 None"""
    if tools.PERMISSION[name] == "승인 후" and not state.get("approved"):
        log.event("차단", 규칙="권한: 승인 전 저장", 도구=name)
        return None
    out = await call_tool(name, args)
    log.event("도구", 도구=name, 권한=tools.PERMISSION[name], 입력=json.dumps(args, ensure_ascii=False)[:300],
              결과=str(out)[:300])
    return out


class State(TypedDict, total=False):
    report_date: str | None
    prev_name: str | None   # 직전 보고서 파일 이름(첨부한 파일 또는 data의 최신)
    prev: str            # 직전 보고서 JSON
    weeks: dict
    candidates: list
    excluded: list       # 완료(예정)일이 지나 후보에서 자동 제외한 건 [{column, title, end}]
    user: dict           # 사용자 확인 답
    items_this: list
    items_next: list
    checks: list         # 새로 쓴 건의 (출력, 입력) 글 — 규칙1 검사용
    overrides: dict      # 건 제목 → (계속) 수동 지정(True/False)
    problems: list
    feedback: str
    retry_count: int
    new_refs: list       # 새로 쓴 건의 위치·입력(AI 점검이 쓴다)
    reject_count: int
    reject_reason: str   # 반려 사유. 사용자 확인 화면에 보여 주고 그 입력을 받으면 비운다
    report_json: str
    approved: bool
    overwrite: bool
    why: str
    result: str


def count_items(answer: dict) -> int:
    """사용자 확인 답에서 금주+차주 칸에 들어갈 건수(이월 선택 + 신규 건 × 들어갈 칸 수)"""
    picked = sum(len(titles) for titles in answer.get("select", {}).values())
    return picked + sum(len(n.get("columns", ["this"])) for n in answer.get("new", []))


def missing_add_lines(answer: dict, candidates: list) -> list[str]:
    """금주 칸의 이월 건(원문 복사 건 제외) 중 개발내용 1줄이 비어 있는 건의 제목"""
    raw = {c["title"] for c in candidates if c.get("raw")}
    lines = answer.get("add_line", {})
    return [t for t in answer.get("select", {}).get("this", []) if t not in raw and not (lines.get(t) or "").strip()]


def _weeks(state: State) -> Weeks:
    w = state["weeks"]
    period = lambda p: Period(date.fromisoformat(p["start"]), date.fromisoformat(p["end"]))
    return Weeks(date.fromisoformat(w["report_date"]), period(w["this_week"]), period(w["next_week"]), period(w["csr_week"]))


def _pool(prev: Report) -> dict[str, Item]:
    pool = {}
    for item in prev.this_items + prev.next_items:   # 같은 건이면 차주 쪽(최신 계획)이 남는다
        pool[item.title] = item
    return pool


def _phase(p: dict) -> Phase:
    d = lambda s: date.fromisoformat(s) if s else None
    name, start = mask(p["name"]), d(p["start"])
    if name.startswith("본적용"):          # 본적용 및 모니터링은 종료일 없이 시작일 하루로 쓴다
        return Phase(name, start, start, False)
    return Phase(name, start, d(p["end"]), bool(p.get("open_ended")))


def _title_of(n: dict) -> str:
    """신규 건 타이틀: 직접 쓴 title이 있으면 그대로, 없으면 프로그램명(프로그램코드) 구분. 프로그램명이 없으면 빈 값(AI가 작성)"""
    if n.get("title", "").strip():
        return n["title"].strip()
    name = n.get("program_name", "").strip()
    if not name:
        return ""
    code, kind = n.get("program_code", "").strip(), n.get("work_type", "").strip() or "개선 개발"
    return f"{name}({code}) {kind}" if code else f"{name} {kind}"


def _context(prev: Report, limit: int = 6) -> str:
    """전주 주간보고 건 요약(타이틀·사유·개발내용). AI가 맥락과 문체를 맞추는 데 쓴다. 원문 복사 건은 제외"""
    seen, lines = set(), []
    for item in prev.this_items + prev.next_items:
        if item.raw is not None or item.title in seen:
            continue
        seen.add(item.title)
        reason = " ".join(item.reason)[:80]
        content = " / ".join(c.strip().lstrip("·").strip() for c in item.content[:2])[:100]
        lines.append(f"- {item.title}: 사유 {reason}; 개발내용 {content}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def _input(n: dict, context: str = "") -> ItemInput:
    """사용자 입력 → ItemInput. 개인정보는 모델에 보내기 전에 가린다"""
    due = date.fromisoformat(n["due"]) if n.get("due") else None
    return ItemInput(mask(n.get("reason", "")), mask(n.get("content", "")), [_phase(p) for p in n["phases"]],
                     mask(n["requester"]), due, mask(_title_of(n)), n.get("program_code", "").strip(),
                     n.get("work_type", "").strip(), context)


def _allowed(inp: ItemInput) -> str:
    return " ".join([inp.title, inp.program_code, inp.reason, inp.content, inp.requester, *[fmt_phase(p) for p in inp.phases],
                     fmt_date(inp.due) if inp.due else ""])


def build_graph(compose, call_tool, approver: str = "작성자", review=None):
    """compose: async (ItemInput, feedback) -> Composed / call_tool: async (이름, 인자) -> 문자열
    review: async (ItemInput, Composed) -> Composed. AI 점검 단계에서 새로 쓴 건을 한 번 더 점검한다(없으면 그대로 통과)"""

    async def call(name, args, state, config):
        return await guarded_call(call_tool, name, args, state, config["configurable"]["log"])

    async def read_prev(state: State, config):
        name = state.get("prev_name") or tools.latest_report_path(tools.DATA_DIR).name
        prev_json = await call("read_report", {"file_name": name}, state, config)
        report_date = state.get("report_date") or next_report_date(tools.report_from_json(prev_json).report_date).isoformat()
        return {"prev": prev_json, "report_date": report_date, "prev_name": name}

    async def calc(state: State, config):
        return {"weeks": json.loads(await call("calc_weeks", {"report_date": state["report_date"]}, state, config))}

    def carry(state: State, config):
        prev, weeks = tools.report_from_json(state["prev"]), _weeks(state)
        was = {"this": {i.title for i in prev.this_items}, "next": {i.title for i in prev.next_items}}
        found, excluded = [], []
        for column, week in (("this", weeks.this_week), ("next", weeks.next_week)):
            for item in _pool(prev).values():
                if is_finished_before(item, week):   # 완료(예정)일이 그 주 시작 전이면 이미 끝난 건: 자동 제외
                    excluded.append({"column": column, "title": item.title, "end": end_date(item).isoformat()})
                    continue
                # 일정이 없는 원문 복사 건은 직전 보고서에서 있던 칸에만 제안한다(진척표 중복 방지)
                if overlaps(item, week) or (item.raw is not None and item.title in was[column]):
                    found.append({"column": column, "title": item.title, "page": item.page, "raw": item.raw is not None})
        config["configurable"]["log"].event("자동제외", 건수=len(excluded), 건=[f"{e['column']}:{e['title']}({e['end']})" for e in excluded])
        return {"candidates": found, "excluded": excluded}

    def confirm(state: State, config):
        payload = {"후보": state["candidates"], "자동제외": state.get("excluded", []), "주차": state["weeks"], "반려사유": state.get("reject_reason", ""),
                   "입력": "select(칸별 이월 건 제목), new(신규 건들), csr(숫자 5개), continued(건별 (계속) 수정), add_line(금주 이월 건 개발내용 1줄 추가)"}
        answer = interrupt(payload)
        while True:   # 건수가 모자라거나 금주 이월 건의 1줄이 비면 다시 받는다
            need, missing = MIN_ITEMS - count_items(answer), missing_add_lines(answer, state["candidates"])
            errors = ([f"건수가 {need}건 부족합니다(금주+차주 합계 {MIN_ITEMS}건 이상). 신규 건을 등록하세요."] if need > 0 else []) + \
                     ([f"금주 이월 건의 개발내용 1줄을 입력하세요: {', '.join(missing)}"] if missing else [])
            if not errors:
                break
            answer = interrupt({**payload, "오류": " ".join(errors)})
        config["configurable"]["log"].event("확인", 이월=answer.get("select"), 신규건수=len(answer.get("new", [])))
        # 반려 뒤 다시 들어와도 반려 횟수·반려 사유(feedback)는 이어 간다. 새 입력으로 다시 쓸 때 사유를 반영한다
        return {"user": answer, "retry_count": 0, "reject_reason": ""}

    async def draft(state: State, config):
        prev, user = tools.report_from_json(state["prev"]), state["user"]
        pool, log = _pool(prev), config["configurable"]["log"]
        columns = {"this": [], "next": []}
        add_line = user.get("add_line", {})
        for column in columns:
            for title in user.get("select", {}).get(column, []):
                if title not in pool:
                    continue
                item = pool[title]
                line = (add_line.get(title) or "").strip().splitlines()[:1]
                if column == "this" and line and item.raw is None:   # 개발내용 1줄 추가는 금주 칸의 이월 건만(굵게 표시)
                    added = format_added_line(item, mask(line[0].strip()))   # 건의 기존 형식에 맞추고 길면 단어 단위로 나눈다
                    item = dataclasses.replace(item, content=[*item.content, *added], emphasis=added)
                columns[column].append(item)
        checks, problems, overrides, refs = [], [], dict(user.get("continued", {})), []
        context = _context(prev)
        for n in user.get("new", []):
            inp = _input(n, context)
            try:
                c = await compose(inp, mask(state.get("feedback", "")))
            except ValueError as e:
                problems.append(str(e))
                continue
            title = inp.title.strip() or c.title                       # 직접 쓴 타이틀이 우선
            content = c.content                                        # 내용이 비었으면 모델이 사유를 보고 쓴 내용
            item = Item(title, False, c.reason, "개발내용", content, "주요일정", inp.phases, inp.requester, inp.due)
            targets = list(n.get("columns", ["this"]))
            end = end_date(item)
            if "this" in targets and "next" not in targets and end and end > _weeks(state).this_week.end:
                targets.append("next")      # 금주 신규 건이 금주 이후까지 이어지면 차주에도 그대로 복사한다
            for column in targets:
                # 신규 건은 그 칸에서 이어지는 건들의 맨 아래에 붙는다: 마지막 이월 건과 같은 쪽(없으면 1쪽)
                page = max((i.page for i in columns[column]), default=1)
                refs.append({"column": column, "index": len(columns[column]), "check": len(checks),
                             "input": {"title": inp.title, "reason": inp.reason, "content": inp.content,
                                       "program_code": inp.program_code, "work_type": inp.work_type, "context": context}})
                columns[column].append(dataclasses.replace(item, page=page))
            if n.get("continued") is not None:
                overrides[title] = bool(n["continued"])      # 최종 타이틀 기준(점검 전)
            checks.append({"output": " ".join([title, *c.reason, *content]), "allowed": _allowed(inp),
                           "lines": [title, *c.reason, *content]})
        log.event("초안", 신규건수=len(user.get("new", [])), 피드백=state.get("feedback", ""))
        return {"items_this": [tools.item_to_dict(i) for i in columns["this"]],
                "items_next": [tools.item_to_dict(i) for i in columns["next"]], "checks": checks, "problems": problems,
                "overrides": overrides, "new_refs": refs}

    async def ai_review(state: State, config):
        """AI 점검: 새로 쓴 건(사용자가 타이틀을 쓴 경우 포함)의 타이틀·사유·개발내용을 한 번 더 점검해 고친다"""
        refs = state.get("new_refs", [])
        if review is None or not refs:
            return {}
        log = config["configurable"]["log"]
        items = {"this": [dict(d) for d in state["items_this"]], "next": [dict(d) for d in state["items_next"]]}
        checks, done = [dict(c) for c in state.get("checks", [])], {}
        overrides = dict(state.get("overrides", {}))
        for ref in refs:
            number = ref["check"]
            if number not in done:           # 같은 건이 두 칸에 있어도 한 번만 점검한다
                d = items[ref["column"]][ref["index"]]
                inp = ItemInput(ref["input"]["reason"], ref["input"]["content"], [], "", None, ref["input"]["title"],
                                ref["input"]["program_code"], ref["input"]["work_type"], ref["input"]["context"])
                try:
                    done[number] = await review(inp, Composed(d["title"], d["reason"], d["content"]))
                except ValueError as e:     # 점검 결과를 읽지 못하면 초안을 그대로 쓴다
                    log.event("점검 실패", 오류=str(e))
                    done[number] = None
            out = done[number]
            if out is not None:
                d = items[ref["column"]][ref["index"]]
                if out.title and out.title != d["title"] and d["title"] in overrides:   # 타이틀이 바뀌면 (계속) 수동 지정도 따라간다
                    overrides[out.title] = overrides[d["title"]]
                d["title"], d["reason"], d["content"] = out.title or d["title"], out.reason, out.content
        for number, out in done.items():
            if out is not None:
                checks[number]["lines"] = [out.title, *out.reason, *out.content]
                checks[number]["output"] = " ".join(checks[number]["lines"])
        log.event("AI 점검", 건수=len(done), 수정=sum(o is not None for o in done.values()))
        return {"items_this": items["this"], "items_next": items["next"], "checks": checks, "overrides": overrides}

    def mark(state: State, config):
        prev, weeks, user = tools.report_from_json(state["prev"]), _weeks(state), state["user"]
        overrides = state.get("overrides", {})
        # 이전 보고에 있던 건: 금주는 전주 보고서, 차주는 이번 보고서의 금주 칸
        before = {"this": {i.title for i in prev.this_items + prev.next_items},
                  "next": {d["title"] for d in state["items_this"]}}
        marked = {}
        shaped = {c: [dict(d) for d in state[f"items_{c}"]] for c in ("this", "next")}
        for ref in state.get("new_refs", []):     # 새로 쓴 건만 줄바꿈·들여쓰기·개발내용 형식을 정리한다(이월 건은 그대로)
            d = shaped[ref["column"]][ref["index"]]
            content = d["content"]
            if not ref["input"]["content"].strip():      # 내용을 비워 AI가 쓴 건은 기본으로 단일 항목(형식 A)으로 모은다
                content = [" ".join(content_items(content))]
            d["reason"] = wrap_reason(d["reason"])
            d["content_label"], d["content"] = format_content(content)
        new_idx = {(r["column"], r["index"]) for r in state.get("new_refs", [])}
        for column, week in (("this", weeks.this_week), ("next", weeks.next_week)):
            items = [tools.item_from_dict(d) for d in shaped[column]]
            for pos, item in enumerate(items):
                if item.raw is not None:
                    continue    # 형식이 다른 건은 원문 그대로
                if item.title in overrides:
                    item.continued = bool(overrides[item.title])
                else:
                    # 신규 건은 직전 보고에 없어도 실제 완료일이 그 주 이후면 (계속), 그 주에 끝나면 붙이지 않는다
                    item.continued = is_continuing(item, week, (column, pos) in new_idx or item.title in before[column])
            marked[column] = items
        csr = Csr(weeks.csr_week, **user["csr"])
        report = Report(date.fromisoformat(state["report_date"]), prev.dept, weeks.this_week, weeks.next_week,
                        marked["this"], marked["next"], csr)
        return {"items_this": [tools.item_to_dict(i) for i in marked["this"]],
                "items_next": [tools.item_to_dict(i) for i in marked["next"]], "report_json": tools.report_to_json(report)}

    def verify(state: State, config):
        problems = list(state.get("problems", []))
        for c in state.get("checks", []):
            problems += check_facts(c["output"], c["allowed"]) + check_tone(c.get("lines", []))
        report = tools.report_from_json(state["report_json"])
        problems += check(preview_text(report))
        tools.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = tools.REPORTS_DIR / ".tmp-verify.hwpx"
        render_report(tools.resolve_report(state.get("prev_name")), report, tmp)
        problems += verify_hwpx(tmp)
        tmp.unlink()
        config["configurable"]["log"].event("검증", 문제=problems)
        if not problems:
            return {"problems": [], "feedback": ""}
        count = state.get("retry_count", 0) + 1
        return {"problems": problems, "feedback": "; ".join(problems), "retry_count": count,
                "why": f"검증 실패({'; '.join(problems)[:120]})"}

    def approve(state: State, config):
        report = tools.report_from_json(state["report_json"])
        exists = (tools.REPORTS_DIR / f"{report.report_date:%Y%m%d}.hwpx").exists()
        new_marks = {(r["column"], r["index"]) for r in state.get("new_refs", [])}      # 새로 등록한 건의 위치
        answer = interrupt({"미리보기": preview_text(report), "항목": preview_items(report, new_marks), "기존파일": exists,
                            "입력": "승인 / 승인 덮어쓰기 / 반려: 사유"}).strip()
        ok = answer.startswith("승인")
        config["configurable"]["log"].event("승인", 승인자=approver, 결과="승인" if ok else "반려", 입력=answer)
        if ok:
            return {"approved": True, "overwrite": "덮어쓰기" in answer}
        reason = mask(answer.split(":", 1)[1].strip() if ":" in answer else answer)
        count = state.get("reject_count", 0) + 1
        return {"approved": False, "feedback": reason, "reject_count": count, "why": f"{count}번 반려", "reject_reason": reason}

    async def save(state: State, config):
        report = tools.report_from_json(state["report_json"])
        name = f"{report.report_date:%Y%m%d}.hwpx"
        out = json.loads(await call("save_report", {"report_json": state["report_json"], "out_name": name,
                                                    "overwrite": bool(state.get("overwrite")),
                                                    "template": state.get("prev_name") or ""}, state, config))
        if "오류" in out:
            return {"result": f"사람 이관: 저장 실패({out['오류']}) - 덮어쓰기 동의 또는 담당자 확인 필요"}
        config["configurable"]["log"].event("저장", 파일=out["저장"])
        return {"result": f"저장 완료: {out['저장']}"}

    def handoff(state: State, config):
        config["configurable"]["log"].event("이관", 사유=state.get("why"))
        return {"result": f"사람 이관: {state.get('why', '사유 없음')} - 담당자 확인 필요"}

    def after_verify(s):
        if not s.get("problems"):
            return "사용자 승인"
        return "사람 전담" if s["retry_count"] > MAX_RETRY else "초안"

    def after_approve(s):
        if s["approved"]:
            return "저장"
        return "사람 전담" if s["reject_count"] >= MAX_REJECT else "사용자 확인"     # 반려하면 내용 입력으로 돌아간다

    g = StateGraph(State)
    for name, fn in [("직전보고 읽기", read_prev), ("주차 계산", calc), ("이월 후보", carry), ("사용자 확인", confirm),
                     ("초안", draft), ("AI 점검", ai_review), ("계속 표시", mark), ("검증", verify), ("사용자 승인", approve),
                     ("저장", save), ("사람 전담", handoff)]:
        g.add_node(name, fn)
    g.add_edge(START, "직전보고 읽기")
    for a, b in [("직전보고 읽기", "주차 계산"), ("주차 계산", "이월 후보"), ("이월 후보", "사용자 확인"),
                 ("사용자 확인", "초안"), ("초안", "AI 점검"), ("AI 점검", "계속 표시"), ("계속 표시", "검증")]:
        g.add_edge(a, b)
    # 분기 목적지를 명시해 두면 그래프 구조(화면 하단 workflow 그림)가 실제와 같게 읽힌다
    g.add_conditional_edges("검증", after_verify, ["사용자 승인", "초안", "사람 전담"])
    g.add_conditional_edges("사용자 승인", after_approve, ["저장", "사용자 확인", "사람 전담"])
    g.add_edge("저장", END)
    g.add_edge("사람 전담", END)
    return g.compile(checkpointer=InMemorySaver())


def new_run(report_date: date | None):
    log = RunLog(report_date.isoformat() if report_date else "next")
    config = {"configurable": {"thread_id": f"run-{time.time()}", "log": log}, "callbacks": [log]}
    log.event("시작", 보고일=str(report_date))
    return config, log


async def advance(app, config, step, path: list):
    """그래프를 다음 interrupt(또는 끝)까지 진행한다. 멈춘 곳의 안내(dict)를 돌려주고, 끝나면 None"""
    paused = None
    async for update in app.astream(step, config, stream_mode="updates"):
        for node, value in update.items():
            if node == "__interrupt__":
                paused = value[0].value
            else:
                path.append(node)
    return paused


async def run(app, report_date: date | None, answers: list, show: bool = True, prev_name: str | None = None):
    """answers를 순서대로 사용자 확인·승인 답으로 넣으며 끝까지 실행. (노드 순서, 최종 상태, 로그 경로) 반환"""
    config, log = new_run(report_date)
    path, step = [], {"report_date": report_date.isoformat() if report_date else None, "prev_name": prev_name}
    while True:
        paused = await advance(app, config, step, path)
        if not paused:
            break
        if show:
            print(json.dumps(paused, ensure_ascii=False)[:600])
        if not answers:
            break
        step = Command(resume=answers.pop(0))
    final = app.get_state(config).values
    log.event("끝", 노드순서=path, 결과=final.get("result"), LLM호출=log.llm_calls, 토큰합계=log.tokens)
    if show:
        print(f"노드 순서: {' -> '.join(path)}\n결과: {final.get('result')}\n로그: {log.path}")
    return path, final, log.path
