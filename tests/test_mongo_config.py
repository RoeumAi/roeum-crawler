import unittest

from scripts.core.database.mongo_config import resolve_mongo_uri


class MongoConfigTest(unittest.TestCase):
    def test_prefers_primary_environment_name(self):
        self.assertEqual(
            resolve_mongo_uri({"MONGODB_URI": "primary", "MONGO_URI": "fallback"}),
            "primary",
        )

    def test_accepts_legacy_environment_name(self):
        self.assertEqual(resolve_mongo_uri({"MONGO_URI": "fallback"}), "fallback")

    def test_fails_closed_when_uri_is_missing(self):
        with self.assertRaisesRegex(RuntimeError, "MONGODB_URI or MONGO_URI"):
            resolve_mongo_uri({})


if __name__ == "__main__":
    unittest.main()
