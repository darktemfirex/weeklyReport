"""주간보고 웹 화면 서버 (FastAPI + HTML 폼)
실행: .venv\\Scripts\\python.exe -m uvicorn web:app --host 127.0.0.1 --port 8501
흐름: 직전 보고서 선택/첨부 → 이월 후보·신규 건·CSR 입력(confirm) → 초안·검증 → 미리보기 → 승인(approve) → hwpx 저장·내려받기
작업 순서는 graph.py(LangGraph)가 맡고, 여기서는 단계마다 입력을 받아 이어 줄 뿐이다."""
import re
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

try:   # 첫 요청이 느리지 않게 모델 라이브러리를 서버 시작 때 미리 불러온다
    import langchain_openai  # noqa: F401
except ImportError:
    pass

import graph
import tools
from schedule import calc_weeks, next_report_date
from validation import validate_answer

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 20 * 1024 * 1024
MAX_RUNS = 50


class RunIn(BaseModel):
    prev_name: str | None = None


class ApproveIn(BaseModel):
    answer: str


@dataclass
class Run:
    graph: object
    config: dict
    path: list
    paused: dict | None
    stage: str
    candidates: list


def default_compose_factory(log):
    """실제 모델(.env)로 문구를 쓰는 compose. 모델 호출은 실행 로그에 남긴다"""
    from agent import COMPOSE_SYSTEM, Agent, make_compose, make_review, search_tools
    agent = Agent(search_tools(), COMPOSE_SYSTEM)
    agent.callbacks = [log]
    compose = make_compose(agent)
    compose.review = make_review([log])     # AI 점검 단계에서 쓴다
    return compose


def _dated(name: str) -> bool:
    return re.fullmatch(r"\d{8}\.hwpx", name) is not None


def _default_prev_name() -> str | None:
    names = sorted(p.name for p in tools.DATA_DIR.glob("*.hwpx") if _dated(p.name))
    return names[-1] if names else None


def _info(prev_name: str | None) -> dict:
    name = prev_name or _default_prev_name()
    if not name:
        raise HTTPException(404, "첨부한 파일이 없고 data 폴더에도 보고서가 없습니다. 직전 주간보고 hwpx를 첨부하세요.")
    try:
        path = tools.resolve_report(name)
    except FileNotFoundError:
        raise HTTPException(404, f"보고서를 찾을 수 없습니다: {name}")
    prev_date = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
    weeks = calc_weeks(next_report_date(prev_date))
    span = lambda p: {"start": p.start.isoformat(), "end": p.end.isoformat()}
    return {"prev_name": path.name, "source": "upload" if path.parent == tools.UPLOADS_DIR else "data",
            "report_date": weeks.report_date.isoformat(), "new_name": f"{weeks.report_date:%Y%m%d}.hwpx",
            "weeks": {"this_week": span(weeks.this_week), "next_week": span(weeks.next_week),
                      "csr_week": span(weeks.csr_week)}}


