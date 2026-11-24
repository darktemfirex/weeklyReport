"""검증표 케이스를 한 번에 돌려 기대 동작과 비교한다
실행: .venv\\Scripts\\python.exe 검증\\run_cases.py            가짜 모델로 전 케이스(오프라인)
      .venv\\Scripts\\python.exe 검증\\run_cases.py --live     첫 케이스만 실제 모델로
결과: 검증/비교표.md
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "server"))

import graph  # noqa: E402
import tools  # noqa: E402
from agent import Composed  # noqa: E402
from guardrails import find_pii  # noqa: E402
from hwpx_io import P, load_section, para_text, read_report  # noqa: E402
from hwpx_render import render_report, verify_hwpx  # noqa: E402


def fake_compose(mode):
    async def compose(inp, feedback=""):
        title = "신규 2099 개발" if mode == "숫자지어냄" else "신규 테스트 개발"
        return Composed(title, [inp.reason], ["    · " + inp.content])
    return compose


def hwpx_text(path: Path) -> str:
    return "".join(para_text(p) for p in load_section(path)[0].iter(P + "p"))


def judge(case, path, final, out_files, log_path):
    """기대 동작과 실제를 비교해 (통과 여부, 어긋난 점)을 돌려준다"""
    problems = []
    if path[-1] != case["끝"]:
        problems.append(f"끝 노드 {path[-1]}")
    if len(out_files) != case["기록"]:
        problems.append(f"저장 파일 {len(out_files)}건")
    if case.get("사유") and case["사유"] not in (final.get("result") or ""):
        problems.append(f"결과 문구 다름({final.get('result')})")
    if case.get("초안") and path.count("초안") != case["초안"]:
        problems.append(f"초안 {path.count('초안')}번")
    texts = [hwpx_text(f) for f in out_files] + ([log_path.read_text(encoding="utf-8")] if log_path else [])
    for f in out_files:
        problems += verify_hwpx(f)
        if "금주건수" in case and len([i for i in read_report(f).this_items]) != case["금주건수"]:
            problems.append("금주 건수 다름")
    if leaked := sorted({k for t in texts for k in find_pii(t)}):
        problems.append(f"개인정보 원문 노출({', '.join(leaked)})")
    return not problems, problems


async def run_case(case, live=False):
    saved = tools.REPORTS_DIR, graph.LOGS_DIR, graph.MIN_ITEMS
    graph.MIN_ITEMS = case.get("최소", 1)   # 건수 규칙은 "최소"를 지정한 케이스에서만 켠다
    try:
        return await _run_case(case, live)
    finally:
        tools.REPORTS_DIR, graph.LOGS_DIR, graph.MIN_ITEMS = saved   # 다른 테스트에 임시 폴더·설정이 남지 않게


async def _run_case(case, live=False):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tools.REPORTS_DIR, graph.LOGS_DIR = tmp / "reports", tmp / "logs"
        if case.get("사전파일"):
            tools.REPORTS_DIR.mkdir()
            (tools.REPORTS_DIR / case["사전파일"]).write_bytes(b"old")
        if live:
            from agent import COMPOSE_SYSTEM, Agent, make_compose, search_tools
            agent = Agent(search_tools(), COMPOSE_SYSTEM)
            compose = make_compose(agent)
        else:
            compose = fake_compose(case["compose"])
        app = graph.build_graph(compose, tools.call_local)
        path, final, log_path = await graph.run(app, None, list(case["답"]), show=False)
        out = sorted(f for f in tools.REPORTS_DIR.glob("*.hwpx") if f.read_bytes() != b"old") if tools.REPORTS_DIR.exists() else []
        ok, problems = judge(case, path, final, out, log_path)
        if case.get("기존유지") and (tools.REPORTS_DIR / case["사전파일"]).read_bytes() != b"old":
            ok, problems = False, problems + ["기존 파일이 바뀜"]
        return {"path": " -> ".join(path), "ok": ok, "problems": problems}


def run_golden(case):
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        for name in case["골든"]:
            template = tools.DATA_DIR / f"{name}.hwpx"   # 원문 복사 건의 서식 ID는 그 파일 것이라 자기 자신을 템플릿으로 쓴다
            report = read_report(template)
            out = Path(tmp) / f"{name}.hwpx"
            render_report(template, report, out)
            if read_report(out) != report:
                problems.append(f"{name} 왕복 불일치")
            problems += [f"{name}: {p}" for p in verify_hwpx(out)]
    return {"path": "파싱 → 렌더 → 파싱", "ok": not problems, "problems": problems}


async def run_all(live=False):
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    rows = []
    for number, case in enumerate(cases):
        if "골든" in case:
            row = run_golden(case)
        elif live and number > 0:
            continue
        else:
            row = await run_case(case, live and number == 0)
        rows.append(row)
        print(f"{case['케이스']}: {'통과' if row['ok'] else '실패 - ' + ', '.join(row['problems'])}")
    return rows


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    live = "--live" in sys.argv
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    rows = await run_all(live)
    shown = [c for n, c in enumerate(cases) if not (live and n > 0 and "골든" not in c)]
    lines = ["| 케이스 | 기대 동작 | 결과 |", "|---|---|---|"]
    for c, r in zip(shown, rows):
        lines.append(f"| {c['케이스']} | {c['기대']} | {'통과' if r['ok'] else '실패: ' + ', '.join(r['problems'])}<br>{r['path']} |")
    (HERE / "비교표.md").write_text("# 검증 케이스 비교표\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
