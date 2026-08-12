import unittest


class FakeCollection:
    """update_summary 동기화 검증용 최소 MongoDB 컬렉션 대역."""

    def __init__(self, documents):
        self.documents = documents

    def _get(self, doc, dotted_key):
        value = doc
        for part in dotted_key.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def _matches(self, doc, query):
        for key, condition in (query or {}).items():
            value = self._get(doc, key)
            if isinstance(condition, dict):
                if "$in" in condition and value not in condition["$in"]:
                    return False
                if "$nin" in condition and value in condition["$nin"]:
                    return False
                if "$exists" in condition:
                    exists = value is not None
                    if exists != condition["$exists"]:
                        return False
            elif value != condition:
                return False
        return True

    def find(self, query=None, projection=None):
        return [doc for doc in self.documents if self._matches(doc, query)]

    def _apply(self, doc, update):
        for dotted_key, value in update.get("$set", {}).items():
            target = doc
            parts = dotted_key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        for dotted_key in update.get("$unset", {}):
            target = doc
            parts = dotted_key.split(".")
            for part in parts[:-1]:
                if not isinstance(target, dict) or part not in target:
                    target = None
                    break
                target = target[part]
            if isinstance(target, dict):
                target.pop(parts[-1], None)

    def update_one(self, query, update):
        for doc in self.documents:
            if self._matches(doc, query):
                self._apply(doc, update)
                return

    def update_many(self, query, update):
        for doc in self.documents:
            if self._matches(doc, query):
                self._apply(doc, update)


def make_chunk(doc_id, article_number, content, *, law_id="G1", effective="",
               is_upcoming=False, is_active=True, update_summary=None):
    metadata = {
        "law_id": law_id,
        "effective": effective,
        "is_upcoming": is_upcoming,
        "is_active": is_active,
        "created_at": f"{effective}T00:00:00" if effective else "",
    }
    if update_summary is not None:
        metadata["update_summary"] = update_summary
    return {
        "doc_id": doc_id,
        "article_number": article_number,
        "content": content,
        "metadata": metadata,
    }


class PlanUpcomingDiffPairsTest(unittest.TestCase):
    def _plan(self, doc_reps):
        from scripts.core.database.upcoming_diff_sync import plan_upcoming_diff_pairs
        return plan_upcoming_diff_pairs(doc_reps)

    def test_single_upcoming_pairs_with_current(self):
        pairs = self._plan({
            "cur": {"effective": "2025-10-01", "is_upcoming": False, "created_at": ""},
            "up1": {"effective": "2026-08-20", "is_upcoming": True, "created_at": ""},
        })
        self.assertEqual(pairs, [("cur", "up1")])

    def test_upcoming_chain_pairs_consecutively(self):
        pairs = self._plan({
            "up2": {"effective": "2026-09-18", "is_upcoming": True, "created_at": ""},
            "cur": {"effective": "2025-10-01", "is_upcoming": False, "created_at": ""},
            "up3": {"effective": "2026-11-27", "is_upcoming": True, "created_at": ""},
            "up1": {"effective": "2026-08-20", "is_upcoming": True, "created_at": ""},
        })
        self.assertEqual(pairs, [("cur", "up1"), ("up1", "up2"), ("up2", "up3")])

    def test_no_upcoming_returns_empty(self):
        pairs = self._plan({
            "cur": {"effective": "2025-10-01", "is_upcoming": False, "created_at": ""},
        })
        self.assertEqual(pairs, [])

    def test_upcoming_without_predecessor_is_skipped(self):
        # 제정 법령: 첫 버전이 시행예정이면 비교 대상이 없으므로 diff 없음
        pairs = self._plan({
            "up1": {"effective": "2026-08-20", "is_upcoming": True, "created_at": ""},
        })
        self.assertEqual(pairs, [])