# 화면 하단 workflow 그림용 설명. 노드와 연결은 실제 LangGraph 정의(graph.build_graph)에서 읽고, 여기서는 설명만 붙인다.
NODE_INFO = {
    "직전보고 읽기": ("도구", "read_report로 직전 hwpx를 읽어 건·일정·CSR을 파싱합니다."),
    "주차 계산": ("코드", "calc_weeks: 보고일 = 직전 + 7일, 금주·차주·CSR 접수기간(월~금)을 계산합니다."),
    "이월 후보": ("코드", "일정이 새 금주/차주와 겹치는 직전 보고서 건을 후보로 제안합니다."),
    "사용자 확인": ("사람", "후보 선택, 신규 건, CSR 입력. 합계 5건 이상, 금주 이월 건은 개발내용 1줄이 필수입니다."),
    "초안": ("LLM", "작성 에이전트가 과거 건을 검색(RAG)하고 전주 주간보고 맥락을 보며 타이틀·사유·개발내용을 보고체로 씁니다."),
    "AI 점검": ("LLM", "AI가 신규 건의 타이틀·사유·개발내용을 한 번 더 점검합니다(보고체·존댓말 금지·타이틀 형식·사실 유지)."),
    "계속 표시": ("코드", "이전 보고에 있던 건이고 완료(예정)일이 그 주 이후이면 (계속)을 붙입니다."),
    "검증": ("가드레일", "입력에 없는 숫자·개인정보를 검사하고 hwpx 구조를 확인합니다."),
    "사용자 승인": ("사람", "미리보기를 보고 승인 또는 반려합니다. 반려하면 내용 입력으로 돌아가 수정합니다."),
    "저장": ("도구", "승인 뒤에만 save_report로 reports/YYYYMMDD.hwpx를 저장합니다."),
    "사람 전담": ("이관", "재작성·반려 한도를 넘기면 담당자 확인으로 넘기고 저장하지 않습니다."),
}
EDGE_NOTES = {
    ("검증", "초안"): "검증 실패 → 재작성(최대 2회)",
    ("검증", "사용자 승인"): "검증 통과",
    ("검증", "사람 전담"): "재작성 한도 초과",
    ("사용자 승인", "사용자 확인"): "반려 → 내용 입력으로 돌아가 수정(사유는 다시 쓸 때 반영)",
    ("사용자 승인", "저장"): "승인",
    ("사용자 승인", "사람 전담"): "반려 2회",
}


EDGE_LABELS = {   # 흐름도 화살표 옆에 쓰는 짧은 말
    ("검증", "초안"): "재작성(최대 2회)", ("검증", "사용자 승인"): "통과", ("검증", "사람 전담"): "한도 초과",
    ("사용자 승인", "사용자 확인"): "반려 → 수정", ("사용자 승인", "저장"): "승인", ("사용자 승인", "사람 전담"): "반려 2회",
}


def _workflow() -> dict:
    g = graph.build_graph(None, None).get_graph()    # 구조만 읽는다(실행하지 않음)
    edges = [(e.source, e.target, bool(getattr(e, "conditional", False))) for e in g.edges]
    out_of = {}
    for source, target, _ in edges:
        out_of.setdefault(source, []).append(target)
    order, queue = [], ["__start__"]
    while queue:                                     # 시작에서 따라가며 보이는 순서대로
        node = queue.pop(0)
        if node in order:
            continue
        order.append(node)
        queue += sorted(out_of.get(node, []), key=lambda t: NODE_INFO.get(t, ("",))[0] == "이관")   # 이관 노드는 맨 뒤
    names = sorted((n for n in order if not n.startswith("__")), key=lambda n: NODE_INFO[n][0] == "이관")   # 이관 노드는 맨 뒤(정렬은 안정적)
    return {
        "nodes": [{"id": n, "kind": NODE_INFO[n][0], "desc": NODE_INFO[n][1],
                   "terminal": "__end__" in out_of.get(n, [])} for n in names],
        "edges": [{"from": s, "to": t, "conditional": c, "note": EDGE_NOTES.get((s, t), ""), "label": EDGE_LABELS.get((s, t), "")}
                  for s, t, c in edges if not s.startswith("__") and not t.startswith("__")],
    }


def _stage(paused: dict | None) -> str:
    if paused is None:
        return "done"
    return "approve" if "미리보기" in paused else "confirm"


