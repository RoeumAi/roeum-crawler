import unittest

from scripts.core.database.refresh_promotion import (
    group_active_documents_by_stable_id,
    plan_group_transitions,
)


class GroupActiveDocumentsTest(unittest.TestCase):
    def test_groups_by_stable_id_field_keeping_one_representative_per_doc_id(self):
        documents = [
            {"doc_id": "d1", "metadata": {"law_id": "G1", "effective": "2026-01-01",
                                           "is_upcoming": False, "created_at": "2026-01-01T00:00:00"}},
            {"doc_id": "d1", "metadata": {"law_id": "G1", "effective": "2026-01-01",
                                           "is_upcoming": False, "created_at": "2026-01-01T00:00:00"}},
            {"doc_id": "d2", "metadata": {"law_id": "G1", "effective": "2026-07-01",
                                           "is_upcoming": True, "created_at": "2026-06-01T00:00:00"}},
            {"doc_id": "d3", "metadata": {}},
        ]

        groups = group_active_documents_by_stable_id(documents, "law_id")

        self.assertEqual(set(groups.keys()), {"G1"})
        self.assertEqual(set(groups["G1"].keys()), {"d1", "d2"})
        self.assertEqual(groups["G1"]["d1"]["effective"], "2026-01-01")


class PlanGroupTransitionsTest(unittest.TestCase):
    def test_promotes_when_upcoming_becomes_current(self):
        doc_reps = {
            "old1": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
            "new1": {"effective": "2026-07-01", "is_upcoming": True, "created_at": "2026-06-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertEqual(plan["new_current"], "new1")
        self.assertEqual(plan["old_current"], "old1")
        self.assertTrue(plan["promoted"])
        self.assertEqual(plan["activate_current"], "new1")
        self.assertEqual(plan["deactivate"], ["old1"])
        self.assertEqual(plan["keep_upcoming"], [])

    def test_no_promotion_when_current_already_latest(self):
        doc_reps = {
            "cur1": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertEqual(plan["new_current"], "cur1")
        self.assertEqual(plan["old_current"], "cur1")
        self.assertFalse(plan["promoted"])
        self.assertIsNone(plan["activate_current"])
        self.assertEqual(plan["deactivate"], [])

    def test_keeps_only_latest_duplicate_upcoming_effective_date(self):
        doc_reps = {
            "cur1": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
            "up_old": {"effective": "2026-09-01", "is_upcoming": True, "created_at": "2026-06-01T00:00:00"},
            "up_new": {"effective": "2026-09-01", "is_upcoming": True, "created_at": "2026-07-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertFalse(plan["promoted"])
        self.assertEqual(plan["keep_upcoming"], ["up_new"])
        self.assertEqual(plan["deactivate"], ["up_old"])

    def test_deactivates_superseded_past_versions(self):
        doc_reps = {
            "cur_new": {"effective": "2026-07-01", "is_upcoming": False, "created_at": "2026-06-01T00:00:00"},
            "cur_stale": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertEqual(plan["new_current"], "cur_new")
        self.assertEqual(plan["deactivate"], ["cur_stale"])


if __name__ == "__main__":
    unittest.main()
