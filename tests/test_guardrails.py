from guardrails import check, check_facts, mask


def test_mask_phone():
    assert mask("연락처 010-1234-5678") == "연락처 010-****-5678"


def test_mask_resident_and_card():
    assert mask("900101-1234567") == "900101-1******"
    assert mask("1234-5678-9012-3456") == "1234-****-****-3456"


def test_check_pii():
    assert check("주민번호 900101-1234567 확인")[0].startswith("규칙2")
    assert check("평범한 문장") == []


def test_check_facts_ok():
    assert check_facts("매입등록(70-2048) 개선", "70-2048 화면 개선") == []


def test_check_facts_invented():
    assert check_facts("매입등록(70-2099) 개선", "70-2048 화면 개선") == ["규칙1 입력에 없는 숫자: 2099"]


def test_check_facts_ignores_non_digits():
    assert check_facts("개선 개발(계속)", "개선") == []


def test_mask_phone_attached_to_korean():
    assert mask("담당010-1234-5678입니다") == "담당010-****-5678입니다"
    assert mask("연락처:010-1234-5678") == "연락처:010-****-5678"


def test_mask_phone_other_separators():
    assert mask("010 1234 5678") == "010-****-5678"
    assert mask("01012345678") == "010-****-5678"


def test_check_detects_attached_number():
    assert check("연락처:010-1234-5678입니다") != []


# ---- 규칙3: 보고체(존댓말 금지) ----
from guardrails import check_tone  # noqa: E402


def test_check_tone_flags_polite_endings():
    for bad in ["기능을 개선하였습니다.", "오류가 발생합니다", "확인해 주세요", "처리했어요", "담당자입니다.", "· 확인 부탁드립니다"]:
        assert check_tone([bad]) != [], bad


def test_check_tone_accepts_report_style():
    for good in ["거래명세서 스캔본 첨부 기능 추가 개발", "수산부 동일 기능 확대 적용 요청", "    · 상품명 줄바꿈 처리 추가",
                 "오류 메시지 보완 진행(진행중)", "매입원가산입 등록(70-2013) 개선 개발", ""]:
        assert check_tone([good]) == [], good


def test_check_tone_message_names_the_rule_and_the_line():
    out = check_tone(["정상 문장", "했습니다"])
    assert len(out) == 1 and out[0].startswith("규칙3") and "했습니다" in out[0]
