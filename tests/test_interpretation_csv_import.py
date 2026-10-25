from scripts.interpretation.csv_import import (
    SKIP_CSV_DUP, SKIP_EXISTING, SKIP_IMPORTED, SKIP_LEGISLATION,
    ExistingIndex, build_documents, make_title, numbers_from_existing,
    parse_date, plan_rows, split_sections, split_sub_title,
)


def row(wm_id, sub_title, content="【질 의】\n연장근로 수당 질문입니다\n【회 시】\n가능합니다"):
    return {"wm_id": wm_id, "sub_title": sub_title, "wm_content2": content}


def test_split_sub_title_and_date_normalization():
    assert split_sub_title("근로기준정책과-42, 2026. 4. 14") == ("근로기준정책과-42", "2026-04-14")
    assert split_sub_title("법제처 26-0284, 2026. 7. 21.") == ("법제처 26-0284", "2026-07-21")
    assert split_sub_title("고용차별개선과-820, 2019-04-17") == ("고용차별개선과-820", "2019-04-17")


def test_impossible_year_is_no_date():
    assert parse_date("1448. 3. 2.") is None
    assert split_sub_title("근기 68207-1, 4758. 1. 2.")[1] is None


def test_sections_map_to_crawled_titles():
    text = "【질 의】\n\n○ 연차휴가는?\n\n\n\n【회 시】\n가능합니다.\n[이유]\n법 제60조"
    sections = split_sections(text)
    assert [s["article_title"] for s in sections] == ["질의요지", "회답", "이유"]
    assert sections[0]["content"] == "○ 연차휴가는?"


def test_bare_and_numbered_headers():
    sections = split_sections("1. 질의요지\n질문\n답변내용\n답\n[질의2]\n두번째")
    assert [s["article_title"] for s in sections] == ["질의요지", "회답", "질의요지 2"]


def test_no_header_is_single_body_and_short_lines_stay_content():
    sections = split_sections("근로자 질문입니다\n확인\n끝")
    assert sections == [{"article_title": "본문", "content": "근로자 질문입니다\n확인\n끝"}]


def test_title_from_first_question_line():
    sections = split_sections("【질의】\n○ 1) 경조사비를 임금에서 공제할 수 있는지 여부\n【회시】\n불가")
    assert make_title(sections) == "경조사비를 임금에서 공제할 수 있는지 여부"
    assert len(make_title([{"article_title": "질의요지", "content": "가" * 300}])) == 81


def test_title_prefers_yeobu_sentence_over_long_background():
    text = "【질의】\n❏근로기준법 제76조의2는 직장 내 괴롭힘을 금지하고 있습니다.\n○ 사용자 친족의 괴롭힘도 과태료 대상인지 여부\n【회시】\n답"
    assert make_title(split_sections(text)) == "사용자 친족의 괴롭힘도 과태료 대상인지 여부"


def test_title_does_not_cut_mid_sentence_at_yeobu():
    text = ("【질의】\n1) 노동절(5.1)에 근무했다면 휴일근로수당을 지급하여야 하는지 여부\n"
            "2) 괴롭힘 여부를 감독관이 조사하는지 문의드립니다.\n【회시】\n답")
    assert make_title(split_sections(text)) == "노동절(5.1)에 근무했다면 휴일근로수당을 지급하여야 하는지 여부"
    only_mid = "【질의】\n감독관이 괴롭힘 여부를 직접 조사하는지 문의드립니다.\n【회시】\n답"
    assert make_title(split_sections(only_mid)) == "감독관이 괴롭힘 여부를 직접 조사하는지 문의드립니다."


def test_nts_document_is_labeled_nts():
    docs = build_documents(row("1", "기준-2026-법규소득-0065, 2026. 6. 18."), "t", "f.csv", "b")
    assert docs[0]["sub_title"].startswith("국세청, ")
    assert docs[0]["metadata"]["cpt_ofi_name"] == "국세청"
    assert "cpt_ofi_code" not in docs[0]["metadata"]


def test_existing_numbers_from_crawled_and_pdf_docs():
    crawled = {"sub_title": "고용노동부, 임금근로시간정책팀-2624, 2006.9.8", "metadata": {}}
    old = {"sub_title": "고용노동부 고관68460-884", "metadata": {}}
    pdf = {"title": "감독 32130-844", "sub_title": "", "metadata": {"source_type": "pdf"}}
    assert list(numbers_from_existing(crawled)) == ["임금근로시간정책팀-2624"]
    assert list(numbers_from_existing(old)) == ["고관68460-884"]
    assert list(numbers_from_existing(pdf)) == ["감독 32130-844"]


def test_plan_skips_existing_legislation_imported_and_csv_duplicates():
    index = ExistingIndex()
    index.add("근로기준정책과-42", "2026-04-14")   # 날짜까지 같으면 이미 있음
    index.add("근로기준정책과-655", "2010-01-01")  # 번호만 같고 날짜 다름 → 다른 문서
    index.add("고관68460-884", None)               # 날짜 모르는 옛 문서 → 번호만으로 일치
    rows = [
        row("1", "근로기준정책과-42, 2026. 4. 14"),
        row("2", "근로기준정책과-655, 2019. 3. 1."),
        row("3", "고관 68460-884, 1995. 1. 1."),
        row("4", "법제처 26-0284, 2026. 7. 21."),
        row("5", "임금정책과-1, 2025. 1. 2."),
        row("6", "임금정책과-1, 2025. 1. 2."),
        row("7", "임금정책과-2, 2025. 1. 3."),
    ]
    plan = plan_rows(rows, index, imported_doc_ids={"csv_interp_7"})
    assert [(r["wm_id"], action, reason) for r, action, reason in plan] == [
        ("1", "skip", SKIP_EXISTING),
        ("2", "insert", None),
        ("3", "skip", SKIP_EXISTING),
        ("4", "skip", SKIP_LEGISLATION),
        ("5", "insert", None),
        ("6", "skip", SKIP_CSV_DUP),
        ("7", "skip", SKIP_IMPORTED),
    ]


def test_build_documents_matches_crawled_shape():
    docs = build_documents(row("83286", "근로기준정책과-42, 2026. 4. 14"), "2026-10-25T10:00:00",
                           "행정해석_260926.csv", "batch-x")
    assert [d["article_number"] for d in docs] == ["1", "2"]
    first = docs[0]
    assert first["doc_id"] == "csv_interp_83286"
    assert first["chunk_id"] == "interpretation:csv_interp_83286:article:1"
    assert first["sub_title"] == "고용노동부, 근로기준정책과-42, 2026.4.14"
    assert first["title"] == "연장근로 수당 질문입니다"
    assert first["doc_type"] == "법령해석"
    assert first["content_hash"] and first["source_version_id"]
    meta = first["metadata"]
    assert meta["effective"] == "2026-04-14"
    assert meta["import_batch"] == "batch-x" and meta["source_type"] == "csv"
    assert meta["is_active"] is True and meta["total_articles"] == 2
