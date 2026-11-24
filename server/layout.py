"""보고서 문장의 줄바꿈·들여쓰기. 셀 폭을 넘는 줄은 단어(공백) 단위로 직접 나누고, 이어지는 줄은 앞 항목 글자 폭만큼 들여쓴다.
(한글 프로그램의 자동 줄바꿈은 글자 단위로 단어를 끊을 수 있어서, 샘플처럼 직접 줄을 나눠 쓴다)"""
import unicodedata

from schedule import Item

LIMIT = 49        # 한 줄 폭(한글·전각 2, 영문·숫자·공백 1). 샘플로 보정: 이어지는 줄 49는 들어가고 다음 단어가 붙는 50은 줄이 바뀐다
BULLET_INDENT = 6  # '    · ' 다음에 이어지는 줄의 들여쓰기


def text_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def wrap(text: str, width: int) -> list[str]:
    """공백 기준으로 줄을 나눈다. 단어는 끊지 않는다(width보다 긴 단어는 그 단어만 한 줄을 차지한다)"""
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}" if current else word
        if not current or text_width(trial) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _logical_lines(lines) -> list[str]:
    """줄 목록을 논리 줄로 편다. 한 문자열 안의 줄바꿈도 별도 줄로 본다(빈 줄은 버린다)"""
    return [part.strip() for line in lines for part in str(line).splitlines() if part.strip()]


def prefix_width(label: str) -> int:
    """'  - 사유 : ' 같은 머리말의 폭. 이어지는 줄의 들여쓰기 칸 수와 같다(샘플: 사유는 11칸)"""
    return text_width(f"  - {label} : ")


def wrap_reason(lines: list[str]) -> list[str]:
    """사유는 샘플처럼 하나의 문단으로 이어 쓴다: 줄들을 합친 뒤 머리말 뒤 폭에 맞춰 단어 단위로 나눈다.
    이어지는 줄의 들여쓰기는 렌더러가 붙인다"""
    text = " ".join(_logical_lines(lines))
    return wrap(text, LIMIT - prefix_width("사유")) if text else []


CONTENT_INDENT = 15   # 이어지는 줄의 들여쓰기('  - 개발내용 : ' 폭). 형식 A·B 모두 같다


def wrap_hanging(text: str, first_width: int) -> list[str]:
    """첫 줄은 first_width, 이어지는 줄은 15칸 들여쓰기를 뺀 폭으로 단어 단위로 나눈다"""
    first = wrap(text, first_width)
    if len(first) == 1:
        return first
    rest = text[len(first[0]):].split()
    return [first[0]] + wrap(" ".join(rest), LIMIT - CONTENT_INDENT)


def content_items(lines) -> list[str]:
    """내용 줄들을 항목 목록으로 만든다: 줄(문자열 안의 줄바꿈 포함)마다 하나, 글머리(·)는 걷어낸다"""
    return [item for item in (line.lstrip("·").strip() for line in _logical_lines(lines)) if item]


def format_content(lines: list[str]) -> tuple[str, list[str]]:
    """내용을 (머리말, 줄들)로 만든다.
    형식 A: 항목이 하나(또는 없음)이면 머리말 '내용'. 첫 줄은 '- 내용 : ' 뒤에 이어 쓰고 이어지는 줄은 15칸 들여쓴다.
    형식 B: 항목이 여럿이면 머리말 '개발내용'. 머리 줄 아래에 '    · ' 항목으로 쓰고 이어지는 줄은 15칸 들여쓴다.
    단어는 끊지 않고 줄은 폭(LIMIT)을 넘지 않는다"""
    items = content_items(lines)
    pad = " " * CONTENT_INDENT
    if len(items) <= 1:
        segs = wrap_hanging(items[0], LIMIT - prefix_width("내용")) if items else []
        return "내용", segs[:1] + [pad + seg for seg in segs[1:]]
    out = []
    for item in items:
        segs = wrap_hanging(item, LIMIT - BULLET_INDENT)
        out += ["    · " + segs[0]] + [pad + seg for seg in segs[1:]]
    return "개발내용", out


def format_added_line(item: Item, text: str) -> list[str]:
    """이월 건의 개발내용 끝에 붙일 줄(들). 건이 이미 글머리(·)를 쓰면 같은 글머리로, 아니면 글머리 없이
    이어지는 줄의 들여쓰기에 맞춰 붙인다. 길면 단어 단위로 나눈다"""
    existing = item.content
    if any(line.lstrip().startswith("·") for line in existing):
        segs = wrap(text, LIMIT - BULLET_INDENT)
        return ["    · " + segs[0]] + [" " * BULLET_INDENT + seg for seg in segs[1:]]
    continuation = [line for line in existing[1:] if line.strip()]
    indent = (len(continuation[-1]) - len(continuation[-1].lstrip())) if continuation else prefix_width(item.content_label or "개발내용")
    segs = wrap(text, LIMIT - indent)
    lines = [" " * indent + seg for seg in segs]
    if not existing:                       # 내용이 비어 있으면 첫 줄은 머리 줄 뒤에 이어 쓰는 줄이다
        lines[0] = segs[0]
    return lines
