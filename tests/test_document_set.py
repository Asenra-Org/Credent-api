"""One borrower's documents read as one evidence set.

The behaviour that matters here is what happens when two documents disagree.
Silently preferring one would hide the finding the lender needs; the merge
keeps both readings and records the conflict.
"""

import pytest

from app.parsers.document_set import (
    AGREEMENT_TOLERANCE,
    MATERIAL_FIELDS,
    merge,
)

BALANCE_SHEET = """AUDITED FINANCIAL STATEMENTS
Sahyadri Precision Castings Private Limited

BALANCE SHEET (INR Lakhs)
Share Capital: 320.00
Reserves and Surplus: 1,486.00
Long Term Borrowings: 742.00
Short Term Borrowings: 418.00
Trade Payables: 496.00
Total Current Assets: 2,246.00
Total Assets: 4,114.00
"""

PROFIT_AND_LOSS = """STATEMENT OF PROFIT AND LOSS (INR Lakhs)
Sahyadri Precision Castings Private Limited
Revenue from Operations: 3,842.00
Other Income: 58.00
Finance Costs: 148.00
Depreciation and Amortisation: 214.00
Profit Before Tax: 486.00
Profit for the Year: 358.00
"""

# Same borrower, same field, a materially different revenue. This is the
# reconciliation finding the product exists to surface.
GST_SUMMARY = """GST ANNUAL SUMMARY (INR Lakhs)
Sahyadri Precision Castings Private Limited
Revenue from Operations: 3,410.00
Trade Receivables: 1,092.00
"""

KYC_PAGE = """KYC RECORD
Sahyadri Precision Castings Private Limited
PAN: AAECS4471K
GSTIN: 27AAECS4471K1ZP
Registered Office: MIDC Shiroli, Kolhapur
"""


class TestMergingAcrossDocuments:
    def test_fields_from_separate_documents_combine(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        # Neither document alone supports these; together they do.
        assert s.value("total_revenue") == 384_200_000
        assert s.total_debt() == 116_000_000
        assert s.equity() == 180_600_000
        assert s.ebitda() == 84_800_000

    def test_every_figure_names_its_document(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        assert s.figures["long_term_debt"].document == "bs.pdf"
        assert s.figures["total_revenue"].document == "pnl.pdf"
        for name, fig in s.figures.items():
            assert fig.line_number > 0, name
            assert fig.source_line, name

    def test_ratios_can_be_computed_from_the_set(self):
        from app.services.financial_calculator import calculate_financial_ratios

        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        ratios = calculate_financial_ratios(s.to_financial_data())
        assert round(ratios["debt_to_equity"], 2) == 0.64
        assert round(ratios["ebitda_margin"], 2) == 22.07

    def test_a_single_document_still_works(self):
        s = merge({"bs.pdf": BALANCE_SHEET})
        assert s.total_debt() == 116_000_000
        assert s.conflicts == []


class TestConflictDetection:
    """Two documents, one field, different numbers."""

    def test_a_material_disagreement_is_recorded(self):
        s = merge({"pnl.pdf": PROFIT_AND_LOSS, "gst.pdf": GST_SUMMARY})
        fields = [c.field for c in s.conflicts]
        assert "total_revenue" in fields

    def test_both_readings_are_kept_with_their_sources(self):
        s = merge({"pnl.pdf": PROFIT_AND_LOSS, "gst.pdf": GST_SUMMARY})
        conflict = next(c for c in s.conflicts if c.field == "total_revenue")
        by_doc = {r.document: r.value for r in conflict.readings}
        assert by_doc == {"pnl.pdf": 384_200_000, "gst.pdf": 341_000_000}
        # Each reading must be checkable at its source.
        for r in conflict.readings:
            assert r.line_number > 0
            assert "Revenue from Operations" in r.source_line

    def test_the_spread_is_reported(self):
        s = merge({"pnl.pdf": PROFIT_AND_LOSS, "gst.pdf": GST_SUMMARY})
        conflict = next(c for c in s.conflicts if c.field == "total_revenue")
        # (384.2 - 341.0) / 384.2 = 11.2%
        assert 0.11 < conflict.spread < 0.12

    def test_revenue_disagreement_is_material(self):
        s = merge({"pnl.pdf": PROFIT_AND_LOSS, "gst.pdf": GST_SUMMARY})
        conflict = next(c for c in s.conflicts if c.field == "total_revenue")
        assert conflict.material is True
        assert "total_revenue" in MATERIAL_FIELDS
        assert s.material_conflicts

    def test_agreeing_documents_raise_no_conflict(self):
        """The same statement twice is not a discrepancy."""
        s = merge({"a.pdf": BALANCE_SHEET, "b.pdf": BALANCE_SHEET})
        assert s.conflicts == []
        assert s.total_debt() == 116_000_000

    def test_rounding_is_not_a_conflict(self):
        rounded = BALANCE_SHEET.replace("Long Term Borrowings: 742.00",
                                        "Long Term Borrowings: 742.05")
        s = merge({"a.pdf": BALANCE_SHEET, "b.pdf": rounded})
        assert [c.field for c in s.conflicts] == []

    def test_a_gap_beyond_tolerance_is_a_conflict(self):
        shifted = BALANCE_SHEET.replace("Long Term Borrowings: 742.00",
                                        "Long Term Borrowings: 900.00")
        s = merge({"a.pdf": BALANCE_SHEET, "b.pdf": shifted})
        assert "long_term_debt" in [c.field for c in s.conflicts]

    def test_material_conflicts_sort_first(self):
        mixed = PROFIT_AND_LOSS.replace("Other Income: 58.00", "Other Income: 92.00")
        s = merge({"pnl.pdf": PROFIT_AND_LOSS, "gst.pdf": GST_SUMMARY, "alt.pdf": mixed})
        assert s.conflicts[0].material is True

    def test_a_conflict_never_silently_picks_a_winner(self):
        """The merged value is one reading, but the disagreement is still reported."""
        s = merge({"pnl.pdf": PROFIT_AND_LOSS, "gst.pdf": GST_SUMMARY})
        assert s.value("total_revenue") is not None
        assert s.material_conflicts, "a resolved value must not hide the conflict"


class TestNonStatementDocuments:
    def test_a_kyc_page_is_skipped_with_a_reason(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "kyc.pdf": KYC_PAGE})
        reasons = dict(s.skipped)
        assert "kyc.pdf" in reasons
        assert "Schedule III" in reasons["kyc.pdf"]
        # And it must not have disturbed the real statement.
        assert s.total_debt() == 116_000_000

    def test_an_empty_document_is_skipped(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "blank.pdf": "   "})
        assert "blank.pdf" in dict(s.skipped)

    def test_skipping_everything_yields_no_figures(self):
        s = merge({"kyc.pdf": KYC_PAGE})
        assert s.figures == {}
        assert s.value("total_revenue") is None

    def test_summary_reports_what_was_read_and_skipped(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS,
                   "kyc.pdf": KYC_PAGE, "gst.pdf": GST_SUMMARY})
        summary = s.summary()
        assert summary["documents_read"] == 3
        assert [d["document"] for d in summary["documents_skipped"]] == ["kyc.pdf"]
        assert summary["material_conflicts"] >= 1
        assert 0 < summary["coverage"] <= 1


