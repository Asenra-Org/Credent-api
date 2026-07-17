# =============================================================================
# CREDENT — Integrity Verification Agent (GST vs Bank Cross-Validation)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Threshold: flag MEDIUM warning when monthly GST vs Bank difference exceeds this.
_MONTHLY_VARIANCE_THRESHOLD: float = 0.20

# Default response when analysis cannot be performed
DEFAULT_INTEGRITY = {
    "status": "completed",
    "flags_detected": 0,
    "flags": [],
    "warning": None
}

class IntegrityVerificationAgent:
    def __init__(self) -> None:
        pass

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def cross_validate(
        self,
        gst_data: List[Dict[str, Any]],
        bank_data: List[Dict[str, Any]],
    ) -> dict:
        """Cross-validate data across GST returns and bank statements with full error handling."""
        
        flags = []
        warnings = []

        # Validate inputs
        if not gst_data or not isinstance(gst_data, list) or len(gst_data) == 0:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "No GST data provided. Integrity check skipped."
            }

        if not bank_data or not isinstance(bank_data, list) or len(bank_data) == 0:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "No bank data provided. Integrity check skipped."
            }

        # Create DataFrames safely
        try:
            df_gst = pd.DataFrame(gst_data)
        except Exception as e:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": f"Could not parse GST data: {str(e)}"
            }

        try:
            df_bank = pd.DataFrame(bank_data)
        except Exception as e:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": f"Could not parse bank data: {str(e)}"
            }
        
        # 1. Revenue Inflation Check (only if required columns exist)
        if 'taxable_value' in df_gst.columns and 'type' in df_bank.columns and 'amount' in df_bank.columns:
            try:
                total_gst_sales = pd.to_numeric(df_gst['taxable_value'], errors='coerce').fillna(0).sum()
                
                bank_credits = df_bank[df_bank['type'] == 'CREDIT']
                total_bank_inflows = pd.to_numeric(bank_credits['amount'], errors='coerce').fillna(0).sum() if len(bank_credits) > 0 else 0
                
                denominator = max(float(total_gst_sales), 1.0)
                variance = abs(float(total_gst_sales) - float(total_bank_inflows)) / denominator
                
                if variance > 0.20:
                    flags.append({
                        "flag": "Revenue Discrepancy",
                        "severity": "HIGH" if variance > 0.4 else "MEDIUM",
                        "details": f"GST Sales ({total_gst_sales:,.0f}) differ from Bank Inflows ({total_bank_inflows:,.0f}) by {variance:.1%}"
                    })
            except Exception as e:
                warnings.append(f"Revenue inflation check failed: {str(e)}")
        else:
            missing_cols = []
            if 'taxable_value' not in df_gst.columns:
                missing_cols.append("gst_data.taxable_value")
            if 'type' not in df_bank.columns:
                missing_cols.append("bank_data.type")
            if 'amount' not in df_bank.columns:
                missing_cols.append("bank_data.amount")
            warnings.append(f"Revenue check skipped — missing columns: {', '.join(missing_cols)}")

        # 2. Circular Trading Detection (only if required columns exist)
        if 'type' in df_gst.columns and 'counterparty_gstin' in df_gst.columns and 'taxable_value' in df_gst.columns:
            try:
                gst_with_type = df_gst[df_gst['type'].isin(['SALE', 'PURCHASE'])]
                
                if len(gst_with_type) > 0:
                    sales_mask = gst_with_type['type'] == 'SALE'
                    purchase_mask = gst_with_type['type'] == 'PURCHASE'
                    
                    if sales_mask.any() and purchase_mask.any():
                        sales_by_entity = gst_with_type[sales_mask].groupby('counterparty_gstin')['taxable_value'].sum()
                        purchases_by_entity = gst_with_type[purchase_mask].groupby('counterparty_gstin')['taxable_value'].sum()
                        
                        intersection = sales_by_entity.index.intersection(purchases_by_entity.index)
                        for gstin in intersection:
                            try:
                                sale_amt = float(sales_by_entity[gstin])
                                purch_amt = float(purchases_by_entity[gstin])
                                if abs(sale_amt - purch_amt) / max(sale_amt, 1.0) < 0.05:
                                    flags.append({
                                        "flag": "Potential Circular Trading",
                                        "severity": "CRITICAL",
                                        "details": f"Identical buy/sell volume with GSTIN {gstin} (Amount: {sale_amt:,.0f})"
                                    })
                            except (ValueError, TypeError) as e:
                                warnings.append(f"Circular trading check error for {gstin}: {str(e)}")
            except Exception as e:
                warnings.append(f"Circular trading detection failed: {str(e)}")
        else:
            warnings.append("Circular trading check skipped — missing required GST columns (type, counterparty_gstin, taxable_value).")

        # 3. Monthly GST-to-Bank Cross-Validation
        monthly_flags, monthly_warnings = self._cross_validate_monthly(
            gst_data=gst_data,
            bank_data=bank_data,
        )
        flags.extend(monthly_flags)
        warnings.extend(monthly_warnings)

        result = {
            "status": "completed",
            "flags_detected": len(flags),
            "flags": flags
        }

        if warnings:
            result["warnings"] = warnings

        return result

    # -------------------------------------------------------------------------
    # Private Helpers
    # -------------------------------------------------------------------------

    def _cross_validate_monthly(
        self,
        gst_data: List[Dict[str, Any]],
        bank_data: List[Dict[str, Any]],
    ) -> tuple[list, list]:
        """
        Compare monthly GST sales against monthly bank credit inflows.

        Algorithm
        ---------
        1. Build a monthly GST sales series: group ``gst_data`` by the
           ``period`` column (YYYY-MM) and sum ``taxable_value``.
        2. Build a monthly bank-credit series: parse ``date`` in ``bank_data``
           to period YYYY-MM, filter rows where ``type == 'CREDIT'``, then
           group and sum ``amount``.
        3. Outer-merge the two series on period so months present in only one
           source are still evaluated (missing values become 0).
        4. For each month calculate:

               pct_diff = |gst_sales - bank_credits| / max(gst_sales, 1)

        5. Flag MEDIUM when ``pct_diff > _MONTHLY_VARIANCE_THRESHOLD`` (20%).

        Returns
        -------
        (flags: list[dict], warnings: list[str])
            flags    — zero or more flag dicts following the project schema.
            warnings — zero or more human-readable skip/error messages.
        """
        flags: list = []
        warnings: list = []

        # --- Require both date/period columns to proceed ---
        df_gst = pd.DataFrame(gst_data)
        df_bank = pd.DataFrame(bank_data)

        has_gst_cols = (
            "taxable_value" in df_gst.columns
            and "period" in df_gst.columns
        )
        has_bank_cols = (
            "amount" in df_bank.columns
            and "type" in df_bank.columns
            and "date" in df_bank.columns
        )

        if not has_gst_cols or not has_bank_cols:
            missing: list[str] = []
            if not has_gst_cols:
                missing.append("gst_data.period / gst_data.taxable_value")
            if not has_bank_cols:
                missing.append("bank_data.date / bank_data.type / bank_data.amount")
            warnings.append(
                f"Monthly GST-Bank cross-validation skipped — missing columns: "
                f"{', '.join(missing)}"
            )
            return flags, warnings

        try:
            # ------------------------------------------------------------------
            # Step 1 — Monthly GST sales aggregation
            # ------------------------------------------------------------------
            # Coerce to numeric; non-parseable values become NaN then 0.
            df_gst["taxable_value"] = pd.to_numeric(
                df_gst["taxable_value"], errors="coerce"
            ).fillna(0.0)

            # Normalise period: accept both 'YYYY-MM' and any parseable date.
            df_gst["period"] = self._normalise_period(df_gst["period"])

            # Drop rows where period could not be parsed.
            gst_valid = df_gst.dropna(subset=["period"])

            if gst_valid.empty:
                warnings.append(
                    "Monthly GST-Bank cross-validation skipped — no valid period "
                    "values found in gst_data."
                )
                return flags, warnings

            # Group by month and sum; duplicate months are intentionally merged.
            monthly_gst: pd.Series = (
                gst_valid
                .groupby("period")["taxable_value"]
                .agg("sum")
                .rename("gst_sales")
            )

            # ------------------------------------------------------------------
            # Step 2 — Monthly bank credit aggregation
            # ------------------------------------------------------------------
            df_bank["amount"] = pd.to_numeric(
                df_bank["amount"], errors="coerce"
            ).fillna(0.0)

            # Filter to CREDIT transactions only (case-insensitive).
            credits_mask = df_bank["type"].str.upper() == "CREDIT"
            df_credits = df_bank[credits_mask].copy()

            if df_credits.empty:
                warnings.append(
                    "Monthly GST-Bank cross-validation skipped — no CREDIT "
                    "transactions found in bank_data."
                )
                return flags, warnings

            # Parse transaction dates → 'YYYY-MM' period key.
            df_credits["period"] = self._normalise_period(df_credits["date"])
            bank_valid = df_credits.dropna(subset=["period"])

            if bank_valid.empty:
                warnings.append(
                    "Monthly GST-Bank cross-validation skipped — no parseable "
                    "date values found in bank_data."
                )
                return flags, warnings

            monthly_bank: pd.Series = (
                bank_valid
                .groupby("period")["amount"]
                .agg("sum")
                .rename("bank_credits")
            )

            # ------------------------------------------------------------------
            # Step 3 — Outer merge: retain months present in either source.
            # ------------------------------------------------------------------
            merged: pd.DataFrame = (
                pd.merge(
                    monthly_gst.reset_index(),
                    monthly_bank.reset_index(),
                    on="period",
                    how="outer",
                )
                .fillna(0.0)
                .sort_values("period")
                .reset_index(drop=True)
            )

            # ------------------------------------------------------------------
            # Step 4 & 5 — Month-by-month percentage difference & flagging.
            # ------------------------------------------------------------------
            for _, row in merged.iterrows():
                period: str = str(row["period"])
                gst_sales: float = float(row["gst_sales"])
                bank_credits: float = float(row["bank_credits"])

                # Denominator: use GST sales as the reference baseline.
                # Clamp to 1.0 so we never divide by zero.
                denominator: float = max(gst_sales, 1.0)
                pct_diff: float = abs(gst_sales - bank_credits) / denominator

                if pct_diff > _MONTHLY_VARIANCE_THRESHOLD:
                    flags.append({
                        "flag": "Monthly GST-Bank Mismatch",
                        "severity": "MEDIUM",
                        "details": (
                            f"Period {period}: GST Sales \u20b9{gst_sales:,.0f} vs "
                            f"Bank Inflows \u20b9{bank_credits:,.0f} "
                            f"(diff {pct_diff:.1%})"
                        ),
                    })
                    logger.debug(
                        "[IntegrityVerificationAgent] Monthly mismatch %s: "
                        "gst=%.0f bank=%.0f diff=%.1f%%",
                        period, gst_sales, bank_credits, pct_diff * 100,
                    )

        except Exception as exc:
            logger.exception(
                "[IntegrityVerificationAgent] Monthly cross-validation failed."
            )
            warnings.append(
                f"Monthly GST-Bank cross-validation encountered an error: {exc}"
            )

        return flags, warnings

    @staticmethod
    def _normalise_period(series: pd.Series) -> pd.Series:
        """
        Convert a Series of date strings to 'YYYY-MM' period keys.

        Handles both:
        - GSTR period format: ``'2024-03'``  (already YYYY-MM)
        - Bank statement date format: ``'2024-03-15'`` or other parseable dates.

        Unparseable values are silently coerced to NaT so the caller can
        drop them with ``dropna()`` without crashing.

        Parameters
        ----------
        series : pd.Series
            Raw string column from the input DataFrame.

        Returns
        -------
        pd.Series
            String Series in 'YYYY-MM' format, with NaN for invalid entries.
        """
        # First attempt direct YYYY-MM match (no datetime parsing overhead).
        # This covers the GSTR native format and is O(n).
        yyyy_mm_mask = series.astype(str).str.match(r"^\d{4}-\d{2}$")

        if yyyy_mm_mask.all():
            # All values are already in the canonical format.
            return series.astype(str)

        # Fall back to pandas datetime parsing for full date strings.
        parsed = pd.to_datetime(series, errors="coerce")
        return parsed.dt.to_period("M").astype(str).where(parsed.notna(), other=pd.NA)