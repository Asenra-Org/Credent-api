"""
Credent - Financial Ratio Calculator

Deterministic financial ratio calculations.
No LLM is used for mathematical calculations.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _to_float(value: Any) -> Optional[float]:
    """
    Safely convert a value to float.

    Supports:
    - int
    - float
    - numeric strings
    - None

    Returns None when conversion is not possible.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return float(value)

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_divide(
    numerator: Any,
    denominator: Any,
) -> Optional[float]:
    """
    Safely divide numerator by denominator.

    Returns None when:
    - either value is missing
    - denominator is zero
    """
    numerator = _to_float(numerator)
    denominator = _to_float(denominator)

    if numerator is None or denominator is None:
        return None

    if denominator == 0:
        return None

    return numerator / denominator


# ---------------------------------------------------------------------------
# Individual Financial Ratio Calculators
# ---------------------------------------------------------------------------


def calculate_dscr(
    cash_flow_available_for_debt_service: Any,
    debt_service: Any = None,
) -> Optional[float]:
    """
    Calculate Debt Service Coverage Ratio (DSCR).

    Formula:
        DSCR = Cash Flow Available for Debt Service / Debt Service

    Examples:
        calculate_dscr(1_000_000, 500_000) -> 2.0

    Returns None when debt service is zero or data is unavailable.
    """
    return _safe_divide(
        cash_flow_available_for_debt_service,
        debt_service,
    )


def calculate_debt_to_equity(
    total_debt: Any,
    total_equity: Any = None,
) -> Optional[float]:
    """
    Calculate Debt-to-Equity ratio.

    Formula:
        Debt-to-Equity = Total Debt / Total Equity

    Examples:
        calculate_debt_to_equity(5_000_000, 2_500_000) -> 2.0
    """
    return _safe_divide(
        total_debt,
        total_equity,
    )


def calculate_current_ratio(
    current_assets: Any,
    current_liabilities: Any = None,
) -> Optional[float]:
    """
    Calculate Current Ratio.

    Formula:
        Current Ratio = Current Assets / Current Liabilities

    Examples:
        calculate_current_ratio(3_000_000, 2_000_000) -> 1.5
    """
    return _safe_divide(
        current_assets,
        current_liabilities,
    )


def calculate_ebitda_margin(
    ebitda: Any,
    revenue: Any = None,
) -> Optional[float]:
    """
    Calculate EBITDA margin as a percentage.

    Formula:
        EBITDA Margin = (EBITDA / Revenue) * 100

    Examples:
        calculate_ebitda_margin(1_500_000, 10_000_000) -> 15.0
    """
    ratio = _safe_divide(ebitda, revenue)

    if ratio is None:
        return None

    return ratio * 100


def calculate_quick_ratio(
    quick_assets: Any,
    current_liabilities: Any = None,
) -> Optional[float]:
    """
    Calculate Quick Ratio.

    Formula:
        Quick Ratio = Quick Assets / Current Liabilities

    If quick_assets is not supplied, the aggregate calculator
    will attempt to calculate it from:
        current_assets - inventory
    """
    return _safe_divide(
        quick_assets,
        current_liabilities,
    )


# ---------------------------------------------------------------------------
# Aggregate Financial Ratio Calculator
# ---------------------------------------------------------------------------


