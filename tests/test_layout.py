from layout import LIMIT, format_added_line, format_content, text_width, wrap, wrap_reason
from schedule import Item


def item(content, label="개발내용"):
    return Item("t", False, [], label, content, None, [], "r", None)


def test_text_width_counts_hangul_as_two():
    assert text_width("사유") == 4 and text_width("abc 1") == 5
    assert text_width("  - 사유 : ") == 11            # 샘플의 사유 이어쓰기 들여쓰기(공백 11칸)와 같다


def test_wrap_never_splits_a_word():
    text = "축산부 매입등록 화면 거래명세서 첨부 기능 적용 완료에 따른 수산부 동일 기능 확대 적용 요청"
    lines = wrap(text, 36)
    assert len(lines) > 1 and all(text_width(line) <= 36 for line in lines)
    assert " ".join(lines) == text                                        # 줄을 이으면 원문 그대로 = 단어가 끊기지 않았다
    assert all(word in text.split() for line in lines for word in line.split())


def test_wrap_matches_the_sample_first_reason_line():
    # 샘플: '  - 사유 : 축산부 매입등록 화면 거래명세서 첨부' 다음에 '기능 …'이 이어진다
    lines = wrap_reason(["축산부 매입등록 화면 거래명세서 첨부 기능 적용 완료에 따른 수산부 동일 기능 확대 적용 요청"])
    assert lines[0] == "축산부 매입등록 화면 거래명세서 첨부" and lines[1].startswith("기능 적용 완료에 따른")


def test_wrap_keeps_a_too_long_word_whole():
    assert wrap("가" * 30 + " 나", 10) == ["가" * 30, "나"]


def test_wrap_short_and_empty_text():
    assert wrap("짧은 문장", 40) == ["짧은 문장"] and wrap("", 40) == [""]
    assert wrap("  여러   공백   정리  ", 40) == ["여러 공백 정리"]


def test_wrap_reason_flows_lines_into_one_paragraph_and_fits_after_the_label():
    lines = wrap_reason(["첫 줄은 짧음", "두번째 줄은 아주 길어서 줄바꿈이 필요한 문장으로 일부러 길게 작성한 설명이며 계속 이어짐"])
    width = LIMIT - text_width("  - 사유 : ")
    assert all(text_width(line) <= width for line in lines)
    assert lines[0].startswith("첫 줄은 짧음 두번째")                      # 사유는 한 문단: 모델이 나눈 줄도 이어서 채운다
    assert " ".join(lines) == "첫 줄은 짧음 두번째 줄은 아주 길어서 줄바꿈이 필요한 문장으로 일부러 길게 작성한 설명이며 계속 이어짐"


# 형식 A: 항목이 하나면 '- 내용 : ' 뒤에 이어 쓴다 / 형식 B: 여러 항목이면 '- 개발내용' 아래에 '· ' 항목으로 쓴다
def test_single_item_is_form_a_label_naeyong_inline():
    label, out = format_content(["· 상품코드 입력 시 정합성 체크 기능 추가"])
    assert (label, out) == ("내용", ["상품코드 입력 시 정합성 체크 기능 추가"])   # 짧으면 한 줄
    label, out = format_content(["상품코드 입력 시 정합성 체크 기능을 추가하고 오류 메시지를 표시하도록 보완"])
    assert label == "내용" and text_width("  - 내용 : " + out[0]) <= LIMIT
    assert all(line.startswith(" " * 15) and not line.startswith(" " * 16) for line in out[1:])   # 이어지는 줄은 15칸 들여쓰기


def test_multiple_items_are_form_b_with_bullets_under_the_label():
    label, out = format_content(["데몬 에러 집계 테이블 존재 여부 확인", "정합성 오류 발생 시 저장 제한 및 오류 메시지 표시"])
    assert label == "개발내용"
    assert out[0] == "    · 데몬 에러 집계 테이블 존재 여부 확인"
    assert out[1].startswith("    · 정합성 오류 발생 시 저장 제한 및") and out[2].startswith(" " * 15)   # 예시 B
    assert " ".join(l.strip().lstrip("·").strip() for l in out[1:]) == "정합성 오류 발생 시 저장 제한 및 오류 메시지 표시"
    assert all(text_width(line) <= LIMIT for line in out)


def test_format_content_empty_and_newline_items():
    assert format_content([]) == ("내용", []) and format_content(["", "  "]) == ("내용", [])
    assert format_content(["첫 항목\n둘째 항목"])[0] == "개발내용"                    # 문자열 안의 줄바꿈도 항목 구분
    assert format_content(["    · 이미 글머리가 있는 단일 항목"]) == ("내용", ["이미 글머리가 있는 단일 항목"])


def test_format_content_wraps_at_words_and_keeps_all_text():
    text = "원가산입등록 내역 조회 시 합계가 누락되는 현상과 상품코드 입력 시 조회 필수 체크가 되지 않는 정합성 문제를 함께 보완"
    label, out = format_content([text])
    assert label == "내용" and len(out) >= 2 and all(text_width(line) <= LIMIT for line in out)
    assert all(line.startswith(" " * 15) and not line.startswith(" " * 16) for line in out[1:])
    assert " ".join(line.strip() for line in out) == text


def test_added_line_follows_bullet_style_when_the_item_uses_bullets():
    it = item(["    · 첫 항목", "    · 둘째 항목"])
    assert format_added_line(it, "금주 진행") == ["    · 금주 진행"]
    long = format_added_line(it, "이번 주에는 상품명 출력 영역을 확대하고 줄바꿈 처리를 추가하는 작업을 진행하여 현장 확인까지 마침")
    assert long[0].startswith("    · ") and all(line.startswith("      ") for line in long[1:])
    assert all(text_width(line) <= LIMIT for line in long)


def test_added_line_aligns_with_existing_continuation_lines():
    it = item(["첫 줄", "            둘째 줄"], label="내용")                 # 이어지는 줄이 12칸 들여쓰기
    assert format_added_line(it, "추가") == ["            추가"]


def test_added_line_uses_label_prefix_width_when_no_continuation():
    out = format_added_line(item(["첫 줄"], label="개발내용"), "추가")
    assert out == [" " * 15 + "추가"]


def test_added_line_on_empty_content_becomes_the_inline_first_line():
    assert format_added_line(item([]), "추가") == ["추가"]


def test_newlines_inside_a_string_are_separate_logical_lines():
    assert wrap_reason(["첫 줄\n둘째 줄", "셋째"]) == ["첫 줄 둘째 줄 셋째"]            # 사유는 이어 쓰고
    assert format_content(["첫 줄\n둘째 줄"]) == ("개발내용", ["    · 첫 줄", "    · 둘째 줄"])   # 내용은 줄(항목) 단위 유지
