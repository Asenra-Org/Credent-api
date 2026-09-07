"""Schedule III parser: deterministic extraction with no provider call.

These tests pin the behaviour that makes the parser worth having - the same
document always yields the same figures, every figure names the line it came
from, and a missing borrowings line reports "unknown" rather than "no debt".
"""

import pytest

from app.parsers.schedule_iii import (
    CRORE,
    LAKH,
    detect_unit,
    looks_like_schedule_iii,
    parse_schedule_iii,
)

STATEMENT = """AUDITED FINANCIAL STATEMENTS
Axis Technocast Private Limited
FY 2023-24

PROFIT AND LOSS STATEMENT (INR Lakhs)
Revenue from Operations: 2,450.00
Other Income: 45.00
Cost of Materials Consumed: 1,420.00
Employee Benefit Expenses: 198.00
Depreciation: 87.00
Finance Costs: 62.00
Profit Before Tax: 273.00
Tax Expense: 68.00
Profit After Tax (PAT): 205.00

BALANCE SHEET (INR Lakhs)
Total Current Assets: 900.00
Total Assets: 1,580.00
Share Capital: 200.00
Reserves and Surplus: 580.00
Long Term Borrowings: 350.00
Short Term Borrowings: 180.00
Trade Payables: 220.00
Other Current Liabilities: 50.00

KEY FINANCIAL RATIOS
Current Ratio: 2.14
Debt to Equity Ratio: 0.67
EBITDA: 422.00 Lakhs
"""


class TestUnitDetection:
    """The statement declares its unit; it is read, never inferred."""

    @pytest.mark.parametrize("header,expected,name", [
        ("PROFIT AND LOSS (INR Lakhs)", LAKH, "Lakhs"),
        ("Balance Sheet (INR Crores)", CRORE, "Crores"),
        ("Statement in Thousands", 1_000, "Thousands"),
        ("Balance Sheet", 1, "Absolute INR"),
    ])
    def test_declared_unit_is_read(self, header, expected, name):
        multiplier, unit_name = detect_unit(header)
        assert multiplier == expected
        assert unit_name == name

    def test_figures_are_scaled_to_rupees(self):
        r = parse_schedule_iii(STATEMENT)
        # 2,450.00 Lakhs is 24.5 crore, not 2450 rupees.
        assert r.value("total_revenue") == 245_000_000


class TestExtraction:
    def test_headline_figures(self):
        r = parse_schedule_iii(STATEMENT)
        assert r.value("total_revenue") == 245_000_000
        assert r.value("pat") == 20_500_000
        assert r.value("long_term_debt") == 35_000_000
        assert r.value("short_term_debt") == 18_000_000

    def test_every_figure_names_its_source_line(self):
        r = parse_schedule_iii(STATEMENT)
        for name, fig in r.figures.items():
            assert fig.line_number > 0, name
            assert fig.source_line, name
            # The citation must be checkable against the document.
            assert fig.source_line in STATEMENT, name

    def test_citations_are_marked_verified(self):
        """Deterministically matched, so unlike an LLM citation it cannot be invented."""
        r = parse_schedule_iii(STATEMENT)
        assert all(c["verified"] for c in r.citations().values())

    def test_other_income_never_captured_as_revenue(self):
        """Captions match whole, not by substring."""
        r = parse_schedule_iii(STATEMENT)
        assert r.value("other_income") == 4_500_000
        assert r.value("total_revenue") == 245_000_000

    def test_parsing_is_deterministic(self):
        a = parse_schedule_iii(STATEMENT)
        b = parse_schedule_iii(STATEMENT)
        assert {k: v.value for k, v in a.figures.items()} == \
               {k: v.value for k, v in b.figures.items()}


