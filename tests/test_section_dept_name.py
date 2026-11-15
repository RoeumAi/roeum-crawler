"""헌재·법제처해석례·행정심판 저장 행의 metadata.dept_name — 기존 행과 같은 값(법률 DB 내보내기의 기관명 대체값)."""
import unittest
from unittest.mock import patch

from scripts.admin_decc.logic import scraper as decc
from scripts.constitutional_decc.logic import scraper as detc
from scripts.legislation_expc.logic import scraper as expc
from tests.fakes import FakeDB

CHUNKS = [{"chunk_id": "doc:1:summary", "doc_id": "1", "title": "주문", "text": "본문", "metadata": {"chapter": "t"},
           "source_url": "u"}]


def saved_dept(module, subtitle):
    db = FakeDB()
    with patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db):
        module.save_to_mongodb("1", "제목", subtitle, "u", [dict(c) for c in CHUNKS])
    return {row["metadata"].get("dept_name") for row in db[module.SPEC.collection].docs}


class SectionDeptNameTest(unittest.TestCase):
    def test_constant_and_subtitle_based_values(self):
        self.assertEqual(saved_dept(detc, "전원재판부 2022헌바79, 2026. 8. 27."), {"헌법재판소"})
        self.assertEqual(saved_dept(expc, "법제처 26-0209, 2026. 5. 26., 민원인"), {"법제처"})
        self.assertEqual(saved_dept(decc, "국민권익위원회 2000-01715, 2000. 5. 8., 인용"), {"국민권익위원회"})

    def test_empty_subtitle_leaves_decc_dept_unset(self):
        self.assertEqual(saved_dept(decc, ""), {None})


if __name__ == "__main__":
    unittest.main()
