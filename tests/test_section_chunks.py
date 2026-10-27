"""섹션 청크 저장 공용 함수: 정상 저장, 토큰 분할 실패는 실패로 센다."""
import unittest
from unittest.mock import MagicMock, patch

from scripts.core.database import section_chunks


class SaveSectionChunksTest(unittest.TestCase):
    def save(self, split=None):
        collection = MagicMock()
        db = {"admin_decc": collection}
        chunks = [{"title": "주문", "text": "기각한다."}, {"title": "이유", "text": "이유 본문"}]
        patches = [patch("scripts.core.database.mongo_client.get_mongo_db", return_value=db)]
        if split:
            patches.append(patch.object(section_chunks, "split_section", side_effect=split))
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return section_chunks.save_section_chunks("admin_decc", "100", "제목", "부제", "https://x", chunks), collection

    def test_saves_each_section_with_token_count(self):
        ok, collection = self.save(split=lambda text: [(text, 3)])
        self.assertTrue(ok)
        self.assertEqual(collection.update_one.call_count, 2)
        filt, update = collection.update_one.call_args_list[0].args
        self.assertEqual(filt, {"doc_id": "100", "doc_type": "주문", "chunk_seq": 1})
        self.assertEqual(update["$set"]["metadata.token_count"], 3)

    def test_tokenizer_failure_is_a_failed_save(self):
        def split(text):
            if text == "이유 본문":
                raise RuntimeError("tokenizer missing")
            return [(text, 3)]
        ok, collection = self.save(split=split)
        self.assertFalse(ok)  # 나누지 못한 섹션을 조용히 통째로 넣지 않는다
        self.assertEqual(collection.update_one.call_count, 1)

    def test_long_section_is_split(self):
        with patch("scripts.utils.tokenizer_utils.count_tokens", side_effect=lambda t: 9000 if t == "long" else 10), \
             patch("scripts.utils.tokenizer_utils.chunk_text_by_tokens", return_value=["a", "b"]):
            self.assertEqual(section_chunks.split_section("long"), [("a", 10), ("b", 10)])


if __name__ == "__main__":
    unittest.main()
