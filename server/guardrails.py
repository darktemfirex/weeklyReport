"""가드레일: 규칙1 입력에 없는 숫자 금지(check_facts), 규칙2 개인정보 원문 금지(check)·가리기(mask), 규칙3 보고체(존댓말 금지, check_tone)"""
import re

PII = {  # 이름: (찾는 모양, 가리는 방법)
    "주민등록번호": (r"(?<!\d)(\d{6})-([1-4])\d{6}(?!\d)", r"\1-\2******"),
    "카드번호": (r"(?<!\d)(\d{4})-\d{4}-\d{4}-(\d{4})(?!\d)", r"\1-****-****-\2"),
    "전화번호": (r"(?<!\d)(01[016789])[-. ]?\d{3,4}[-. ]?(\d{4})(?!\d)", r"\1-****-\2"),
}


def mask(text: str) -> str:
    """개인정보를 가운데만 *로 가린다 (예: 010-****-5678)"""
    for pattern, repl in PII.values():
        text = re.sub(pattern, repl, text)
    return text


def find_pii(text: str) -> list[str]:
    return [name for name, (pattern, _) in PII.items() if re.search(pattern, text)]


def check(text: str) -> list[str]:
    """규칙2: 개인정보 원문이 있으면 위반 목록을 돌려준다. 빈 목록이면 통과"""
    found = find_pii(text)
    return [f"규칙2 개인정보 원문: {', '.join(found)}"] if found else []


def check_facts(output: str, allowed: str) -> list[str]:
    """규칙1: output의 숫자 덩어리 중 allowed(입력·계산 결과)에 없는 것을 위반으로 돌려준다"""
    known = set(re.findall(r"\d+", allowed))
    invented = sorted(set(re.findall(r"\d+", output)) - known)
    return [f"규칙1 입력에 없는 숫자: {n}" for n in invented]


POLITE_END = re.compile(r"(니다|세요|해요|어요|아요|예요|이에요|네요|죠)[\s.!?]*$")


def check_tone(lines: list[str]) -> list[str]:
    """규칙3: 보고서 문장은 개조식(체언·명사형 종결)이어야 한다. 존댓말(~합니다, ~입니다, ~해요 등)로 끝나는 줄을 위반으로 돌려준다"""
    found = []
    for line in lines:
        text = line.strip().lstrip("·").strip()
        if text and POLITE_END.search(text):
            found.append(f"규칙3 존댓말(보고체로 쓸 것): {text[:40]}")
    return found
