import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "검증"))
import run_cases  # noqa: E402


def test_all_cases_pass_offline():
    rows = asyncio.run(run_cases.run_all(live=False))
    assert len(rows) == 8
    assert [r["problems"] for r in rows if not r["ok"]] == []


def test_judge_reports_mismatch():
    case = {"케이스": "x", "끝": "저장", "기록": 1}
    ok, problems = run_cases.judge(case, ["초안", "사람 전담"], {"result": "사람 이관: 사유"}, [], None)
    assert not ok and any("끝 노드" in p for p in problems) and any("저장 파일" in p for p in problems)
