"""
text_processing.py
-------------------
Local NLP keyword matcher using regex to detect high-risk credit/financial
sector terms in raw document text.
"""

import re

# ---------------------------------------------------------------------------
# High-risk credit/finance keywords
# ---------------------------------------------------------------------------
CREDIT_RISK_KEYWORDS = {
    "default",
    "restructuring",
    "npa",
    "overdue",
    "delinquent",
    "write-off",
    "bankruptcy",
    "insolvency",
    "liquidation",
    "moratorium",
    "loan default",
    "credit downgrade",
    "covenant breach",
    "non-performing asset",
    "provisioning",
    "stressed asset",
    "debt restructuring",
    "recovery proceedings",
    "wilful defaulter",
}


class CreditKeywordMatcher:
    """
    Detects high-risk credit keywords in text.
    """

    def __init__(self, keywords=None):
        self.keywords = {k.lower().strip() for k in (keywords or CREDIT_RISK_KEYWORDS)}

        self.single_words = {
            kw for kw in self.keywords
            if " " not in kw and "-" not in kw
        }

        self.hyphenated = {
            kw for kw in self.keywords
            if "-" in kw
        }

        self.phrases = {
            kw for kw in self.keywords
            if " " in kw
        }

    def _sanitize(self, text):
        """Remove control characters."""
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)

    def extract_keywords(self, text):
        if not isinstance(text, str) or not text.strip():
            return []

        lowered = self._sanitize(text).lower()

        found = set()

        # Fast regex tokenizer
        tokens = re.findall(r"\b[\w-]+\b", lowered)
        token_set = set(tokens)

        # Single-word keywords
        for kw in self.single_words:
            if kw in token_set:
                found.add(kw)

        # Hyphenated keywords
        for kw in self.hyphenated:
            if re.search(r"\b" + re.escape(kw) + r"\b", lowered):
                found.add(kw)

        # Multi-word keywords
        for kw in self.phrases:
            if re.search(r"\b" + re.escape(kw) + r"\b", lowered):
                found.add(kw)

        return sorted(found)


def extract_keywords(text):
    matcher = CreditKeywordMatcher()
    return matcher.extract_keywords(text)


if __name__ == "__main__":
    sample = (
        "The account was flagged as NPA after the company filed for "
        "restructuring and remained overdue."
    )

    print(extract_keywords(sample))