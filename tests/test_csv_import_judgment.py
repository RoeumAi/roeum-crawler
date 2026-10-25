from scripts.csv_import.judgment import SKIP_EXISTING, Planner, build_documents, case_number


def test_case_number_with_region():
    assert case_number("지방노동위원회(서울) 2026. 7. 14. 판정 2026부해1628") == "서울2026부해1628"
    assert case_number("중앙노동위원회 2026. 6. 15. 결정 중앙2026교섭17") == "중앙2026교섭17"
    assert case_number("노동위원회(중앙) 2025. 1. 2. 판정 2024부해100") == "중앙2024부해100"
    assert case_number("지방노동위원회() 2020. 1. 2. 판정 2019부해5",
                       "◈ 경기지방노동위원회 경기2019부해5 판정서 ◈") == "경기2019부해5"


def test_planner_and_document_shape():
    row = {"wm_id": "83287", "sub_title": "지방노동위원회(서울) 2026. 7. 14. 판정 2026부해1628",
           "wm_content2": "【요 지】 해고는 부당함.\n\n\n\n| 구분 | 금액 |\n| 임금 | 100 |"}
    assert Planner({"서울2026부해1628"}, set()).decide(row) == ("skip", SKIP_EXISTING)
    assert Planner(set(), set()).decide(row) == ("insert", None)
    doc = build_documents(row, "t", "f.csv", "b")
    assert doc["doc_id"] == "csv_judgment_83287" and doc["chunk_id"] == "judgment:csv_judgment_83287"
    assert doc["title"] == "서울2026부해1628" and doc["doc_type"] == "주요판정사례"
    assert doc["metadata"]["effective"] == "2026-07-14" and doc["metadata"]["source_type"] == "csv"
    assert "| 구분 | 금액 |\n|---|---|\n| 임금 | 100 |" in doc["content"]
    assert doc["content_hash"]
