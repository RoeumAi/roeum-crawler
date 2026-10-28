"""한 줄 판결문 본문을 번호 앞에서 문단으로 나눈다(글자는 그대로)."""
import unittest

from scripts.utils.paragraph_split import same_text_ignoring_space, split_paragraphs


class SplitParagraphsTest(unittest.TestCase):
    def check(self, text, expected):
        result = split_paragraphs(text)
        self.assertTrue(same_text_ignoring_space(text, result))
        self.assertEqual(result.split("\n\n"), expected)

    def test_sentence_end_then_marker(self):
        self.check("원고의 청구를 기각하였다. 2. 판단 피고는 다투었다. 가. 법리 법리는 이렇다.",
                   ["원고의 청구를 기각하였다.", "2. 판단 피고는 다투었다.", "가. 법리 법리는 이렇다."])

    def test_short_titles_without_period(self):
        self.check("2. 당사자들의 주장 가. 원고의 주장 1) 주위적 청구 피고들은 편취하였다. 2) 예비적 청구 부당이득이다.",
                   ["2. 당사자들의 주장", "가. 원고의 주장", "1) 주위적 청구 피고들은 편취하였다.", "2) 예비적 청구 부당이득이다."])

    def test_dates_and_long_runs_are_not_split(self):
        text = "원고는 2002. 1. 1. 입사하여 2010. 12. 31. 퇴직한 사람으로 제1항 제2호에 따라 1. 2. 단계를 거쳤다고 주장한다."
        self.assertEqual(split_paragraphs(text), text)

    def test_bullets_and_header(self):
        self.check("【이유】 이를 인용한다. ○ 제5쪽을 고친다. ○ 제7쪽을 고친다.",
                   ["【이유】", "이를 인용한다.", "○ 제5쪽을 고친다.", "○ 제7쪽을 고친다."])

    def test_text_with_newlines_untouched(self):
        self.assertEqual(split_paragraphs("1. 가\n2. 나"), "1. 가\n2. 나")


if __name__ == "__main__":
    unittest.main()
