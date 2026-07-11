# =============================================================================
# CREDENT — Unit Tests: Document Ingestion Preprocessing
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
Tests for the PDF text-cleaning and financial-term-validation utilities
added to DocumentIngestionAgent.

Run with:
    pytest tests/ -v

All tests are synchronous and do NOT require a live Groq API key, a real PDF
file, or any network connection.  We test only the pure preprocessing logic by
calling the helpers directly on controlled string inputs.
"""
import pytest
import os

# Set a dummy API key so DocumentIngestionAgent.__init__ doesn't warn.
os.environ.setdefault("GROQ_API_KEY", "test-dummy-key-does-not-make-network-calls")

# ---------------------------------------------------------------------------
# NOW import the module under test
# ---------------------------------------------------------------------------
from app.agents.input.document_ingestion import (  # noqa: E402
    _remove_duplicate_headers,
    _RE_CONTROL_CHARS,
    _RE_EXCESS_NEWLINES,
    _RE_FINANCIAL_TERMS,
    DocumentIngestionAgent,
)



# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def agent() -> DocumentIngestionAgent:
    """A single shared agent instance for the whole test module.

    ``scope="module"`` means this is created once, not before every test,
    which keeps the test suite fast since __init__ performs I/O (env-var
    lookup + ChatGroq object construction).
    """
    return DocumentIngestionAgent()


# ---------------------------------------------------------------------------
# _remove_duplicate_headers Tests
# ---------------------------------------------------------------------------

class TestRemoveDuplicateHeaders:
    """Tests for the module-level _remove_duplicate_headers() utility."""

    def test_empty_list_returns_empty(self):
        """Edge case: empty input should return an empty list without error."""
        assert _remove_duplicate_headers([]) == []

    def test_single_page_is_unchanged(self):
        """A one-page doc cannot have duplicate headers — must pass through untouched."""
        pages = ["ACME Corp Annual Report\n\nRevenue: 50 Cr\nProfit: 10 Cr"]
        result = _remove_duplicate_headers(pages)
        assert result == pages

    def test_two_pages_not_removed(self):
        """A header on exactly 2 pages is BELOW the threshold and must NOT be removed."""
        header = "ACME Corp — Confidential"
        pages = [
            f"{header}\nRevenue: 50 Cr",
            f"{header}\nDebt: 20 Cr",
        ]
        result = _remove_duplicate_headers(pages)
        # Both pages should still contain the header line.
        assert any(header in page for page in result)

    def test_header_on_three_pages_is_removed(self):
        """A line appearing at the top of >= 3 pages must be stripped from all pages."""
        header = "ACME Corp — Confidential"
        pages = [
            f"{header}\nRevenue: 50 Cr\nDetails here.",
            f"{header}\nDebt: 20 Cr\nMore details.",
            f"{header}\nEquity: 30 Cr\nFinal page.",
        ]
        result = _remove_duplicate_headers(pages)
        for page in result:
            assert header not in page, (
                f"Header '{header}' should have been removed but was found in: {page!r}"
            )

    def test_content_below_header_is_preserved(self):
        """Non-header lines must be fully preserved after duplicate header removal."""
        header = "Report Title"
        pages = [
            f"{header}\nRevenue: 100 Cr",
            f"{header}\nDebt: 40 Cr",
            f"{header}\nEquity: 60 Cr",
        ]
        result = _remove_duplicate_headers(pages)
        assert "Revenue: 100 Cr" in result[0]
        assert "Debt: 40 Cr" in result[1]
        assert "Equity: 60 Cr" in result[2]

    def test_no_duplicate_headers_returns_unchanged(self):
        """Pages with distinct first lines must be returned as-is."""
        pages = [
            "January Statement\nRevenue: 10 Cr",
            "February Statement\nRevenue: 12 Cr",
            "March Statement\nRevenue: 11 Cr",
        ]
        result = _remove_duplicate_headers(pages)
        assert result == pages

    def test_leading_blank_lines_skipped_correctly(self):
        """Pages that start with blank lines should still have their true first
        non-empty line detected as the candidate header."""
        header = "ACME Annual Report"
        pages = [
            f"\n\n{header}\nRevenue: 50 Cr",
            f"\n\n{header}\nDebt: 20 Cr",
            f"\n\n{header}\nEquity: 30 Cr",
        ]
        result = _remove_duplicate_headers(pages)
        for page in result:
            assert header not in page


# ---------------------------------------------------------------------------
# _clean_text Tests
# ---------------------------------------------------------------------------

class TestCleanText:
    """Tests for DocumentIngestionAgent._clean_text()."""

    def test_empty_string_returns_empty(self, agent):
        """Edge case: empty input must return empty output without error."""
        assert agent._clean_text("") == ""

    def test_none_returns_none(self, agent):
        """Edge case: falsy input (None) must be returned as-is."""
        assert agent._clean_text(None) is None

    def test_control_characters_removed(self, agent):
        """Non-printable control characters must be stripped from the output."""
        # \x00 = null byte, \x0c = form-feed (common in PDFs), \x1b = ESC
        dirty = "Revenue: 50 Cr\x00\nDebt: 20 Cr\x0c\nEquity: 30 Cr\x1b"
        result = agent._clean_text(dirty)
        assert "\x00" not in result
        assert "\x0c" not in result
        assert "\x1b" not in result

    def test_readable_content_preserved(self, agent):
        """All printable ASCII content must survive the cleaning pass."""
        content = "Company: ACME Ltd\nRevenue: 50 Cr\nTotal Debt: 20 Cr"
        result = agent._clean_text(content)
        assert "ACME Ltd" in result
        assert "Revenue: 50 Cr" in result
        assert "Total Debt: 20 Cr" in result

    def test_newlines_and_tabs_preserved(self, agent):
        """Standard whitespace (\\n, \\r, \\t) must NOT be stripped by the
        control-character pass — only non-printable non-whitespace chars are removed."""
        text = "Line 1\n\tIndented\r\nLine 2"
        result = agent._clean_text(text)
        assert "\t" in result
        assert "\n" in result

    def test_excessive_blank_lines_collapsed(self, agent):
        """Three or more consecutive blank lines must be collapsed to two."""
        text = "Section A\n\n\n\n\nSection B"
        result = agent._clean_text(text)
        assert "\n\n\n" not in result

    def test_two_blank_lines_preserved(self, agent):
        """Exactly two consecutive newlines (one blank line) must be kept intact."""
        text = "Paragraph A\n\nParagraph B"
        result = agent._clean_text(text)
        assert "Paragraph A" in result
        assert "Paragraph B" in result

    def test_leading_trailing_whitespace_stripped(self, agent):
        """Output must not start or end with whitespace."""
        text = "\n\n   ACME Revenue: 50 Cr   \n\n"
        result = agent._clean_text(text)
        assert result == result.strip()

    def test_mixed_control_and_excess_newlines(self, agent):
        """Both passes must apply correctly when dirty text has both problems."""
        text = "Header\x00\n\n\n\n\nFooter\x0c"
        result = agent._clean_text(text)
        assert "\x00" not in result
        assert "\x0c" not in result
        assert "\n\n\n" not in result


# ---------------------------------------------------------------------------
# _contains_financial_terms Tests
# ---------------------------------------------------------------------------

class TestContainsFinancialTerms:
    """Tests for DocumentIngestionAgent._contains_financial_terms()."""

    # --- Positive cases (should return True) ---

    def test_revenue_detected(self, agent):
        assert agent._contains_financial_terms("Total revenue for FY2024 was 120 Cr") is True

    def test_turnover_detected(self, agent):
        assert agent._contains_financial_terms("Annual turnover: 85 crore") is True

    def test_balance_sheet_detected(self, agent):
        assert agent._contains_financial_terms("As per the balance sheet dated 31 March") is True

    def test_borrowings_detected(self, agent):
        assert agent._contains_financial_terms("Total borrowings from banks: 40 Cr") is True

    def test_cibil_detected(self, agent):
        assert agent._contains_financial_terms("CIBIL score: 720") is True

    def test_gstr_detected(self, agent):
        assert agent._contains_financial_terms("GSTR-3B filed for Q3 FY24") is True

    def test_inr_symbol_detected(self, agent):
        assert agent._contains_financial_terms("Net profit: 25,00,000") is True

    def test_audit_detected(self, agent):
        assert agent._contains_financial_terms("Audited financial results for FY2024") is True

    def test_npa_detected(self, agent):
        assert agent._contains_financial_terms("Account classified as NPA on Jan 2024") is True

    def test_case_insensitive_matching(self, agent):
        """Keyword matching must be case-insensitive (e.g. REVENUE vs revenue)."""
        assert agent._contains_financial_terms("REVENUE: 50 CR") is True
        assert agent._contains_financial_terms("Revenue: 50 Cr") is True
        assert agent._contains_financial_terms("revenue: 50 cr") is True

    def test_lakh_detected(self, agent):
        assert agent._contains_financial_terms("Loan amount: 15 lakh") is True

    def test_crore_detected(self, agent):
        assert agent._contains_financial_terms("Working capital limit: 5 crore") is True

    def test_depreciation_detected(self, agent):
        assert agent._contains_financial_terms("Annual depreciation charge: 2.5 Cr") is True

    def test_ebitda_detected(self, agent):
        assert agent._contains_financial_terms("EBITDA margin: 18%") is True

    # --- Negative cases (should return False) ---

    def test_generic_brochure_rejected(self, agent):
        text = (
            "Welcome to ACME Inc. We are a leading provider of innovative solutions. "
            "Our team is dedicated to excellence, customer satisfaction, and growth. "
            "Contact us at info@acme.com for more details."
        )
        assert agent._contains_financial_terms(text) is False

    def test_hr_policy_rejected(self, agent):
        text = (
            "Leave Policy: All employees are entitled to 20 days of casual leave per year. "
            "Applications must be submitted via the HR portal 3 days in advance."
        )
        assert agent._contains_financial_terms(text) is False

    def test_empty_string_rejected(self, agent):
        assert agent._contains_financial_terms("") is False

    def test_whitespace_only_rejected(self, agent):
        assert agent._contains_financial_terms("   \n\n   ") is False

    def test_random_numbers_not_enough(self, agent):
        """Numbers alone (no keywords) must not trigger a match."""
        assert agent._contains_financial_terms("12345 67890 9999 100 200 300") is False
