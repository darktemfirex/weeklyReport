"""입력 검사. 웹 화면(JS)이 먼저 확인하고, 서버(web.py)가 같은 규칙으로 다시 확인한다."""
import re
from datetime import date

import graph


WORK_TYPES = ("개선 개발", "신규 개발")
HANGUL = re.compile(r"[가-힣]")
PROGRAM_CODE = re.compile(r"[A-Za-z0-9_-]+")


def _needs_hangul(label: str, text: str) -> list[str]:
    """입력했다면 한글이 한 글자 이상 있어야 한다(한글 기준 입력). label은 조사까지 포함한다(예: '사유는')"""
    if text.strip() and not HANGUL.search(text):
        return [f"{label} 한글로 입력하세요(한글이 한 글자 이상 있어야 합니다)."]
    return []


def validate_item_form(reason: str, content: str, phases: list[dict], requester: str,
                       program_name: str = "", program_code: str = "", work_type: str = "") -> list[str]:
    """신규 건 입력 검사. 요청부서·주요일정이 필수이고, 프로그램명·사유·내용은 하나 이상만 있으면 나머지는 AI가 쓴다.
    입력한 글은 모두 한글 기준(한글 포함)이어야 한다."""
    errors = []
    if not (program_name.strip() or reason.strip() or content.strip()):
        errors.append("프로그램명, 사유, 내용 중 하나 이상은 입력하세요(비워 둔 항목은 AI가 작성합니다).")
    errors += _needs_hangul("프로그램명은", program_name) + _needs_hangul("사유는", reason) + _needs_hangul("내용은", content)
    if program_code.strip() and not PROGRAM_CODE.fullmatch(program_code.strip()):
        errors.append("프로그램코드는 영문·숫자·하이픈(-)·밑줄(_)만 쓸 수 있습니다(예: 70-2048).")
    if work_type.strip() and work_type not in WORK_TYPES:
        errors.append("구분은 '개선 개발' 또는 '신규 개발' 중에서 고르세요.")
    if not requester.strip():
        errors.append("요청부서를 입력하세요.")
    else:
        errors += _needs_hangul("요청부서는", requester)
    if not phases:
        errors.append("주요일정 단계를 하나 이상 입력하세요.")
    for ph in phases:
        if not ph["name"].strip():
            errors.append("주요일정 단계 이름을 입력하세요.")
        else:
            errors += _needs_hangul("주요일정 단계 이름은", ph["name"])
        if ph.get("start") is None:
            errors.append(f"'{ph['name']}' 시작일을 입력하세요.")
        elif (not ph.get("open_ended") and ph.get("end") and ph["end"] < ph["start"]
              and not ph["name"].startswith("본적용")):          # 본적용 및 모니터링은 종료일을 쓰지 않는다
            errors.append(f"'{ph['name']}' 종료일이 시작일보다 빠릅니다.")
    return errors


def validate_csr(requested: int, handled: int, calls: int, outages: int, on_time: int) -> list[str]:
    errors = []
    if min(requested, handled, calls, outages, on_time) < 0:
        errors.append("CSR 숫자는 0 이상이어야 합니다.")
    if handled > requested:
        errors.append("처리건수가 요청건수보다 많습니다.")
    return errors


def _date(text) -> date | None:
    return date.fromisoformat(text) if text else None


def validate_answer(answer: dict, candidates: list, minimum: int) -> list[str]:
    """사용자 확인 답 전체를 검사한다. 오류 문구 목록(빈 목록 = 통과)"""
    errors = []
    for number, new in enumerate(answer.get("new", []), 1):
        prefix = f"신규 건 {number}: "
        try:
            phases = [{"name": p.get("name", ""), "start": _date(p.get("start")), "end": _date(p.get("end")),
                       "open_ended": bool(p.get("open_ended"))} for p in new.get("phases", [])]
            _date(new.get("due"))
        except (ValueError, TypeError):
            errors.append(prefix + "날짜 형식이 올바르지 않습니다(YYYY-MM-DD).")
            continue
        errors += [prefix + e for e in validate_item_form(
            new.get("reason", ""), new.get("content", ""), phases, new.get("requester", ""),
            new.get("program_name") or new.get("title", ""), new.get("program_code", ""), new.get("work_type", ""))]
        if len(new.get("columns", [])) != 1:
            errors.append(prefix + "들어갈 칸은 금주 또는 차주 중 하나만 고르세요.")
    try:
        csr = answer["csr"]
        errors += validate_csr(*(int(csr[k]) for k in ("requested", "handled", "calls", "outages", "on_time")))
    except (KeyError, TypeError, ValueError):
        errors.append("CSR 숫자 5개를 모두 입력하세요.")
    count = graph.count_items(answer)
    if count < minimum:
        errors.append(f"현재 {count}건 · 금주+차주 합계 {minimum}건 이상이어야 합니다. {minimum - count}건 부족합니다. 신규 건을 추가하세요.")
    errors += [f"금주 '{t}': 개발내용 1줄을 입력하세요." for t in graph.missing_add_lines(answer, candidates)]
    return errors
