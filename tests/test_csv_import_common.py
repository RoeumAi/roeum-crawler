from scripts.csv_import.common import convert_tables, has_table, iter_rows, tidy


def test_bordered_ascii_table_merges_wrapped_cells():
    text = "\n".join([
        "앞 문단",
        "+----+---------+------+",
        "| 순 | 공사명  | 발주처 |",
        "|    |         | (기관) |",
        "+----+---------+------+",
        "| 1  | 정부    | 총무처 |",
        "|    | 제3청사 |        |",
        "+----+---------+------+",
        "뒤 문단",
    ])
    out = convert_tables(text)
    assert "| 순 | 공사명 | 발주처 (기관) |\n|---|---|---|\n| 1 | 정부 제3청사 | 총무처 |" in out
    assert out.startswith("앞 문단") and out.endswith("뒤 문단")
    assert has_table(out)


def test_pipe_rows_without_border_get_separator():
    out = convert_tables("| 구분 | 금액 |\n| 기본급 | 100 |\n| 수당 | 20 |")
    assert out.strip() == "| 구분 | 금액 |\n|---|---|\n| 기본급 | 100 |\n| 수당 | 20 |"


def test_ragged_grid_drawing_is_fenced_for_monospace():
    text = "+--+--+\n| a | b |\n| c |\n+--+--+"
    assert convert_tables(text).strip() == "```text\n" + text + "\n```"
    assert not has_table(convert_tables(text))


def test_single_pipe_line_is_left_as_is():
    assert convert_tables("| 메모 |") == "| 메모 |"


def test_cell_pipes_are_escaped_and_plain_text_untouched():
    assert convert_tables("문단 | 사이에 | 파이프") == "문단 | 사이에 | 파이프"


def test_tidy_collapses_blank_lines():
    assert tidy("a  \n\n\n\nb\r\n") == "a\n\nb"


def test_iter_rows_streams_large_fields(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text('﻿wm_id,sub_title,wm_content2\n1,s,"' + "가" * 200000 + '"\n', encoding="utf-8")
    rows = list(iter_rows(str(p)))
    assert rows[0]["wm_id"] == "1" and len(rows[0]["wm_content2"]) == 200000
