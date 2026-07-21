import unittest

from scripts.core.database.change_detector import ArticleDiffBuilder


class ArticleDiffBuilderTest(unittest.TestCase):
    def test_detects_revision_when_content_differs(self):
        old_articles = [{"article_number": "1", "content": "구 조문"}]
        new_articles = [{"article_number": "1", "content": "신 조문"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["article_number"], "1")
        self.assertEqual(diffs[0]["change_type"], "개정")
        self.assertEqual(diffs[0]["old_content"], "구 조문")
        self.assertEqual(diffs[0]["new_content"], "신 조문")

    def test_detects_new_article(self):
        old_articles = [{"article_number": "1", "content": "조문1"}]
        new_articles = [
            {"article_number": "1", "content": "조문1"},
            {"article_number": "1.1", "content": "신설 조문"},
        ]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["article_number"], "1.1")
        self.assertEqual(diffs[0]["change_type"], "신설")
        self.assertIsNone(diffs[0]["old_content"])

    def test_detects_deletion_via_removed_article(self):
        old_articles = [
            {"article_number": "1", "content": "조문1"},
            {"article_number": "2", "content": "조문2"},
        ]
        new_articles = [{"article_number": "1", "content": "조문1"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["article_number"], "2")
        self.assertEqual(diffs[0]["change_type"], "삭제")
        self.assertIsNone(diffs[0]["new_content"])

    def test_detects_deletion_via_placeholder_content(self):
        old_articles = [{"article_number": "3", "content": "조문3 원문"}]
        new_articles = [{"article_number": "3", "content": "<삭제>"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["change_type"], "삭제")

    def test_excludes_unchanged_articles(self):
        old_articles = [{"article_number": "1", "content": "동일 내용"}]
        new_articles = [{"article_number": "1", "content": "동일 내용"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(diffs, [])


if __name__ == "__main__":
    unittest.main()
