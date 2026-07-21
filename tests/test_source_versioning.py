import hashlib
import unittest

from scripts.core.database.source_versioning import (
    build_source_version_id,
    enrich_source_document,
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


if __name__ == "__main__":
    unittest.main()
