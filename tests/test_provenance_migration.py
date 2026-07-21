import unittest

from scripts.core.database.provenance_migration import (
    audit_provenance,
    build_provenance_update,
)
from scripts.core.database.source_versioning import sha256_content
from scripts.migrations.backfill_source_provenance import migrate_collection


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.updates = []
        self.indexes = []

    def find(self, *_args, **_kwargs):
        return list(self.documents)

    def update_one(self, query, update):
        self.updates.append((query, update))

    def create_index(self, fields, **kwargs):
        self.indexes.append((fields, kwargs))


class ProvenanceMigrationTest(unittest.TestCase):
    def test_builds_only_missing_provenance_fields(self):
        document = {
            "doc_id": "108467",
            "chunk_id": "law:108467:article:23",
            "content": "원문",
            "metadata": {},
        }

        update = build_provenance_update(document, "law")
        content_hash = sha256_content("원문")

        self.assertEqual(update["content_hash"], content_hash)
        self.assertEqual(
            update["source_version_id"],
            f"law:law:108467:article:23:{content_hash}",
        )
        self.assertNotIn("metadata.is_active", update)

    def test_preserves_explicit_inactive_history(self):
        document = {
            "chunk_id": "law:108467:article:23",
            "content": "과거 원문",
            "metadata": {"is_active": False},
        }

        update = build_provenance_update(document, "law")

        self.assertNotIn("metadata.is_active", update)

    def test_skips_invalid_documents_instead_of_hashing_empty_content(self):
        document = {"_id": "mongo-1", "chunk_id": "case:123", "content": ""}

        self.assertEqual(build_provenance_update(document, "case"), {})
        self.assertEqual(audit_provenance(document, "case")["provenance"], "invalid")

    def test_skips_documents_without_a_stable_chunk_identity(self):
        document = {"_id": "mongo-1", "content": "원문"}

        self.assertEqual(build_provenance_update(document, "case"), {})
        self.assertEqual(audit_provenance(document, "case")["provenance"], "invalid")

    def test_audit_reports_hash_and_version_mismatches(self):
        document = {
            "chunk_id": "case:123:section:1",
            "content": "현재 원문",
            "content_hash": "wrong",
            "source_version_id": "wrong-version",
            "metadata": {"is_active": True},
        }

        audit = audit_provenance(document, "case")

        self.assertEqual(
            audit,
            {
                "provenance": "valid",
                "content_hash": "mismatch",
                "source_version_id": "mismatch",
                "active_state": "present",
            },
        )

    def test_dry_run_reports_updates_without_mutating_collection(self):
        collection = FakeCollection(
            [
                {
                    "_id": "mongo-1",
                    "chunk_id": "case:123:section:1",
                    "content": "원문",
                    "metadata": {},
                }
            ]
        )

        stats = migrate_collection(collection, "case", apply=False)

        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(stats["would_update"], 1)
        self.assertEqual(collection.updates, [])
        self.assertEqual(collection.indexes, [])

    def test_apply_updates_documents_and_creates_lookup_indexes(self):
        collection = FakeCollection(
            [
                {
                    "_id": "mongo-1",
                    "chunk_id": "case:123:section:1",
                    "content": "원문",
                    "metadata": {},
                }
            ]
        )

        stats = migrate_collection(collection, "case", apply=True)

        self.assertEqual(stats["updated"], 1)
        self.assertEqual(len(collection.updates), 1)
        self.assertEqual(
            [kwargs["name"] for _, kwargs in collection.indexes],
            ["idx_citation_chunk_hash", "idx_source_version_id"],
        )


if __name__ == "__main__":
    unittest.main()