class TestEmptySet:
    def test_no_documents(self):
        s = merge({})
        assert s.figures == {}
        assert s.conflicts == []
        assert s.summary()["documents_read"] == 0

    def test_tolerance_is_a_ratio_not_an_absolute(self):
        assert 0 < AGREEMENT_TOLERANCE < 1


class TestEntityConsistency:
    """A folder holding two borrowers must not become one confident picture."""

    OTHER_BORROWER = """BALANCE SHEET (INR Crores)
Konkan Agro Foods Private Limited
Share Capital: 4.00
Long Term Borrowings: 34.50
"""

    SAME_BORROWER_ABBREVIATED = """STATEMENT OF PROFIT AND LOSS (INR Lakhs)
Sahyadri Precision Castings Pvt Ltd
Revenue from Operations: 3,842.00
Profit Before Tax: 486.00
"""

    def test_one_borrower_is_named(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "pnl.pdf": PROFIT_AND_LOSS})
        assert s.entity_mismatch is False
        assert s.borrower == "Sahyadri Precision Castings Private Limited"

    def test_two_borrowers_are_refused_a_single_identity(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "other.pdf": self.OTHER_BORROWER})
        assert s.entity_mismatch is True
        # Guessing which company the merged figures belong to would attach the
        # appraisal to the wrong borrower.
        assert s.borrower is None
        assert s.summary()["entity_mismatch"] is True

    def test_the_same_company_written_two_ways_is_not_a_mismatch(self):
        """"Private Limited" on one document, "Pvt Ltd" on the next."""
        s = merge({"bs.pdf": BALANCE_SHEET,
                   "pnl.pdf": self.SAME_BORROWER_ABBREVIATED})
        assert s.entity_mismatch is False
        assert s.borrower is not None

    def test_each_document_reports_the_entity_it_named(self):
        s = merge({"bs.pdf": BALANCE_SHEET, "other.pdf": self.OTHER_BORROWER})
        named = s.summary()["entities_named"]
        assert named["bs.pdf"].startswith("Sahyadri")
        assert named["other.pdf"].startswith("Konkan")

    def test_an_unnamed_document_does_not_trigger_a_mismatch(self):
        """A P&L page that omits the company name is common and harmless."""
        anonymous = "STATEMENT OF PROFIT AND LOSS (INR Lakhs)\nRevenue from Operations: 3,842.00\n"
        s = merge({"bs.pdf": BALANCE_SHEET, "anon.pdf": anonymous})
        assert s.entity_mismatch is False