def calculate_financial_ratios(
    financial_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Calculate all supported financial ratios.

    Supported input fields:

        DSCR:
            cash_flow_available_for_debt_service
            OR
            net_operating_income
            debt_service

        Debt-to-Equity:
            total_debt
            total_equity

        Current Ratio:
            current_assets
            current_liabilities

        EBITDA Margin:
            ebitda
            revenue

        Quick Ratio:
            quick_assets
            OR current_assets - inventory
            current_liabilities

    Returns:
        {
            "dscr": ...,
            "debt_to_equity": ...,
            "current_ratio": ...,
            "ebitda_margin": ...,
            "quick_ratio": ...,
            "notes": [...]
        }
    """

    if financial_data is None:
        financial_data = {}

    if not isinstance(financial_data, dict):
        raise TypeError("financial_data must be a dictionary")

    notes = []

    # -----------------------------------------------------------------------
    # DSCR
    # -----------------------------------------------------------------------
    #
    # Primary field:
    #   cash_flow_available_for_debt_service
    #
    # FinancialHealthAgent tests also use:
    #   net_operating_income
    #
    # Therefore support both.
    # -----------------------------------------------------------------------

    cash_flow_available_for_debt_service = financial_data.get(
        "cash_flow_available_for_debt_service"
    )

    if cash_flow_available_for_debt_service is None:
        cash_flow_available_for_debt_service = financial_data.get(
            "net_operating_income"
        )

    debt_service = financial_data.get("debt_service")

    dscr = calculate_dscr(
        cash_flow_available_for_debt_service,
        debt_service,
    )

    if dscr is None:
        # Only add a note when the user attempted to provide
        # DSCR-related data but calculation could not be performed.
        if (
            cash_flow_available_for_debt_service is not None
            or debt_service is not None
        ):
            notes.append(
                "Required values for DSCR are unavailable."
            )

    # -----------------------------------------------------------------------
    # Debt-to-Equity
    # -----------------------------------------------------------------------

    total_debt = financial_data.get("total_debt")
    total_equity = financial_data.get("total_equity")

    debt_to_equity = calculate_debt_to_equity(
        total_debt,
        total_equity,
    )

    if debt_to_equity is None:
        if total_debt is not None or total_equity is not None:
            notes.append(
                "Required values for debt-to-equity ratio are unavailable."
            )

    # -----------------------------------------------------------------------
    # Current Ratio
    # -----------------------------------------------------------------------

    current_assets = financial_data.get("current_assets")
    current_liabilities = financial_data.get("current_liabilities")

    current_ratio = calculate_current_ratio(
        current_assets,
        current_liabilities,
    )

    if current_ratio is None:
        if current_assets is not None or current_liabilities is not None:
            notes.append(
                "Required values for current ratio are unavailable."
            )

    # -----------------------------------------------------------------------
    # EBITDA Margin
    # -----------------------------------------------------------------------

    ebitda = financial_data.get("ebitda")
    revenue = financial_data.get("revenue")

    ebitda_margin = calculate_ebitda_margin(
        ebitda,
        revenue,
    )

    if ebitda_margin is None:
        if ebitda is not None or revenue is not None:
            notes.append(
                "Required values for EBITDA margin are unavailable."
            )

    # -----------------------------------------------------------------------
    # Quick Ratio
    # -----------------------------------------------------------------------

    quick_assets = financial_data.get("quick_assets")

    if quick_assets is None:
        inventory = financial_data.get("inventory")

        if current_assets is not None and inventory is not None:
            current_assets_num = _to_float(current_assets)
            inventory_num = _to_float(inventory)

            if (
                current_assets_num is not None
                and inventory_num is not None
            ):
                quick_assets = current_assets_num - inventory_num

    quick_ratio = calculate_quick_ratio(
        quick_assets,
        current_liabilities,
    )

    # Do NOT add a quick-ratio warning when the source data simply
    # does not contain quick-ratio inputs. This keeps complete
    # financial payloads from receiving unnecessary notes.

    return {
        "dscr": round(dscr, 4) if dscr is not None else None,
        "debt_to_equity": (
            round(debt_to_equity, 4)
            if debt_to_equity is not None
            else None
        ),
        "current_ratio": (
            round(current_ratio, 4)
            if current_ratio is not None
            else None
        ),
        "ebitda_margin": (
            round(ebitda_margin, 4)
            if ebitda_margin is not None
            else None
        ),
        "quick_ratio": (
            round(quick_ratio, 4)
            if quick_ratio is not None
            else None
        ),
        "notes": notes,
    }