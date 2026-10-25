from scripts.csv_import.legislation import (
    SKIP_CSV_DUP, SKIP_EXISTING, SKIP_NOT_LEGISLATION, Planner, build_documents, number_of,
)

ROW = {"wm_id": "50001", "sub_title": "법제처 26-0284, 2026. 7. 21.",
       "wm_content2": "【질의요지】\n\n안전보건관리책임자인지?\n\n【회답】\n\n해당합니다.\n\n【이유】\n\n법 제15조에 따라"}


def test_number_and_planner():
    assert number_of("법제처 24-0605, 2024. 9. 13., 민원인") == "24-0605"
    assert number_of("법제처-24-0745, 2024. 11. 8.") == "24-0745"
    planner = Planner({"24-0605"}, set())
    rows = [
        {"wm_id": "1", "sub_title": "근로기준정책과-42, 2026. 4. 14", "wm_content2": "x"},
        {"wm_id": "2", "sub_title": "법제처 24-0605, 2024. 9. 13.", "wm_content2": "x"},
        ROW,
        dict(ROW, wm_id="50002"),
    ]
    assert [planner.decide(r) for r in rows] == [
        ("skip", SKIP_NOT_LEGISLATION), ("skip", SKIP_EXISTING), ("insert", None), ("skip", SKIP_CSV_DUP)]


def test_documents_use_crawled_section_names_and_number_title():
    doc = build_documents(ROW, "t", "행정해석_260926.csv", "b")
    assert doc["doc_id"] == "csv_legis_50001"
    assert doc["title"] == "법제처 26-0284" and doc["sub_title"] == "법제처 26-0284, 2026. 7. 21."
    assert [c["title"] for c in doc["chunks"]] == ["질의요지", "회답", "이유"]
    meta = doc["chunks"][0]["metadata"]
    assert meta["dept_name"] == "법제처" and meta["source_type"] == "csv" and meta["effective"] == "2026-07-21"
    assert meta["chapter"] == "법제처 26-0284"


def test_old_number_title_drops_date():
    doc = build_documents({"wm_id": "30207", "sub_title": "법제처 기획 02102-23, 1988. 8. 26.",
                           "wm_content2": "【질의요지】\n질의\n【회답】\n답"}, "t", "f", "b")
    assert doc["title"] == "법제처 기획 02102-23"
    assert doc["chunks"][0]["metadata"]["effective"] == "1988-08-26"
