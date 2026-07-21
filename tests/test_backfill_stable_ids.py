import unittest

from scripts.migrations.backfill_stable_ids import plan_backfill


class PlanBackfillTest(unittest.TestCase):
    def test_splits_successful_lookups_from_failures(self):
        def fake_lookup(doc_id):
            return {"d1": "S1", "d2": "S2"}.get(doc_id)

        updates, failures = plan_backfill(["d1", "d2", "d3"], fake_lookup)

        self.assertEqual(updates, [("d1", "S1"), ("d2", "S2")])
        self.assertEqual(failures, ["d3"])

    def test_empty_doc_ids_produces_empty_results(self):
        updates, failures = plan_backfill([], lambda doc_id: "unused")

        self.assertEqual(updates, [])
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
