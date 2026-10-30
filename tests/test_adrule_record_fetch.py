"""행정규칙 Open API 조회(동기, 재시도 대기 포함)가 이벤트 루프를 막지 않는다."""
import asyncio
import time
import unittest
from unittest.mock import patch

from scripts.adrule.logic import scraper as adrule_scraper


class FetchAdruleRecordTest(unittest.TestCase):
    def test_slow_lookup_does_not_block_other_tasks(self):
        def slow_fetch(target, doc_id):
            time.sleep(0.3)  # 재시도 대기(5·10초)를 흉내 낸 동기 대기
            return {"record": doc_id}

        async def main():
            ticks = 0

            async def ticker():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.01)
                    ticks += 1

            task = asyncio.create_task(ticker())
            with patch.object(adrule_scraper, "fetch_record", slow_fetch):
                record = await adrule_scraper.fetch_adrule_record("42")
            task.cancel()
            return record, ticks

        record, ticks = asyncio.run(main())
        self.assertEqual(record, {"record": "42"})
        self.assertGreater(ticks, 5)  # 루프가 막혔다면 0~1


if __name__ == "__main__":
    unittest.main()
