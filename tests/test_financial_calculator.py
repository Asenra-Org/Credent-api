import pytest

from app.services.financial_calculator import (
    calculate_dscr,
    calculate_debt_to_equity,
    calculate_current_ratio,
    calculate_ebitda_margin,
    calculate_financial_ratios,
)


def test_calculate_dscr():
    result = calculate_dscr(1_000_000, 500_000)

    assert result == pytest.approx(2.0)


def test_calculate_debt_to_equity():
    result = calculate_debt_to_equity(5_000_000, 2_500_000)

    assert result == pytest.approx(2.0)


def test_calculate_current_ratio():
    result = calculate_current_ratio(3_000_000, 2_000_000)

    assert result == pytest.approx(1.5)


def test_calculate_ebitda_margin():
    result = calculate_ebitda_margin(1_500_000, 10_000_000)

    assert result == pytest.approx(15.0)


def test_dscr_zero_debt_service():
    assert calculate_dscr(1_000_000, 0) is None


def test_debt_to_equity_zero_equity():
    assert calculate_debt_to_equity(5_000_000, 0) is None


def test_current_ratio_zero_liabilities():
    assert calculate_current_ratio(3_000_000, 0) is None


def test_ebitda_margin_zero_revenue():
    assert calculate_ebitda_margin(1_500_000, 0) is None


def test_calculate_financial_ratios():
    financial_data = {
        "cash_flow_available_for_debt_service": 1_000_000,
        "debt_service": 500_000,
        "total_debt": 5_000_000,
        "total_equity": 2_500_000,
        "current_assets": 3_000_000,
        "current_liabilities": 2_000_000,
        "ebitda": 1_500_000,
        "revenue": 10_000_000,
    }

    result = calculate_financial_ratios(financial_data)

    assert result["dscr"] == pytest.approx(2.0)
    assert result["debt_to_equity"] == pytest.approx(2.0)
    assert result["current_ratio"] == pytest.approx(1.5)
    assert result["ebitda_margin"] == pytest.approx(15.0)