import hashlib
import unittest

from scripts.core.database.source_versioning import (
    build_mongo_set_fields,
    build_source_version_id,
    enrich_source_document,
    is_complete_chunk_save,
    sha256_content,
)


class SourceVersioningTest(unittest.TestCase):
    def test_hashes_exact_full_content_with_sha256(self):
        content = "가" * 900

        self.assertEqual(
            sha256_content(content),
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def test_builds_stable_version_id_from_collection_chunk_and_hash(self):
        self.assertEqual(
            build_source_version_id("law", "law:108467:article:23", "abc123"),
            "law:law:108467:article:23:abc123",
        )

    def test_rejects_incomplete_version_identity(self):
        self.assertEqual(build_source_version_id("", "chunk", "hash"), "")
        self.assertEqual(build_source_version_id("law", "", "hash"), "")
        self.assertEqual(build_source_version_id("law", "chunk", ""), "")

    def test_does_not_claim_provenance_for_empty_content(self):
        enriched = enrich_source_document(
            {"chunk_id": "law:108467:article:23", "content": ""},
            "law",
        )

        self.assertNotIn("content_hash", enriched)
        self.assertNotIn("source_version_id", enriched)

    def test_enriches_document_without_overwriting_explicit_active_state(self):
        enriched = enrich_source_document(
            {
                "chunk_id": "law:108467:article:23",
                "content": "원문",
                "metadata": {"is_active": False},
            },
            "law",
        )

        expected_hash = hashlib.sha256("원문".encode("utf-8")).hexdigest()
        self.assertEqual(enriched["content_hash"], expected_hash)
        self.assertEqual(
            enriched["source_version_id"],
            f"law:law:108467:article:23:{expected_hash}",
        )
        self.assertFalse(enriched["metadata"]["is_active"])

    def test_builds_mongo_set_fields_without_parent_metadata_conflict(self):
        document = enrich_source_document(
            {
                "chunk_id": "case:doc-1:section:1",
                "doc_id": "doc-1",
                "content": "원문",
            },
            "case",
        )

        set_fields = build_mongo_set_fields(
            document,
            {
                "source_url": "https://example.com/doc-1",
                "updated_at": "2026-07-27T00:00:00",
            },
        )

        self.assertNotIn("metadata", set_fields)
        self.assertTrue(set_fields["metadata.is_active"])
        self.assertEqual(
            set_fields["metadata.source_url"],
            "https://example.com/doc-1",
        )
        self.assertEqual(
            set_fields["metadata.updated_at"],
            "2026-07-27T00:00:00",
        )

    def test_partial_chunk_save_is_not_complete(self):
        self.assertFalse(
            is_complete_chunk_save(saved=3, unchanged=0, failed=1)
        )
        self.assertTrue(
            is_complete_chunk_save(saved=0, unchanged=4, failed=0)
        )


if __name__ == "__main__":
    unittest.main()
