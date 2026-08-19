"""판정문 본문에서 자기 사건번호를 추출하는 규칙을 고정한다.

본문에는 초심(지노위) 번호 등 다른 사건의 번호가 함께 인용되므로,
'사 건' 라벨(판정문 헤더) 우선 규칙이 깨지면 엉뚱한 번호가 라벨에 박힌다.
"""

from scripts.utils.reference_sub_title import extract_nlrc_case_number_from_body


HEADER_BODY = (
    "판정사항: 해고의 징계가 정당하다고 판정한 사례\n\n"
    "[첨부파일 전문]\n중앙노동위원회 판정서\n"
    "사    건 중앙2014부해781 부당해고 구제 재심신청\n"
    "근로자 김OO\n"
    "이 사건 근로자는 초심 서울2014부해321 사건에서 구제를 신청하였다."
)


def test_prefers_case_number_after_the_case_label():
    assert extract_nlrc_case_number_from_body(HEADER_BODY) == "중앙2014부해781"


def test_label_wins_even_when_another_number_appears_first():
    body = (
        "초심 서울2014부해321 판정에 불복하여 재심을 신청한 사건이다.\n"
        "사    건 중앙2015부해873 부당해고 구제 재심신청"
    )
    assert extract_nlrc_case_number_from_body(body) == "중앙2015부해873"


def test_prose_sagun_does_not_trigger_the_label_rule():
    """'이 사건 근로자'(붙여 쓴 사건)는 라벨이 아니다 — 라벨 없으면 첫 번호."""
    body = (
        "이 사건 근로자는 부당해고를 다투었다. "
        "초심 서울2016부해100 판정이 유지되었다."
    )
    assert extract_nlrc_case_number_from_body(body) == "서울2016부해100"


def test_returns_empty_when_no_case_number_exists():
    assert extract_nlrc_case_number_from_body("판정사항: 근로자성이 부정된 사례") == ""
    assert extract_nlrc_case_number_from_body("") == ""


def test_whitespace_inside_number_is_collapsed():
    body = "사    건 중앙 2019 부해 321 부당해고 구제 재심신청"
    assert extract_nlrc_case_number_from_body(body) == "중앙2019부해321"