class TestScheduleIIIRules:
    """The aggregation and exclusion rules, previously an English prompt."""

    def test_total_debt_sums_both_halves(self):
        r = parse_schedule_iii(STATEMENT)
        assert r.total_debt() == 53_000_000

    def test_trade_payables_are_not_debt(self):
        r = parse_schedule_iii(STATEMENT)
        # Trade payables (220L) and other current liabilities (50L) are present
        # but must not inflate leverage.
        assert r.value("trade_payables") == 22_000_000
        assert r.total_debt() == 53_000_000

    def test_missing_borrowings_reports_unknown_not_zero(self):
        """A statement with no borrowings line means unknown, never zero debt."""
        no_debt = "BALANCE SHEET (INR Lakhs)\nShare Capital: 200.00\n"
        r = parse_schedule_iii(no_debt)
        assert r.total_debt() is None, "absent debt must not read as nil debt"

    def test_only_one_half_of_borrowings_still_totals(self):
        one = "BALANCE SHEET (INR Lakhs)\nLong Term Borrowings: 350.00\n"
        assert parse_schedule_iii(one).total_debt() == 35_000_000

    def test_equity_and_ebitda(self):
        r = parse_schedule_iii(STATEMENT)
        assert r.equity() == 78_000_000
        # PBT 273 + finance 62 + depreciation 87 = 422 Lakhs
        assert r.ebitda() == 42_200_000

    def test_ebitda_is_none_without_pbt(self):
        r = parse_schedule_iii("Finance Costs: 62.00\nDepreciation: 87.00\n")
        assert r.ebitda() is None


class TestSelfReportedRatios:
    """Reading what the document claims lets the caller check it."""

    def test_stated_ratios_are_read(self):
        r = parse_schedule_iii(STATEMENT)
        assert r.stated_ratios["current_ratio"] == 2.14
        assert r.stated_ratios["debt_to_equity"] == 0.67

    def test_stated_ebitda_matches_the_derived_one(self):
        r = parse_schedule_iii(STATEMENT)
        assert r.stated_ratios["ebitda"] == r.ebitda() == 42_200_000

    def test_a_divergence_is_detectable(self):
        """The document asserts 2.14; its own figures give 2.00."""
        r = parse_schedule_iii(STATEMENT)
        computed = r.value("current_assets") / r.current_liabilities()
        assert round(computed, 2) == 2.00
        assert r.stated_ratios["current_ratio"] == 2.14
        assert round(computed, 2) != r.stated_ratios["current_ratio"]


class TestRobustness:
    def test_empty_input_yields_no_figures(self):
        r = parse_schedule_iii("")
        assert r.figures == {}
        assert len(r.unmatched_captions) > 0

    def test_prose_yields_no_figures(self):
        r = parse_schedule_iii("This is a KYC record. PAN: ABCDE1234F\n")
        assert r.value("total_revenue") is None

    def test_hyphenated_and_legacy_captions(self):
        text = ("BALANCE SHEET (INR Lakhs)\n"
                "Long-term Borrowings: 100.00\nShort-term Borrowings: 50.00\n"
                "Sundry Creditors: 30.00\n")
        r = parse_schedule_iii(text)
        assert r.total_debt() == 15_000_000
        assert r.value("trade_payables") == 3_000_000

    def test_column_layout_without_colons(self):
        text = "BALANCE SHEET (INR Lakhs)\nLong Term Borrowings      350.00\n"
        assert parse_schedule_iii(text).value("long_term_debt") == 35_000_000

    def test_parenthesised_figure_is_negative(self):
        r = parse_schedule_iii("PROFIT AND LOSS (INR Lakhs)\nProfit Before Tax: (45.00)\n")
        assert r.value("pbt") == -4_500_000

    def test_unmatched_captions_are_reported(self):
        r = parse_schedule_iii("BALANCE SHEET (INR Lakhs)\nShare Capital: 200.00\n")
        assert "total_revenue" in r.unmatched_captions
        assert "share_capital" not in r.unmatched_captions


class TestRouting:
    def test_statement_is_recognised(self):
        assert looks_like_schedule_iii(STATEMENT) is True

    def test_kyc_record_is_not(self):
        assert looks_like_schedule_iii(
            "KYC RECORD\nPAN: ABCDE1234F\nGSTIN: 27ABCDE1234F1Z5\n") is False

    def test_empty_is_not(self):
        assert looks_like_schedule_iii("") is False


class TestCalculatorHandoff:
    def test_feeds_the_existing_ratio_engine(self):
        """The deterministic engine downstream is unchanged - it just gets fed."""
        from app.services.financial_calculator import calculate_financial_ratios

        r = parse_schedule_iii(STATEMENT)
        ratios = calculate_financial_ratios(r.to_financial_data())
        assert round(ratios["debt_to_equity"], 2) == 0.68
        assert round(ratios["ebitda_margin"], 2) == 17.22
