from scripts.csv_import.case import (
    SKIP_COURT, SKIP_CSV_DUP, SKIP_EXISTING, Planner, build_documents,
    case_keys, case_title, split_sections,
)

FULL = "\n".join([
    "대구지방법원포항지원", "", "판결", "", "사건", "", "2024가단380 임금 등 청구의 소", "",
    "원고(선정당사자)", "", "A", "", "피고", "", "사회복지법인 B", "", "판결선고", "", "2026. 9. 15.", "",
    "주문", "", "1. 피고는 원고에게 돈을 지급하라.", "", "청구취지", "", "주문과 같다.", "",
    "이유", "", "1. 기초사실", "가. 관련 법리", "", "별지", "", "목록",
])
SUMMARY = "\n".join([
    "【요 지】 연차휴가를 사용할 권리는 다음 날 발생한다.", "",
    "◈ 대법원 2026.9.3. 선고 2024다206043 판결 [퇴직금 등] ◈", "",
    "* 사 건 : 2024다206043 퇴직금 등", "* 원고, 상고인 : 원고 1",
    "【주 문】 원심판결을 파기한다.", "【이 유】 상고이유를 판단한다.",
])


def test_full_text_sections_keep_party_info_together():
    sections = split_sections(FULL)
    assert [s["title"] for s in sections] == ["사건정보", "주문", "청구취지", "이유", "별지"]
    assert "사회복지법인 B" in sections[0]["text"] and "2024가단380" in sections[0]["text"]
    assert sections[3]["text"].startswith("1. 기초사실\n가. 관련 법리")


def test_summary_style_sections_and_inline_bracket_content():
    sections = split_sections(SUMMARY)
    assert [s["title"] for s in sections] == ["판결요지", "사건정보", "주문", "이유"]
    assert sections[0]["text"] == "연차휴가를 사용할 권리는 다음 날 발생한다."
    assert sections[2]["text"] == "원심판결을 파기한다."


def test_case_title_is_case_name_like_crawled():
    assert case_title(FULL, "x") == "임금 등 청구의 소"
    assert case_title(SUMMARY, "x") == "퇴직금 등"
    assert case_title("본문만", "2024가단1") == "2024가단1"
    assert case_title("사건\n\n2024가합10658(본소) 임금\n", "x") == "임금"


def test_case_keys_pair_court_with_number():
    assert case_keys("대구지방법원 포항지원 2026. 9. 15. 선고 2024가단380 판결") == {"포항지원2024가단380"}
    assert case_keys("[대법원 2026. 3. 12. 2026두30056 기각]\n [수원지방법원 2025. 1. 15. 2024구합60672 일부 패소]") == {
        "대법원2026두30056", "수원지방법원2024구합60672"}


def test_planner_court_phase_existing_and_duplicates():
    planner = Planner({"대법원2024다1"}, {"csv_case_9"}, court_pattern="대법원|고등법원|행정법원")
    rows = [
        {"wm_id": "1", "sub_title": "대법원 2026. 1. 2. 선고 2024다1 판결", "wm_content2": "x"},
        {"wm_id": "2", "sub_title": "서울중앙지방법원 2026. 1. 2. 선고 2024가합1 판결", "wm_content2": "x"},
        {"wm_id": "3", "sub_title": "서울고등법원 2026. 1. 2. 선고 2024나1 판결", "wm_content2": "x"},
        {"wm_id": "4", "sub_title": "서울고등법원 2026. 1. 2. 선고 2024나1 판결", "wm_content2": "x"},
        {"wm_id": "9", "sub_title": "서울행정법원 2026. 1. 2. 선고 2024구합9 판결", "wm_content2": "x"},
    ]
    assert [planner.decide(r) for r in rows] == [
        ("skip", SKIP_EXISTING), ("skip", SKIP_COURT), ("insert", None), ("skip", SKIP_CSV_DUP), ("update", None)]


def test_build_documents_for_case_chunk_saver():
    doc = build_documents({"wm_id": "124077", "sub_title": "대구지방법원 포항지원 2026. 9. 15. 선고 2024가단380 판결",
                           "wm_content2": FULL}, "t", "판례.csv", "b")
    assert doc["doc_id"] == "csv_case_124077"
    assert doc["title"] == "임금 등 청구의 소" and doc["effective"] == "2026-09-15"
    meta = doc["chunks"][0]["metadata"]
    assert meta["source_type"] == "csv" and meta["import_batch"] == "b"
    assert meta["court"] == "대구지방법원 포항지원" and meta["case_number"] == "2024가단380"
    assert meta["chapter"] == "임금 등 청구의 소"
