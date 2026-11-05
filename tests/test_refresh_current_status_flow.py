import unittest


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.update_one_calls = []
        self.update_many_calls = []

    def _get(self, doc, dotted_key):
        value = doc
        for part in dotted_key.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def find(self, query=None, projection=None):
        query = query or {}
        results = []
        for doc in self.documents:
            matched = True
            for key, condition in query.items():
                value = self._get(doc, key)
                if isinstance(condition, dict) and "$in" in condition:
                    if value not in condition["$in"]:
                        matched = False
                        break
                elif value != condition:
                    matched = False
                    break
            if matched:
                results.append(doc)
        return results

    def update_many(self, query, update):
        self.update_many_calls.append((query, update))

    def update_one(self, query, update):
        self.update_one_calls.append((query, update))


class ApplyPromotionTest(unittest.TestCase):
    def test_promoted_group_deactivates_old_and_activates_new_with_diff(self):
        from scripts.core.flows.refresh_current_status_flow import apply_promotion

        documents = [
            {"doc_id": "old1", "article_number": "1", "content": "구 조문1"},
            {"doc_id": "old1", "article_number": "2", "content": "구 조문2"},
            {"doc_id": "new1", "article_number": "1", "content": "구 조문1"},
            {"doc_id": "new1", "article_number": "2", "content": "신 조문2"},
            {"doc_id": "new1", "article_number": "3", "content": "신설 조문3"},
        ]
        collection = FakeCollection(documents)
        plan = {
            "new_current": "new1",
            "old_current": "old1",
            "promoted": True,
            "activate_current": "new1",
            "deactivate": ["old1"],
            "keep_upcoming": [],
        }

        apply_promotion(collection, "law-group-1", plan)

        self.assertEqual(len(collection.update_many_calls), 2)

        deactivate_call = collection.update_many_calls[0]
        self.assertEqual(deactivate_call[0], {"doc_id": {"$in": ["old1"]}, "metadata.is_active": True})
        self.assertEqual(deactivate_call[1]["$set"]["metadata.is_active"], False)
        self.assertIn("metadata.updated_at", deactivate_call[1]["$set"])

        activate_call = collection.update_many_calls[1]
        self.assertEqual(activate_call[0], {"doc_id": "new1"})
        self.assertEqual(activate_call[1]["$set"]["metadata.is_active"], True)
        self.assertEqual(activate_call[1]["$set"]["metadata.is_upcoming"], False)

        updated_articles = {call[0]["article_number"] for call in collection.update_one_calls}
        self.assertEqual(updated_articles, {"2", "3"})

    def test_not_promoted_makes_no_writes(self):
        from scripts.core.flows.refresh_current_status_flow import apply_promotion

        collection = FakeCollection([])
        plan = {
            "new_current": "cur1",
            "old_current": "cur1",
            "promoted": False,
            "activate_current": None,
            "deactivate": [],
            "keep_upcoming": [],
        }

        apply_promotion(collection, "law-group-1", plan)

        self.assertEqual(collection.update_many_calls, [])
        self.assertEqual(collection.update_one_calls, [])


class RefreshCollectionEndToEndTest(unittest.TestCase):
    def test_refresh_collection_promotes_group_end_to_end(self):
        from scripts.core.flows.refresh_current_status_flow import refresh_collection

        documents = [
            {"doc_id": "old1", "article_number": "1", "content": "구 조문",
             "metadata": {"is_active": True, "law_id": "G1", "effective": "2026-01-01",
                          "is_upcoming": False, "created_at": "2026-01-01T00:00:00"}},
            {"doc_id": "new1", "article_number": "1", "content": "신 조문",
             "metadata": {"is_active": True, "law_id": "G1", "effective": "2026-07-01",
                          "is_upcoming": True, "created_at": "2026-06-01T00:00:00"}},
        ]
        collection = FakeCollection(documents)

        summary = refresh_collection(collection, "law_id", today="2026-07-21")

        self.assertEqual(summary["groups_checked"], 1)
        self.assertEqual(summary["promoted"], 1)

        activate_calls = [c for c in collection.update_many_calls if c[0] == {"doc_id": "new1"}]
        self.assertEqual(len(activate_calls), 1)
        self.assertTrue(activate_calls[0][1]["$set"]["metadata.is_active"])
        self.assertFalse(activate_calls[0][1]["$set"]["metadata.is_upcoming"])

        updated_articles = {call[0]["article_number"] for call in collection.update_one_calls}
        self.assertEqual(updated_articles, {"1"})


if __name__ == "__main__":
    unittest.main()
