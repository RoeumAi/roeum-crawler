import unittest
from unittest.mock import MagicMock

from scripts.core.database.refresh_promotion import (
    group_active_documents_by_stable_id,
    plan_group_transitions,
    version_selector,
)
from scripts.core.database.unified_repository import _version_filter
from scripts.migrations.law_current_version import find_versions_to_restore


def row(doc_id, effective, *, active, upcoming=False):
    return {"doc_id": doc_id, "metadata": {"law_id": "L", "effective": effective, "is_active": active,
                                           "is_upcoming": upcoming, "created_at": "2026-07-09"}}


class VersionFilterTest(unittest.TestCase):
    def test_current_and_upcoming_are_looked_up_apart(self):
        self.assertEqual(_version_filter(None), {})
        self.assertEqual(_version_filter(True), {"metadata.is_upcoming": True})
        self.assertEqual(_version_filter(False), {"metadata.is_upcoming": {"$ne": True}})


class SameDocIdPromotionTest(unittest.TestCase):
    """산업안전보건법 287805: 현행 2026-07-07·예정 2027-01-08 이 doc_id 를 함께 쓴다."""

    docs = [
        row("287805", "2026-07-07", active=True),
        row("287805", "2027-01-08", active=True, upcoming=True),
        row("283449", "2027-01-01", active=True, upcoming=True),
    ]

    def test_only_shared_doc_ids_get_effective_in_key(self):
        group = group_active_documents_by_stable_id(self.docs, "law_id")["L"]
        self.assertEqual(set(group), {"287805@2026-07-07", "287805@2027-01-08", "283449"})
        self.assertEqual(version_selector("283449"), {"doc_id": "283449"})
        self.assertEqual(version_selector("287805@2027-01-08"),
                         {"doc_id": "287805", "metadata.effective": "2027-01-08"})

    def test_nothing_changes_while_current_stays_current(self):
        group = group_active_documents_by_stable_id(self.docs, "law_id")["L"]
        plan = plan_group_transitions(group, today="2026-11-05")
        self.assertFalse(plan["promoted"])
        self.assertEqual(plan["deactivate"], [])

    def test_promotion_retires_only_old_version_not_shared_upcoming(self):
        group = group_active_documents_by_stable_id(self.docs, "law_id")["L"]
        plan = plan_group_transitions(group, today="2027-01-01")
        self.assertEqual(plan["activate_current"], "283449")
        self.assertEqual(plan["deactivate"], ["287805@2026-07-07"])
        self.assertIn("287805@2027-01-08", plan["keep_upcoming"])


class FindVersionsToRestoreTest(unittest.TestCase):
    today = "2026-11-05"

    def test_restores_latest_retired_current_sharing_doc_id_with_upcoming(self):
        rows = [row("287805", "2026-07-07", active=False),
                row("100", "2025-01-01", active=False),
                row("287805", "2027-01-08", active=True, upcoming=True)]
        self.assertEqual(find_versions_to_restore(rows, "law_id", self.today),
                         [{"group_id": "L", "doc_id": "287805", "effective": "2026-07-07"}])

    def test_skips_group_that_has_active_current(self):
        rows = [row("290781", "2026-10-02", active=True), row("283457", "2026-08-20", active=False),
                row("285279", "2027-01-01", active=True, upcoming=True)]
        self.assertEqual(find_versions_to_restore(rows, "law_id", self.today), [])

    def test_skips_when_latest_retired_does_not_share_doc_id(self):
        # 새 법령·폐지 등: 일부러 꺼진 옛 버전을 되살리지 않는다.
        rows = [row("100", "2025-01-01", active=False), row("200", "2027-01-01", active=True, upcoming=True)]
        self.assertEqual(find_versions_to_restore(rows, "law_id", self.today), [])

    def test_skips_retired_version_not_yet_effective(self):
        rows = [row("300", "2027-05-01", active=False), row("300", "2027-06-01", active=True, upcoming=True)]
        self.assertEqual(find_versions_to_restore(rows, "law_id", self.today), [])


class RepositorySaveKeepsCurrentTest(unittest.TestCase):
    def test_upcoming_lookup_excludes_current_row(self):
        from scripts.core.database.unified_repository import UnifiedDocumentRepository

        repo = UnifiedDocumentRepository.__new__(UnifiedDocumentRepository)
        repo.collection = MagicMock()
        repo.find_by_article("287805", "1", active_only=True, is_upcoming=True)
        query = repo.collection.find_one.call_args.args[0]
        self.assertEqual(query["metadata.is_upcoming"], True)
        self.assertEqual(query["doc_id"], "287805")


if __name__ == "__main__":
    unittest.main()
