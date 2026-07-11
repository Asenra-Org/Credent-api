"""
test_text_processing.py
------------------------
Unit tests for CreditKeywordMatcher / extract_keywords.
"""

import unittest
from text_processing import CreditKeywordMatcher, extract_keywords


class TestCreditKeywordMatcher(unittest.TestCase):

    def setUp(self):
        self.matcher =  CreditKeywordMatcher()

    def test_single_keyword_detected(self):
        text = "The loan account is currently overdue."
        result = self.matcher.extract_keywords(text)
        self.assertIn("overdue", result)

    def test_multiple_keywords_detected(self):
        text = "The account is NPA and under restructuring due to default."
        result = self.matcher.extract_keywords(text)
        for expected in ["npa", "restructuring", "default"]:
            self.assertIn(expected, result)

    def test_case_insensitivity(self):
        text = "DEFAULT and Restructuring were both flagged."
        result = self.matcher.extract_keywords(text)
        self.assertIn("default", result)
        self.assertIn("restructuring", result)

    def test_no_keywords_present(self):
        text = "The quarterly report shows stable growth and profits."
        result = self.matcher.extract_keywords(text)
        self.assertEqual(result, [])

    def test_empty_string_input(self):
        self.assertEqual(self.matcher.extract_keywords(""), [])

    def test_non_string_input_returns_empty(self):
        self.assertEqual(self.matcher.extract_keywords(None), [])
        self.assertEqual(self.matcher.extract_keywords(12345), [])

    def test_no_duplicate_results(self):
        text = "default default DEFAULT default"
        result = self.matcher.extract_keywords(text)
        self.assertEqual(result.count("default"), 1)

    def test_hyphenated_keyword(self):
        text = "The loan resulted in a write-off last quarter."
        result = self.matcher.extract_keywords(text)
        self.assertIn("write-off", result)

    def test_multi_word_phrase_keyword(self):
        text = "This was classified as a non-performing asset by the bank."
        result = self.matcher.extract_keywords(text.replace("non-performing asset", "non-performing asset"))
        # phrase keyword check
        text2 = "This was classified as a non-performing asset by the bank."
        result2 = self.matcher.extract_keywords(text2)
        self.assertIn("non-performing asset", result2)

    def test_module_level_function(self):
        text = "Insolvency proceedings have begun."
        result = extract_keywords(text)
        self.assertIn("insolvency", result)

    def test_word_boundary_no_partial_match(self):
        text = "defaultingprocess"
        result = self.matcher.extract_keywords(text)
        self.assertNotIn("default", result)

    def test_large_input_does_not_crash(self):
        text = "safe filler text " * 50000 + "default"
        result = self.matcher.extract_keywords(text)
        self.assertIn("default", result)

    def test_custom_keyword_list(self):
        custom = {"fraud", "embezzlement"}
        matcher = CreditKeywordMatcher(keywords=custom)
        text = "Investigation revealed fraud in the account."
        result = matcher.extract_keywords(text)
        self.assertIn("fraud", result)
        self.assertNotIn("default", result)


if __name__ == "__main__":
    unittest.main()