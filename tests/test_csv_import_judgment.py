from scripts.csv_import.judgment import SKIP_EXISTING, Planner, build_documents, case_number


def test_case_number_with_region():
    assert case_number("지방노동위원회(서울) 2026. 7. 14. 판정 2026부해1628") == "서울2026부해1628"
    assert case_number("중앙노동위원회 2026. 6. 15. 결정 중앙2026교섭17") == "중앙2026교섭17"
    assert case_number("노동위원회(중앙) 2025. 1. 2. 판정 2024부해100") == "중앙2024부해100"
    assert case_number("지방노동위원회() 2020. 1. 2. 판정 2019부해5",
                       "◈ 경기지방노동위원회 경기2019부해5 판정서 ◈") == "경기2019부해5"


def test_old_subtitle_form_uses_first_number():
    assert case_number("사건번호 : 2000부해658,부노142 ,  선고일자 : 2001-03-13") == "2000부해658"
    assert case_number("사건번호 : 재결 2002-11,  선고일자 : 2002-05-06") == "재결 2002-11"
    assert case_number("사건번호 : 국행심 00-4559,  선고일자 : 2000-10-09") == "국행심 00-4559"
    assert case_number("사건번호 : 중노위,  선고일자 : 1993-05-04") == "중노위 1993-05-04"
    doc = build_documents({"wm_id": "1", "sub_title": "사건번호 : 재결 2002-11,  선고일자 : 2002-05-06",
                           "wm_content2": "본문"}, "t", "f", "b")
    assert doc["metadata"]["effective"] == "2002-05-06" and doc["metadata"]["committee"] == ""


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
