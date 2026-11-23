"""테스트용: 출력 hwpx에서 문단별 굵기를 읽는다 (header.xml의 charPr에 <hh:bold/>가 있으면 굵게)"""
import re
import zipfile

from hwpx_io import P, load_section, para_text


def bold_of_char_pr(path) -> dict[str, bool]:
    header = zipfile.ZipFile(path).read("Contents/header.xml").decode("utf-8")
    return {m.group(1): "<hh:bold/>" in m.group(0)
            for m in re.finditer(r'<hh:charPr id="(\d+)".*?</hh:charPr>', header, re.S)}


def paragraph_bold(path) -> list[tuple[str, set]]:
    """(문단 글자, 그 문단 run들의 굵기 집합) 목록. 글자가 있는 문단만"""
    bold = bold_of_char_pr(path)
    root, _ = load_section(path)
    out = []
    for p in root.iter(P + "p"):
        text = para_text(p)
        if text.strip() and p.find(".//" + P + "tbl") is None:
            out.append((text, {bold[r.get("charPrIDRef")] for r in p.findall(P + "run")}))
    return out


def bold_flags(path, startswith: str) -> set:
    """startswith로 시작하는 문단(앞 공백 무시)의 굵기 집합"""
    flags = set()
    for text, kinds in paragraph_bold(path):
        if text.strip().startswith(startswith):
            flags |= kinds
    return flags