class SyncGroupUpcomingDiffsTest(unittest.TestCase):
    def test_writes_update_summary_on_changed_articles_only(self):
        from scripts.core.database.upcoming_diff_sync import sync_group_upcoming_diffs

        collection = FakeCollection([
            make_chunk("cur", "1", "구 조문1", effective="2025-10-01"),
            make_chunk("cur", "2", "구 조문2", effective="2025-10-01"),
            make_chunk("up1", "1", "구 조문1", effective="2026-08-20", is_upcoming=True),
            make_chunk("up1", "2", "신 조문2", effective="2026-08-20", is_upcoming=True),
            make_chunk("up1", "3", "신설 조문3", effective="2026-08-20", is_upcoming=True),
        ])
        doc_reps = {
            "cur": {"effective": "2025-10-01", "is_upcoming": False, "created_at": ""},
            "up1": {"effective": "2026-08-20", "is_upcoming": True, "created_at": ""},
        }

        diff_count = sync_group_upcoming_diffs(collection, doc_reps)

        self.assertEqual(diff_count, 2)
        by_key = {(d["doc_id"], d["article_number"]): d for d in collection.documents}

        summary_2 = by_key[("up1", "2")]["metadata"]["update_summary"]
        self.assertEqual(summary_2["change_type"], "개정")
        self.assertEqual(summary_2["old_content"], "구 조문2")
        self.assertEqual(summary_2["new_content"], "신 조문2")

        summary_3 = by_key[("up1", "3")]["metadata"]["update_summary"]
        self.assertEqual(summary_3["change_type"], "신설")

        self.assertNotIn("update_summary", by_key[("up1", "1")]["metadata"])
        self.assertNotIn("update_summary", by_key[("cur", "1")]["metadata"])

    def test_removes_stale_update_summary_when_no_longer_changed(self):
        from scripts.core.database.upcoming_diff_sync import sync_group_upcoming_diffs

        stale = {"article_number": "1", "change_type": "개정",
                 "old_content": "옛날", "new_content": "구 조문1"}
        collection = FakeCollection([
            make_chunk("cur", "1", "구 조문1", effective="2025-10-01"),
            make_chunk("up1", "1", "구 조문1", effective="2026-08-20",
                       is_upcoming=True, update_summary=stale),
        ])
        doc_reps = {
            "cur": {"effective": "2025-10-01", "is_upcoming": False, "created_at": ""},
            "up1": {"effective": "2026-08-20", "is_upcoming": True, "created_at": ""},
        }

        diff_count = sync_group_upcoming_diffs(collection, doc_reps)

        self.assertEqual(diff_count, 0)
        up1 = collection.find({"doc_id": "up1"})[0]
        self.assertNotIn("update_summary", up1["metadata"])

    def test_chain_diffs_compare_against_immediate_predecessor(self):
        from scripts.core.database.upcoming_diff_sync import sync_group_upcoming_diffs

        collection = FakeCollection([
            make_chunk("cur", "1", "버전A", effective="2025-10-01"),
            make_chunk("up1", "1", "버전B", effective="2026-08-20", is_upcoming=True),
            make_chunk("up2", "1", "버전B", effective="2026-09-18", is_upcoming=True),
        ])
        doc_reps = {
            "cur": {"effective": "2025-10-01", "is_upcoming": False, "created_at": ""},
            "up1": {"effective": "2026-08-20", "is_upcoming": True, "created_at": ""},
            "up2": {"effective": "2026-09-18", "is_upcoming": True, "created_at": ""},
        }

        sync_group_upcoming_diffs(collection, doc_reps)

        by_key = {(d["doc_id"], d["article_number"]): d for d in collection.documents}
        # up1은 cur 대비 개정, up2는 up1과 동일하므로 diff 없음
        self.assertEqual(by_key[("up1", "1")]["metadata"]["update_summary"]["change_type"], "개정")
        self.assertNotIn("update_summary", by_key[("up2", "1")]["metadata"])


class SyncCollectionUpcomingDiffsTest(unittest.TestCase):
    def test_syncs_all_groups_and_reports_summary(self):
        from scripts.core.database.upcoming_diff_sync import sync_collection_upcoming_diffs

        collection = FakeCollection([
            make_chunk("g1-cur", "1", "구", law_id="G1", effective="2025-10-01"),
            make_chunk("g1-up", "1", "신", law_id="G1", effective="2026-08-20", is_upcoming=True),
            make_chunk("g2-cur", "1", "그대로", law_id="G2", effective="2025-01-01"),
            # 비활성 문서는 무시되어야 한다
            make_chunk("g1-old", "1", "아주 구", law_id="G1", effective="2024-01-01", is_active=False),
        ])

        summary = sync_collection_upcoming_diffs(collection, "law_id")

        self.assertEqual(summary["groups_checked"], 2)
        self.assertEqual(summary["groups_with_upcoming"], 1)
        self.assertEqual(summary["diff_articles"], 1)

        g1_up = collection.find({"doc_id": "g1-up"})[0]
        self.assertEqual(g1_up["metadata"]["update_summary"]["change_type"], "개정")
        self.assertEqual(g1_up["metadata"]["update_summary"]["old_content"], "구")


class RefreshCollectionSyncsUpcomingDiffsTest(unittest.TestCase):
    def test_daily_refresh_writes_diffs_for_upcoming_versions_without_promotion(self):
        # run_daily.sh 경로: 승격이 없어도 시행예정 버전의 신구대조표가 생성되어야 한다
        from scripts.core.flows.refresh_current_status_flow import refresh_collection

        collection = FakeCollection([
            make_chunk("cur", "1", "구 조문", effective="2025-10-01"),
            make_chunk("up1", "1", "신 조문", effective="2026-08-20", is_upcoming=True),
        ])

        summary = refresh_collection(collection, "law_id", today="2026-08-12")

        self.assertEqual(summary["promoted"], 0)
        self.assertEqual(summary["diff_articles"], 1)
        up1 = collection.find({"doc_id": "up1"})[0]
        self.assertEqual(up1["metadata"]["update_summary"]["change_type"], "개정")
        self.assertEqual(up1["metadata"]["update_summary"]["old_content"], "구 조문")


class SyncScraperUpcomingDiffsTest(unittest.TestCase):
    def test_law_scraper_syncs_with_law_id(self):
        from scripts.core.database.upcoming_diff_sync import sync_scraper_upcoming_diffs

        collection = FakeCollection([
            make_chunk("cur", "1", "구", law_id="G1", effective="2025-10-01"),
            make_chunk("up1", "1", "신", law_id="G1", effective="2026-08-20", is_upcoming=True),
        ])
        db = {"law": collection}

        result = sync_scraper_upcoming_diffs(db, "law")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["diff_articles"], 1)
        up1 = collection.find({"doc_id": "up1"})[0]
        self.assertEqual(up1["metadata"]["update_summary"]["change_type"], "개정")

    def test_unsupported_scraper_type_is_skipped(self):
        from scripts.core.database.upcoming_diff_sync import sync_scraper_upcoming_diffs

        result = sync_scraper_upcoming_diffs({}, "case")

        self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
