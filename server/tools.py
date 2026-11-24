"""도구 묶음: 읽기·계산·검색은 자동, 저장은 사용자 승인 뒤에만(권한표 PERMISSION). 모두 JSON 문자열을 돌려준다"""
import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from hwpx_io import Csr, Report, read_report
from hwpx_render import render_report, verify_hwpx
from item_search import build_store, search_items
from schedule import Item, Period, Phase, calc_weeks

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UPLOADS_DIR = ROOT / "uploads"   # 화면에서 첨부한 직전 보고서
REPORTS_DIR = ROOT / "reports"
PERMISSION = {"read_report": "자동", "calc_weeks": "자동", "search_items": "자동", "save_report": "승인 후"}

_store = None  # 첫 검색 때 한 번만 만든다(임베딩 비용 절약)
_extra: list[Path] = []  # 첨부한 보고서: 유사 건 검색 대상에 더한다(한 사람이 쓰는 도구라 하나만 둔다)


def latest_report_path(data_dir: Path) -> Path:
    return sorted(p for p in Path(data_dir).glob("*.hwpx") if re.fullmatch(r"\d{8}", p.stem))[-1]


def resolve_report(name: str | None) -> Path:
    """보고서 파일 이름 → 경로. 첨부(uploads)가 data보다 우선하고, 이름이 없으면 data의 최신 파일"""
    if not name:
        return latest_report_path(DATA_DIR)
    name = Path(name).name
    for base in (UPLOADS_DIR, DATA_DIR):
        if (base / name).exists():
            return base / name
    raise FileNotFoundError(name)


def save_upload(filename: str, data: bytes, fallback_date: date | None = None) -> str:
    """첨부한 hwpx를 uploads에 저장하고 파일 이름을 돌려준다. 읽을 수 없으면 ValueError"""
    global _store
    name = Path(filename).name
    if not re.fullmatch(r"\d{8}\.hwpx", name):
        if fallback_date is None:
            raise ValueError("파일명이 YYYYMMDD.hwpx 형식이 아닙니다. 보고일을 직접 입력하세요.")
        name = f"{fallback_date:%Y%m%d}.hwpx"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOADS_DIR / name
    path.write_bytes(data)
    try:
        read_report(path)
    except Exception as e:  # zip이 아니거나 구조가 다르면 지우고 알린다
        path.unlink()
        raise ValueError(f"보고서를 읽을 수 없습니다: {type(e).__name__}: {e}") from e
    _extra[:] = [path]
    _store = None
    return name


def report_to_json(r: Report) -> str:
    return json.dumps(asdict(r), ensure_ascii=False, default=lambda d: d.isoformat())


def _d(text):
    return date.fromisoformat(text) if text else None


def _period(p) -> Period:
    return Period(_d(p["start"]), _d(p["end"]))


def item_from_dict(i) -> Item:
    phases = [Phase(p["name"], _d(p["start"]), _d(p["end"]), p["open_ended"]) for p in i["phases"]]
    return Item(i["title"], i["continued"], i["reason"], i["content_label"], i["content"], i["schedule_label"],
                phases, i["requester"], _d(i["due"]), i["page"], i["raw"], i.get("emphasis"))


def item_to_dict(i: Item) -> dict:
    return json.loads(json.dumps(asdict(i), ensure_ascii=False, default=lambda d: d.isoformat()))


def report_from_json(text: str) -> Report:
    r = json.loads(text)
    c = r["csr"]
    csr = Csr(_period(c["period"]), c["requested"], c["handled"], c["calls"], c["outages"], c["on_time"])
    return Report(_d(r["report_date"]), r["dept"], _period(r["this_week"]), _period(r["next_week"]),
                  [item_from_dict(i) for i in r["this_items"]], [item_from_dict(i) for i in r["next_items"]], csr)


def tool_read_report(file_name: str) -> str:
    return report_to_json(read_report(resolve_report(file_name)))


def tool_calc_weeks(report_date: str) -> str:
    return json.dumps(asdict(calc_weeks(date.fromisoformat(report_date))), ensure_ascii=False,
                      default=lambda d: d.isoformat())


def tool_search_items(query: str) -> str:
    global _store
    if _store is None:
        _store = build_store(DATA_DIR, extra=_extra)
    return json.dumps(search_items(_store, query), ensure_ascii=False)


def tool_save_report(report_json: str, out_name: str, overwrite: bool = False, template: str = "") -> str:
    name = Path(out_name).name
    target = REPORTS_DIR / name
    if target.exists() and not overwrite:
        return json.dumps({"오류": f"이미 존재: {name}"}, ensure_ascii=False)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REPORTS_DIR / f".tmp-{name}"
    render_report(resolve_report(template or None), report_from_json(report_json), tmp)
    problems = verify_hwpx(tmp)
    if problems:
        tmp.unlink()
        return json.dumps({"오류": problems}, ensure_ascii=False)
    tmp.replace(target)
    return json.dumps({"저장": str(target)}, ensure_ascii=False)


TOOLS = {"read_report": tool_read_report, "calc_weeks": tool_calc_weeks,
         "search_items": tool_search_items, "save_report": tool_save_report}


async def call_local(name: str, args: dict) -> str:
    """같은 프로세스 안에서 도구를 실행한다(웹 앱·테스트용). MCP 서버도 같은 함수를 쓴다"""
    return TOOLS[name](**args)
