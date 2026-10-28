"""표가 있는 추출 결과는 Markdown 표로 글을 만든다(탭 정렬 글 대신)."""
import unittest

from scripts.utils.document_blocks import blocks_markdown, table_markdown
from scripts.utils.document_extraction import usable_document

CELLS = [
    {"row": 0, "column": 0, "text": "번호"}, {"row": 0, "column": 1, "text": "검사항목"},
    {"row": 1, "column": 0, "text": "1"}, {"row": 1, "column": 1, "text": "가. 흉부방사선\n나. 결핵균"},
    {"row": 2, "column": 0, "row_span": 1, "column_span": 2, "text": "비고 | 공통"},
]


class DocumentBlocksTest(unittest.TestCase):
    def test_table_markdown_keeps_rows_and_escapes(self):
        self.assertEqual(table_markdown(CELLS).splitlines(), [
            "| 번호 | 검사항목 |",
            "|---|---|",
            "| 1 | 가. 흉부방사선 <br> 나. 결핵균 |",
            "| 비고 \\| 공통 |  |",
        ])

    def test_blocks_keep_order(self):
        text = blocks_markdown([{"kind": "text", "text": "[별표 1] 제목"}, {"kind": "table", "cells": CELLS}])
        self.assertTrue(text.startswith("[별표 1] 제목\n\n| 번호 | 검사항목 |"))

    def test_usable_document_uses_markdown_tables(self):
        extracted = {"is_success": True, "extraction_status": "complete", "full_text": "x",
                     "structured_text": "[별표 1] 제목\n\n번호\t검사항목\n1\t가. 흉부방사선",
                     "document_blocks": [{"kind": "text", "text": "[별표 1] 제목"}, {"kind": "table", "cells": CELLS}],
                     "extraction_metadata": {"paragraph_projection": {"status": "complete"}}}
        self.assertIn("| 1 | 가. 흉부방사선 <br> 나. 결핵균 |", usable_document(extracted)["structured_text"])

    def test_without_tables_text_is_unchanged(self):
        extracted = {"is_success": True, "extraction_status": "complete", "full_text": "x",
                     "structured_text": "본문", "document_blocks": [{"kind": "text", "text": "본문"}],
                     "extraction_metadata": {"paragraph_projection": {"status": "complete"}}}
        self.assertEqual(usable_document(extracted)["structured_text"], "본문")


if __name__ == "__main__":
    unittest.main()