def create_app(compose_factory=default_compose_factory) -> FastAPI:
    app = FastAPI(title="주간보고 작성 자동화 Agent")
    runs: dict[str, Run] = {}
    workflow = _workflow()

    @app.middleware("http")
    async def no_cache(request, call_next):    # 수정한 화면 파일이 바로 보이게
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def get_run(run_id: str, stage: str | None = None) -> Run:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "작성 세션을 찾을 수 없습니다. 처음부터 다시 시작하세요.")
        if stage and run.stage != stage:
            raise HTTPException(409, f"지금은 '{run.stage}' 단계입니다.")
        return run

    def describe(run_id: str, run: Run) -> dict:
        out = {"run_id": run_id, "stage": run.stage, "path": list(run.path)}   # 지금까지 실행한 노드(화면의 진행 표시용)
        if run.stage == "confirm":
            out.update(candidates=run.candidates, weeks=run.paused["주차"], min_items=graph.MIN_ITEMS,
                       error=run.paused.get("오류"), reject_reason=run.paused.get("반려사유", ""))
        elif run.stage == "approve":
            out.update(preview=run.paused["미리보기"], existing=run.paused["기존파일"], items=run.paused["항목"])
        else:
            result = run.graph.get_state(run.config).values.get("result", "")
            out.update(result=result, saved=result.startswith("저장 완료"))
        return out

    async def advance(run: Run, step) -> None:
        try:
            run.paused = await graph.advance(run.graph, run.config, step, run.path)
        except Exception as e:   # 모델 연결·렌더 오류 등. 키 값은 메시지에 없다
            raise HTTPException(500, f"{type(e).__name__}: {e}")
        run.stage = _stage(run.paused)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/workflow")
    def get_workflow():
        return workflow

    @app.get("/api/info")
    def info(prev: str | None = None):
        return _info(prev)

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...), fallback_date: str = Form("")):
        if not (file.filename or "").lower().endswith(".hwpx"):
            return JSONResponse({"detail": "hwpx 파일만 첨부할 수 있습니다."}, status_code=400)
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"detail": "파일이 너무 큽니다(20MB 이하)."}, status_code=400)
        try:
            fallback = date.fromisoformat(fallback_date) if fallback_date else None
            name = tools.save_upload(file.filename, data, fallback)
        except ValueError as e:
            message = str(e)
            return JSONResponse({"detail": message, "need_date": message.startswith("파일명이")}, status_code=400)
        return _info(name)

    @app.post("/api/runs")
    async def create_run(body: RunIn):
        prev_name = body.prev_name or None
        _info(prev_name)    # 파일이 없으면 404
        config, log = graph.new_run(None)
        try:
            compose = compose_factory(log)
        except Exception as e:
            raise HTTPException(500, f"{type(e).__name__}: {e}")
        run = Run(graph.build_graph(compose, tools.call_local, review=getattr(compose, "review", None)),
                  config, [], None, "confirm", [])
        await advance(run, {"report_date": None, "prev_name": prev_name})
        if run.stage != "confirm":
            raise HTTPException(500, "직전 보고서를 읽지 못했습니다.")
        run.candidates = run.paused["후보"]
        run_id = uuid.uuid4().hex
        runs[run_id] = run
        while len(runs) > MAX_RUNS:
            runs.pop(next(iter(runs)))
        return describe(run_id, run)

    @app.post("/api/runs/{run_id}/confirm")
    async def confirm(run_id: str, answer: dict = Body(...)):
        run = get_run(run_id, "confirm")
        errors = validate_answer(answer, run.candidates, graph.MIN_ITEMS)
        if errors:
            return JSONResponse({"errors": errors}, status_code=422)
        await advance(run, Command(resume=answer))
        return describe(run_id, run)

    @app.post("/api/runs/{run_id}/approve")
    async def approve(run_id: str, body: ApproveIn):
        run = get_run(run_id, "approve")
        await advance(run, Command(resume=body.answer))
        return describe(run_id, run)

    @app.get("/api/runs/{run_id}/download")
    def download(run_id: str):
        run = get_run(run_id)
        match = re.match(r"저장 완료: (.+)", run.graph.get_state(run.config).values.get("result", "")) if run.stage == "done" else None
        if not match or not Path(match.group(1)).exists():
            raise HTTPException(404, "내려받을 보고서가 없습니다.")
        path = Path(match.group(1))
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
